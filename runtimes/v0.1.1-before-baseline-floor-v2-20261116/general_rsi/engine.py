from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from statistics import mean

from . import prompts
from .backend import Backend, process_alive, run_process
from .protocol import (Halt, check_decision, feedback_from_process, number,
                       object_from_text, retention_gate)
from .storage import MemoryBank, clone_workspace, digest, lock, read_json, write_json

DEFAULT_LIMITS = {"max_rounds": 10, "max_agent_calls": 40, "max_verifier_calls": 40,
                  "max_seconds": None, "agent_timeout": None, "verifier_timeout": None,
                  "parallelism": 2, "verification_repeats": 2,
                  "max_tokens": None, "max_cost_usd": None}


def validate_limits(limits):
    for key, value in limits.items():
        if key not in DEFAULT_LIMITS:
            raise ValueError(f"Unknown limit: {key}")
        if value is None and key in {"max_tokens", "max_cost_usd", "max_seconds", "agent_timeout", "verifier_timeout"}:
            continue
        if not number(value) or value <= 0:
            raise ValueError(f"Limit {key} must be positive")
        if key not in {"max_seconds", "agent_timeout", "verifier_timeout", "max_cost_usd"} and not isinstance(value, int):
            raise ValueError(f"Limit {key} must be an integer")
    if limits["verification_repeats"] < 2:
        raise ValueError("At least two fresh validation runs are required")
    if limits["parallelism"] > 8:
        raise ValueError("First-version parallelism is bounded at eight workers")


def load_env_file(path):
    """Literal dotenv values only: never execute a shell or persist credentials."""
    if not path:
        return
    p = Path(path).expanduser()
    if not p.exists():
        return  # Pi may already have account credentials configured.
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key.replace("_", "a").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


class Controller:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.state = read_json(self.root / "state.json")
        if not self.state:
            raise ValueError("Not a goal directory")
        self.mutex = threading.RLock()
        self.cancelled = threading.Event()
        self.backend = Backend(self.state["backend"], self.cancelled)
        self.memory = MemoryBank(Path(self.state["memory_root"]))
        load_env_file(self.state["backend"].get("env_file"))

    @classmethod
    def create(cls, root, question, workspace, config, verifier=None, memory_root=None):
        root, workspace = Path(root).resolve(), Path(workspace).resolve()
        if root.exists():
            raise ValueError("Goal already exists; use resume or a new goal directory")
        if not workspace.is_dir() or root == workspace or workspace in root.parents:
            raise ValueError("Use an existing input workspace and a separate output directory")
        limits = {**DEFAULT_LIMITS, **config.get("limits", {})}
        validate_limits(limits)
        backend = config.get("backend", {"type": "pi", "provider": "zhizengzeng", "model": "deepseek-v4.1-flash"})
        encoded = json.dumps(backend).casefold()
        if '"api_key"' in encoded or "--api-key" in encoded:
            raise ValueError("Keep API credentials in the agent account or environment, not this config")
        root.mkdir(parents=True)
        try:
            clone_workspace(workspace, root / "input")
        except BaseException:
            # Leave no initialized goal with an incomplete snapshot.
            import shutil
            shutil.rmtree(root)
            raise
        initial_files = file_manifest(root / "input")
        memory = MemoryBank(Path(memory_root).expanduser().resolve() if memory_root else root.parent / "memory")
        recalled = memory.recall(question)
        guidance = "\n\n".join(x["experience"] for x in recalled)
        seed = {"id": "baseline", "experience": guidance, "scope": question,
                "parent_id": None, "origin": "retrieved_experience" if recalled else "empty",
                "recalled_refs": [x["id"] for x in recalled]}
        created = time.time()
        state = {"schema_version": 1, "question": question, "created_at": created,
                 "implementation_hashes": implementation_hashes(),
                 "deadline": created + limits["max_seconds"] if limits["max_seconds"] is not None else None, "status": "ready",
                 "source_workspace": str(workspace), "input_manifest": initial_files,
                 "input_digest": digest(initial_files), "backend": backend, "limits": limits,
                 "memory_root": str(memory.root), "verifier": verifier,
                 "recalled_negative_evidence": memory.recall_negative(question),
                 "current_experience": "baseline", "experiences": {"baseline": seed},
                 "baseline": [], "retained": {"baseline": []}, "rounds": [],
                 "research": [], "decision_serial": 0, "round_index": 0,
                 "agent_calls": 0, "verifier_calls": 0,
                 "settled_calls": [], "known_tokens": 0, "unknown_usage_calls": [],
                 "known_cost_usd": 0.0, "unknown_cost_calls": [], "reason": ""}
        write_json(root / "state.json", state)
        write_json(root / "feedback.json", [])
        return cls(root)

    def save(self):
        with self.mutex:
            write_json(self.root / "state.json", self.state)

    def event(self, kind, **details):
        with self.mutex:
            with (self.root / "events.jsonl").open("a") as stream:
                stream.write(json.dumps({"time": time.time(), "type": kind, **details}, ensure_ascii=False) + "\n")

    def budget(self, agent=False, verifier=False):
        s, limits = self.state, self.state["limits"]
        if self.cancelled.is_set():
            raise Halt("Controller interrupted", "interrupted")
        if s["deadline"] is not None and time.time() >= s["deadline"]:
            raise Halt("Goal wall-clock deadline reached", "budget_exhausted")
        if agent and s["agent_calls"] >= limits["max_agent_calls"]:
            raise Halt("Agent-call budget exhausted", "budget_exhausted")
        if verifier and s["verifier_calls"] >= limits["max_verifier_calls"]:
            raise Halt("Environment-verification budget exhausted", "budget_exhausted")
        if limits["max_tokens"] is not None:
            if s["unknown_usage_calls"]:
                raise Halt("Token cap cannot be evaluated: a call has unknown usage", "accounting_blocked")
            if s["known_tokens"] >= limits["max_tokens"]:
                raise Halt("Observed token budget reached", "budget_exhausted")
        if limits["max_cost_usd"] is not None:
            if s["unknown_cost_calls"]:
                raise Halt("Money cap cannot be evaluated: a call has unknown cost", "accounting_blocked")
            if s["known_cost_usd"] >= limits["max_cost_usd"]:
                raise Halt("Observed model-cost budget reached", "budget_exhausted")

    def settle(self, identity, result):
        with self.mutex:
            if identity in self.state["settled_calls"]:
                return
            usage = result.get("usage")
            tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
            if number(tokens) and tokens >= 0 and usage.get("accounting_valid", True):
                self.state["known_tokens"] += tokens
            else:
                self.state["unknown_usage_calls"].append(identity)
            cost = usage.get("cost_usd") if isinstance(usage, dict) else None
            if number(cost) and cost >= 0:
                self.state["known_cost_usd"] += cost
            else:
                self.state["unknown_cost_calls"].append(identity)
            self.state["settled_calls"].append(identity)
            self.save()

    def call(self, identity, role, payload, cwd, instructions):
        folder = self.root / "calls" / identity
        cached = read_json(folder / "result.json")
        if cached is not None:
            self.settle(identity, cached)
            return cached
        if (folder / "started.json").exists():
            record = read_json(folder / "process.json", {})
            condition = "still alive" if process_alive(record) else "has unknown completion"
            raise Halt(f"Call {identity} {condition}; inspect it before explicitly abandoning/retrying", "needs_reconciliation")
        with self.mutex:
            self.budget(agent=True)
            self.state["agent_calls"] += 1
            write_json(folder / "started.json", {"role": role, "started_at": time.time()})
            self.save()
        request = {"schema_version": 1, "role": role, "instructions": instructions,
                   "input": payload, "call_id": identity}
        write_json(folder / "request.json", request)
        self.event("agent_started", call_id=identity, role=role)
        try:
            result = self.backend.call(request, cwd, folder, self.state["limits"]["agent_timeout"], self.state["deadline"])
        except Exception as exc:
            result = {"ok": False, "text": "", "usage": None,
                      "error": f"{type(exc).__name__}: {exc}"}
        write_json(folder / "result.json", result)
        self.settle(identity, result)
        self.event("agent_finished", call_id=identity, role=role, ok=result["ok"])
        return result

    def checked_json_call(self, identity, role, payload, cwd, instructions):
        revision = self.state.get("protocol_revision", 0)
        if revision:
            identity += f"-protocol-{revision}"
        repair_suffix = "-json-repair"
        for attempt in range(2):
            key = identity if not attempt else identity + repair_suffix
            result = self.call(key, role, payload, cwd, instructions)
            if not result["ok"]:
                diagnostic = result.get("diagnostic") or {}
                if diagnostic.get("code") == "output_limit" and not attempt:
                    repair_suffix = "-length-repair"
                    payload = {**payload, "previous_failure": diagnostic["message"]}
                    instructions += ("\nThe previous call was truncated by the per-response output token limit. "
                                     "Inspect existing workspace files first; preserve completed work. "
                                     "Use small tool calls to write any required verifier in sections, "
                                     "then return only the concise required JSON. Do not repeat environment exploration.")
                    self.event("agent_output_repair", call_id=key, role=role)
                    continue
                reason = diagnostic.get("message") or "Agent 未返回有效结果"
                raise Halt(f"{role}: {reason}；详见 calls/{key}/", "execution_error")
            try:
                return object_from_text(result["text"])
            except (ValueError, TypeError) as exc:
                payload = {**payload, "protocol_error": str(exc), "previous_output": result["text"][-6000:]}
                instructions += "\nYour previous response was not a JSON object. Return only the required JSON."
        raise Halt(f"{role} did not produce valid JSON after a bounded repair", "protocol_error")

    def ensure_workspace(self, identity):
        path = self.root / "workspaces" / identity
        if not path.exists():
            clone_workspace(self.root / "input", path)
        return path

    def bootstrap(self):
        if self.state.get("bootstrapped"):
            return
        workspace = self.ensure_workspace("bootstrap")
        result = self.checked_json_call("bootstrap", "bootstrap", {
            "goal": self.state["question"], "external_verifier": self.state["verifier"],
            "python_executable": sys.executable,
            "recalled_experiences": self.state["experiences"]["baseline"]["experience"],
            "available_capabilities": ["research", "parallel_experiment", "environment_verifier"],
        }, workspace, prompts.BOOTSTRAP)
        if result.get("blocked"):
            raise Halt(str(result["blocked"]))
        acceptance = result.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance or not all(isinstance(x, str) and x.strip() for x in acceptance):
            raise Halt("Bootstrap needs concrete acceptance requirements", "protocol_error")
        if self.state["verifier"] is None:
            relative = result.get("verifier_file")
            if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise Halt("Invalid verifier artifact path", "protocol_error")
            source = workspace / relative
            checks = result.get("check_ids")
            if not source.is_file() or source.is_symlink() or not isinstance(checks, list) or not checks:
                raise Halt("A real verifier file and nonempty check_ids are required", "protocol_error")
            if any(not isinstance(x, str) or not x for x in checks) or len(set(checks)) != len(checks):
                raise Halt("Verifier check ids must be unique strings", "protocol_error")
            target = self.root / "locked" / "verifier.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            self.state["verifier"] = {"command": [sys.executable, "{verifier}", "{workspace}"],
                                      "mode": "json", "check_ids": checks,
                                      "authority": "agent_proposed", "file": str(target),
                                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        self.validate_verifier_spec()
        self.seal_verifier_files()
        self.state["acceptance"] = acceptance
        self.state["initial_research_question"] = result.get("initial_research_question", "")
        self.state["verifier_seal"] = digest(self.state["verifier"])
        self.state["bootstrapped"] = True
        self.save()
        self.event("acceptance_sealed", authority=self.state["verifier"]["authority"])

    def validate_verifier_spec(self):
        spec = self.state["verifier"]
        if not isinstance(spec, dict) or spec.get("mode") not in {"json", "exit_code"}:
            raise Halt("Verifier mode must be json or exit_code", "protocol_error")
        command = spec.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise Halt("Verifier command must be an argv array", "protocol_error")
        if spec["mode"] == "json" and not spec.get("check_ids"):
            raise Halt("JSON verifier needs sealed check_ids", "protocol_error")
        spec.setdefault("authority", "user_provided")

    def seal_verifier_files(self):
        """Protect explicit test scripts/directories from candidate edits.

        The contract is an integrity boundary, not a hostile-code OS sandbox.
        """
        spec, source = self.state["verifier"], self.root / "input"
        protected, external = {}, {}
        for argument in spec["command"]:
            if argument == "{workspace}":
                continue
            expanded = argument.replace("{workspace}", str(source)).replace("{verifier}", spec.get("file", ""))
            p = Path(expanded)
            if not p.is_absolute():
                p = source / p
            if not p.exists():
                continue
            if p.resolve() == source:
                continue
            files = list(p.rglob("*")) if p.is_dir() else [p]
            for file in files:
                if not file.is_file():
                    continue
                resolved = file.resolve()
                value = hashlib.sha256(resolved.read_bytes()).hexdigest()
                if source in resolved.parents:
                    protected[str(resolved.relative_to(source))] = value
                else:
                    external[str(resolved)] = value
        # Conventional test discovery has no explicit path argument.
        if any("pytest" in x for x in spec["command"]):
            for file in source.rglob("*.py"):
                if "tests" in file.relative_to(source).parts or file.name.startswith("test_"):
                    protected[str(file.relative_to(source))] = hashlib.sha256(file.read_bytes()).hexdigest()
        spec["protected_workspace_files"] = protected
        spec["protected_external_files"] = external

    def workspace_acceptance_unchanged(self, workspace):
        for relative, expected in self.state["verifier"].get("protected_workspace_files", {}).items():
            p = workspace / relative
            if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                return False
        return True

    def verify_integrity(self):
        if self.state.get("implementation_hashes") and implementation_hashes() != self.state["implementation_hashes"]:
            raise Halt("Controller code changed since this goal started; use the pinned version or create a new goal", "integrity_error")
        if digest(file_manifest(self.root / "input")) != self.state["input_digest"]:
            raise Halt("Immutable task input changed", "integrity_error")
        if self.state.get("bootstrapped"):
            spec = self.state["verifier"]
            if digest(spec) != self.state["verifier_seal"]:
                raise Halt("Acceptance/verifier specification changed", "integrity_error")
            if spec.get("file") and hashlib.sha256(Path(spec["file"]).read_bytes()).hexdigest() != spec["sha256"]:
                raise Halt("Sealed acceptance verifier was modified", "integrity_error")
            for name, expected in spec.get("protected_external_files", {}).items():
                p = Path(name)
                if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                    raise Halt("External acceptance program changed", "integrity_error")

    def research(self, identity, question):
        old = next((x for x in self.state["research"] if x["id"] == identity), None)
        if old:
            return old
        payload = {"goal": self.state["question"], "question": question,
                   "known_findings": self.state["research"][-4:], "acceptance": self.state["acceptance"]}
        result = self.call(identity, "research", payload, self.ensure_workspace(identity), prompts.RESEARCH)
        if not result["ok"]:
            row = {"id": identity, "question": question, "findings": "Research execution failed; see call logs", "failed": True}
        else:
            row = {"id": identity, "question": question, "findings": result["text"][:24000], "failed": False}
        self.state["research"].append(row)
        self.save()
        self.event("research_recorded", research_id=identity, failed=row["failed"])
        return row

    def attempt(self, identity, experience, hypothesis, stage):
        path = self.root / "attempts" / f"{identity}.json"
        cached = read_json(path)
        if cached:
            return cached
        self.verify_integrity()
        workspace = self.ensure_workspace(identity)
        payload = {"goal": self.state["question"], "acceptance": self.state["acceptance"],
                   "hypothesis": hypothesis, "experience": experience["experience"],
                   "experience_id": experience["id"], "experience_sha256": digest(experience["experience"]),
                   "research": self.state["research"][-4:], "stage": stage,
                   "workspace": str(workspace)}
        result = self.call(identity, "worker", payload, workspace, prompts.WORKER)
        self.verify_integrity()
        if result["ok"]:
            feedback = self.evaluate(identity, workspace)
        else:
            feedback = {"valid": False, "passed": False, "score": 0.0,
                        "checks": [], "diagnostic": "Agent execution failed; inspect call logs"}
        row = {"id": identity, "experience_id": experience["id"],
               "experience_sha256": digest(experience["experience"]), "stage": stage,
               "workspace": str(workspace), "feedback": feedback,
               "answer": result.get("text", ""), "usage": result.get("usage")}
        write_json(path, row)
        self.event("environment_result", attempt_id=identity, stage=stage,
                   experience_id=experience["id"], valid=feedback["valid"],
                   passed=feedback["passed"], score=feedback["score"])
        return row

    def existing_baseline(self):
        """An explicitly requested artifact baseline, never labeled an LLM baseline."""
        path = self.root / "attempts/baseline.json"
        cached = read_json(path)
        if cached:
            return cached
        workspace = self.ensure_workspace("baseline")
        feedback = self.evaluate("baseline", workspace)
        row = {"id": "baseline", "experience_id": "baseline", "experience_sha256": digest(""),
               "stage": "existing_artifact_baseline", "workspace": str(workspace),
               "feedback": feedback, "answer": "Existing input artifact evaluated without agent edits.", "usage": None}
        write_json(path, row)
        self.event("environment_result", attempt_id="baseline", stage=row["stage"],
                   valid=feedback["valid"], passed=feedback["passed"], score=feedback["score"])
        return row

    def evaluate(self, identity, workspace):
        folder = self.root / "verifications" / identity
        cached = read_json(folder / "feedback.json")
        if cached:
            return cached
        if not self.workspace_acceptance_unchanged(workspace):
            feedback = {"valid": False, "passed": False, "score": 0.0, "checks": [],
                        "diagnostic": "Candidate modified or removed a sealed acceptance/test file"}
            write_json(folder / "feedback.json", feedback)
            return feedback
        if (folder / "started.json").exists() and not (folder / "process-result.json").exists():
            raise Halt(f"Verifier {identity} has unknown completion", "needs_reconciliation")
        spec = self.state["verifier"]
        argv = [x.replace("{workspace}", str(workspace)).replace("{verifier}", spec.get("file", ""))
                .replace("{python}", sys.executable) for x in spec["command"]]
        result = read_json(folder / "process-result.json")
        if result is None:
            with self.mutex:
                self.budget(verifier=True)
                self.state["verifier_calls"] += 1
                self.save()
                write_json(folder / "started.json", {"time": time.time(), "verifier_seal": self.state["verifier_seal"]})
            result = run_process(argv, workspace, folder, "", self.state["limits"]["verifier_timeout"],
                                 self.cancelled, self.state["deadline"])
        self.verify_integrity()
        feedback = feedback_from_process(result, spec)
        feedback["verifier_seal"] = self.state["verifier_seal"]
        write_json(folder / "feedback.json", feedback)
        return feedback

    def validation(self, key, experience):
        rows = []
        for i in range(self.state["limits"]["verification_repeats"]):
            rows.append(self.attempt(f"{key}-validation-{i}", experience,
                                     "Independently rerun the fixed experience from clean input; do not revise it.", "validation"))
        return rows

    def all_attempts(self):
        return [read_json(p) for p in sorted((self.root / "attempts").glob("*.json"))]

    def context(self):
        rows = self.all_attempts()
        # Full logs stay inspectable on disk; planner context remains bounded.
        summary = [{k: row[k] for k in ("id", "experience_id", "stage", "feedback", "usage")} for row in rows[-18:]]
        return {"goal": self.state["question"], "acceptance": self.state.get("acceptance", []),
                "retained_parents": self.state["experiences"],
                "controller_feedback": self.state.get("controller_feedback", ""),
                "prior_negative_evidence": self.state.get("recalled_negative_evidence", []),
                "current_experience": self.state["current_experience"],
                "experiments": summary, "research": self.state["research"][-5:],
                "human_feedback": read_json(self.root / "feedback.json", [])[-8:],
                "rejected_revisions": [x for r in self.state["rounds"] for x in r.get("evaluated", [])][-8:],
                "parallelism": self.state["limits"]["parallelism"],
                "remaining_agent_calls": self.state["limits"]["max_agent_calls"] - self.state["agent_calls"],
                "remaining_seconds": (max(0, self.state["deadline"] - time.time())
                                      if self.state["deadline"] is not None else None),
                "evidence_root": str(self.root)}

    def run(self):
        with lock(self.root / ".controller.lock"):
            if self.state["status"] == "solved":
                return self.report()
            self.state.update(status="running", reason="")
            self.save()
            try:
                self.verify_integrity()
                self.bootstrap()
                if self.state.get("initial_research_question") and not self.state.get("initial_research_done"):
                    self.research("research-initial", self.state["initial_research_question"])
                    self.state["initial_research_done"] = True
                    self.save()
                if not self.state["baseline"]:
                    seed = self.state["experiences"]["baseline"]
                    baseline = (self.existing_baseline() if self.state.get("baseline_existing") else
                                self.attempt("baseline", seed, "Solve using the original task and any applicable recalled guidance.", "baseline"))
                    self.state["baseline"] = [baseline]
                    self.state["retained"]["baseline"] = [baseline]
                    self.save()
                baseline = self.state["baseline"][0]
                if baseline["feedback"]["valid"] and baseline["feedback"]["passed"]:
                    confirmations = self.validation("baseline", self.state["experiences"]["baseline"])
                    if all(x["feedback"]["valid"] and x["feedback"]["passed"] for x in confirmations):
                        self.finish(confirmations[-1], "baseline", "Initial method passed independent repeated verification")
                        return self.report()
                while self.state["round_index"] < self.state["limits"]["max_rounds"]:
                    self.budget()
                    self.round()
                    if self.state["status"] == "solved":
                        break
                if self.state["status"] == "running":
                    raise Halt("Revision-round budget exhausted", "budget_exhausted")
            except Halt as exc:
                self.state.update(status=exc.status, reason=str(exc))
            except KeyboardInterrupt:
                self.cancelled.set()
                self.state.update(status="interrupted", reason="Interrupted by operator; completed calls remain reusable")
            except Exception as exc:
                self.state.update(status="execution_error", reason=f"{type(exc).__name__}: {exc}")
            finally:
                self.save()
                self.report()
            return self.report()

    def round(self):
        index = self.state["round_index"]
        plan_path = self.root / "plans" / f"round-{index}.json"
        decision = read_json(plan_path)
        while decision is None:
            serial = self.state["decision_serial"]
            context = self.context()
            raw = self.checked_json_call(f"decision-{serial}", "planner", context,
                                         self.ensure_workspace("planner"), prompts.DECIDE)
            evidence = {x["id"] for x in self.all_attempts()} | {x["id"] for x in self.state["research"]}
            seen = {x["experience"] for x in self.state["experiences"].values()}
            for r in self.state["rounds"]:
                seen.update(x["experience"] for x in r.get("candidates", []))
            try:
                decision = check_decision(raw, set(self.state["retained"]), evidence,
                                          self.state["limits"]["parallelism"], seen)
            except ValueError as exc:
                corrected = self.checked_json_call(f"decision-{serial}-semantic-repair", "planner",
                    {**context, "rejected_decision": raw, "protocol_error": str(exc)},
                    self.ensure_workspace("planner"), prompts.DECIDE + "\nFix the rejected decision while keeping evidence and acceptance unchanged.")
                try:
                    decision = check_decision(corrected, set(self.state["retained"]), evidence,
                                              self.state["limits"]["parallelism"], seen)
                except ValueError as second:
                    raise Halt(f"Planner decision rejected after repair: {second}", "protocol_error") from None
            if decision["action"] == "research":
                self.research(f"research-{serial}", decision["question"])
                self.state["decision_serial"] += 1
                self.save()
                decision = None
                continue
            if decision["action"] == "blocked":
                raise Halt(decision["reason"])
            if decision["action"] == "finish":
                self.state["controller_feedback"] = ("Completion was rejected because fresh environmental verification has not passed. "
                                                      "Use observed failures to propose an experiment or targeted research.")
                self.state["decision_serial"] += 1
                self.save()
                self.event("unverified_completion_rejected", reason=decision["reason"])
                decision = None
                continue
            write_json(plan_path, decision)
            self.state["decision_serial"] += 1
            self.save()
        candidates = []
        for i, proposal in enumerate(decision["candidates"]):
            identity = f"round-{index}-candidate-{i}"
            candidates.append({**proposal, "id": identity, "experience": proposal["experience"].strip()})
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.state["limits"]["parallelism"]) as pool:
            futures = [pool.submit(self.attempt, c["id"], c, c["hypothesis"], "exploration") for c in candidates]
            observations, exceptions = [], []
            try:
                for future in futures:
                    try:
                        observations.append(future.result())
                    except Exception as exc:
                        exceptions.append(exc)
            except BaseException:
                self.cancelled.set()
                for future in futures:
                    future.cancel()
                raise
            if exceptions:
                raise exceptions[0]
        ranked = sorted(zip(candidates, observations), key=lambda pair: (
            pair[1]["feedback"]["valid"], pair[1]["feedback"]["score"],
            -((pair[1].get("usage") or {}).get("total_tokens") or float("inf"))), reverse=True)
        evaluated, winner = [], None
        # Check each promising branch at most once. Do not revalidate a rejected version until lucky.
        for candidate, observed in ranked:
            parent_rows = self.state["retained"][candidate["parent_id"]]
            old = mean(x["feedback"]["score"] if x["feedback"]["valid"] else 0 for x in parent_rows)
            if not observed["feedback"]["valid"] or observed["feedback"]["score"] < old:
                evaluated.append({"candidate_id": candidate["id"], "accepted": False,
                                  "reason": "invalid_or_regressed_exploration"})
                continue
            validation = self.validation(candidate["id"], candidate)
            gate = retention_gate(validation, parent_rows)
            evaluated.append({"candidate_id": candidate["id"], **gate,
                              "validation_refs": [x["id"] for x in validation]})
            if not gate["accepted"]:
                continue
            self.state["experiences"][candidate["id"]] = candidate
            self.state["retained"][candidate["id"]] = validation
            current_rows = self.state["retained"][self.state["current_experience"]]
            current_score = mean(x["feedback"]["score"] if x["feedback"]["valid"] else 0 for x in current_rows)
            if gate["validation_mean_score"] >= current_score:
                self.state["current_experience"] = candidate["id"]
            self.memory.deposit({**candidate, "id": digest({"goal": str(self.root), "experience": candidate["experience"]}),
                "scope": candidate["scope"] + " " + self.state["question"],
                "status": "validated", "source_goal": str(self.root), "gate": gate,
                "validation_refs": [str(self.root / "attempts" / (x["id"] + ".json")) for x in validation],
                "verifier_authority": self.state["verifier"]["authority"],
                "limitation": "Validated on this goal and these checks; cross-goal benefit remains to be measured."})
            self.event("experience_retained", candidate_id=candidate["id"], parent_id=candidate["parent_id"], gate=gate)
            if all(x["feedback"]["passed"] for x in validation):
                winner = (candidate, validation[-1])
                break
        retained_ids = set(self.state["retained"])
        for candidate, observed in zip(candidates, observations):
            if candidate["id"] in retained_ids:
                continue
            verdict = next((x for x in evaluated if x["candidate_id"] == candidate["id"]), None)
            if verdict is not None:
                self.memory.reject({"id": digest({"goal": str(self.root), "candidate": candidate["id"]}),
                    "scope": candidate["scope"] + " " + self.state["question"],
                    "experience": candidate["experience"], "source_goal": str(self.root),
                    "status": "negative_evidence_not_guidance", "feedback": observed["feedback"],
                    "gate": verdict, "limitation": "Failure in these conditions; do not assume universal failure."})
        self.state["rounds"].append({"index": index, "decision": decision,
                                     "candidates": candidates, "evaluated": evaluated})
        self.state["round_index"] += 1
        self.save()
        if winner:
            self.finish(winner[1], winner[0]["id"], "Revised experience passed fresh repeated environmental verification")

    def finish(self, row, experience_id, reason):
        self.verify_integrity()
        if not row["feedback"]["valid"] or not row["feedback"]["passed"]:
            raise Halt("Cannot complete from an invalid/nonpassing environment result", "unverified")
        self.state.update(status="solved", reason=reason, answer_attempt=row["id"],
                          final_experience=experience_id, completed_at=time.time())
        (self.root / "answer.md").write_text(row["answer"] + "\n")
        self.save()
        self.event("goal_verified", attempt_id=row["id"], experience_id=experience_id,
                   verifier_authority=self.state["verifier"]["authority"])

    def report(self):
        s = self.state
        report = {k: s.get(k) for k in ["question", "status", "reason", "agent_calls", "verifier_calls",
                  "round_index", "known_tokens", "unknown_usage_calls", "known_cost_usd",
                  "unknown_cost_calls", "current_experience", "answer_attempt", "final_experience"]}
        report.update(goal_directory=str(self.root), verifier_authority=(s.get("verifier") or {}).get("authority"),
            total_model_cost_usd=None if s["unknown_cost_calls"] else s["known_cost_usd"],
            baseline_origin="existing_artifact_no_agent" if s.get("baseline_existing") else "agent_execution",
            acceptance=s.get("acceptance"), final_answer=str(self.root / "answer.md") if s.get("answer_attempt") else None,
            validated_experience_count=max(0, len(s["retained"]) - 1),
            assurance="Verified under the sealed checks; agent-proposed checks are not a guarantee of complete objective coverage.",
            cost_scope="All controller-visible agent roles, including research and validation; external tool/compute bills are not included unless a bridge reports them.",
            budget_scope="Call-count budgets are enforced. Time limits are opt-in, absent by default. Token/money caps stop new calls after observed usage; in-flight calls can overshoot.")
        write_json(self.root / "report.json", report)
        return report


def file_manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def implementation_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))}
