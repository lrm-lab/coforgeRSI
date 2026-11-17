from __future__ import annotations

import json
import math
from statistics import mean


class Halt(RuntimeError):
    def __init__(self, reason, status="blocked"):
        super().__init__(reason)
        self.status = status


def object_from_text(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_decision(data, parents, evidence, parallelism, seen_guidance):
    action = data.get("action")
    if action not in {"research", "experiment", "blocked", "finish"}:
        raise ValueError("Unknown planner action")
    if not isinstance(data.get("reason"), str) or not data["reason"].strip():
        raise ValueError("A concrete decision reason is required")
    if action == "research":
        if not isinstance(data.get("question"), str) or not data["question"].strip():
            raise ValueError("Research must name a specific knowledge/evidence gap")
    if action != "experiment":
        return data
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= parallelism:
        raise ValueError("Invalid candidate count")
    new_guidance = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("parent_id") not in parents:
            raise ValueError("Candidate parent must be a retained experience version")
        for key in ("experience", "hypothesis", "observation", "scope", "expected_effect"):
            if not isinstance(candidate.get(key), str) or not candidate[key].strip():
                raise ValueError(f"Candidate {key} is required")
        text = candidate["experience"].strip()
        if len(text) > 6000 or text in seen_guidance or text in new_guidance:
            raise ValueError("Guidance is oversized or repeats an already tested version")
        refs = candidate.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(x not in evidence for x in refs):
            raise ValueError("Revision must cite existing experiment or research evidence")
        new_guidance.add(text)
    return data


def feedback_from_process(result, specification):
    """Only a process actually run by the controller produces environmental feedback."""
    base = {"valid": False, "passed": False, "score": 0.0,
            "exit_code": result["returncode"], "timed_out": result["timed_out"],
            "elapsed_seconds": result["elapsed_seconds"],
            "output_sha256": result["stdout_sha256"], "checks": [],
            "diagnostic": result["stdout_tail"] + "\n" + result["stderr_tail"]}
    if result["timed_out"]:
        return base
    if specification["mode"] == "exit_code":
        passed = result["returncode"] == 0
        return {**base, "valid": True, "passed": passed, "score": float(passed),
                "checks": [{"id": "command_exit", "passed": passed}]}
    if result["returncode"] != 0:
        return base
    try:
        value = object_from_text(result["stdout_tail"])
        score, checks = value["score"], value["checks"]
        if not number(score) or not 0 <= score <= 1:
            raise ValueError("score must be finite and normalized to [0,1]")
        if not isinstance(checks, list) or not checks:
            raise ValueError("A nonempty check list is required")
        ids = []
        for item in checks:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("Each check needs an id")
            if type(item.get("passed")) is not bool or not isinstance(item.get("evidence"), str) or not item["evidence"].strip():
                raise ValueError("Each check needs a boolean verdict and observed evidence")
            ids.append(item["id"])
        if len(set(ids)) != len(ids) or set(ids) != set(specification["check_ids"]):
            raise ValueError("Verifier must retain exactly the sealed acceptance checks")
        constraints = value.get("hard_constraints", {})
        if not isinstance(constraints, dict) or any(type(x) is not bool for x in constraints.values()):
            raise ValueError("Invalid hard constraints")
        passed = value.get("passed")
        if type(passed) is not bool or passed != all(x["passed"] for x in checks):
            raise ValueError("Overall verdict contradicts requirement checks")
        safe = all(constraints.values())
        return {**base, "valid": safe, "passed": passed and safe, "score": score,
                "checks": checks, "hard_constraints": constraints,
                "metrics": value.get("metrics", {}), "diagnostic": value.get("feedback", "")}
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        base["diagnostic"] = "Invalid verifier output: " + str(exc) + "\n" + base["diagnostic"]
        return base


def retention_gate(validation, reference):
    """Adapt the benchmark loop's fresh-repeat, validity and non-regression gates.

    No confidence interval or cross-task generalization is claimed from two runs.
    Failed parent execution contributes zero; it is never rewritten on disk.
    """
    reasons = []
    if len(validation) < 2 or len({x["id"] for x in validation}) != len(validation):
        reasons.append("two_distinct_fresh_validation_runs_required")
    if any(not x["feedback"]["valid"] for x in validation):
        reasons.append("invalid_validation_execution")
    old = mean(x["feedback"]["score"] if x["feedback"]["valid"] else 0 for x in reference)
    scores = [x["feedback"]["score"] if x["feedback"]["valid"] else 0 for x in validation]
    new = mean(scores) if scores else 0
    if scores and min(scores) < old - 1e-9:
        reasons.append("validation_regressed_below_parent")
    quality_gain = bool(scores) and new > old + 1e-9
    if quality_gain and sum(x > old + 1e-9 for x in scores) < 2:
        reasons.append("quality_gain_not_replicated_twice")
    def tokens(rows):
        if any((x.get("usage") or {}).get("accounting_valid") is False for x in rows):
            return None
        values = [(x.get("usage") or {}).get("total_tokens") for x in rows]
        return mean(values) if values and all(number(v) and v > 0 for v in values) else None
    old_tokens, new_tokens = tokens(reference), tokens(validation)
    efficiency_gain = (old_tokens is not None and new_tokens is not None
                       and new_tokens < old_tokens and new >= old - 1e-9)
    if not quality_gain and not efficiency_gain:
        reasons.append("no_verified_quality_or_token_gain")
    return {"accepted": not reasons, "reasons": reasons, "parent_mean_score": old,
            "validation_mean_score": new, "quality_gain": quality_gain,
            "token_gain": efficiency_gain, "parent_mean_tokens": old_tokens,
            "validation_mean_tokens": new_tokens}
