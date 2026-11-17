from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .protocol import Halt, object_from_text
from .storage import read_json, write_json


def pi_executable(config):
    if config.get("executable"):
        return str(Path(config["executable"]).expanduser())
    found = shutil.which("pi")
    if found:
        return found
    for candidate in (Path.home() / ".local/bin/pi", Path.home() / ".npm-global/bin/pi"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "pi"


def process_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def process_alive(record):
    return (record.get("pid") and record.get("start_ticks")
            and process_start(record["pid"]) == record["start_ticks"])


def run_process(argv, cwd, folder, stdin_text, timeout, cancelled=None, deadline=None):
    folder.mkdir(parents=True, exist_ok=True)
    input_path = folder / "stdin.txt"
    input_path.write_text(stdin_text)
    out, err = folder / "stdout.log", folder / "stderr.log"
    started = time.time()
    timed_out, interrupted = False, False
    proc = None
    try:
        with input_path.open() as inp, out.open("wb") as stdout, err.open("wb") as stderr:
            proc = subprocess.Popen(argv, cwd=cwd, stdin=inp, stdout=stdout, stderr=stderr,
                                    start_new_session=True)
            write_json(folder / "process.json", {"pid": proc.pid, "start_ticks": process_start(proc.pid),
                       "started_at": started, "state": "running"})
            limit = min(started + timeout if timeout is not None else float("inf"),
                        deadline if deadline is not None else float("inf"))
            while proc.poll() is None:
                if time.time() >= limit:
                    timed_out = True
                    break
                if cancelled is not None and cancelled.is_set():
                    interrupted = True
                    break
                time.sleep(0.05)
            if timed_out or interrupted:
                _terminate(proc)
            code = proc.wait()
    except BaseException:
        if proc is not None:
            _terminate(proc)
        raise
    finally:
        if proc is not None:
            write_json(folder / "process.json", {"pid": proc.pid, "start_ticks": None,
                       "started_at": started, "finished_at": time.time(), "state": "finished"})
    stdout_raw, stderr_raw = out.read_bytes(), err.read_bytes()
    result = {"returncode": code, "timed_out": timed_out, "interrupted": interrupted,
              "elapsed_seconds": time.time() - started,
              "stdout_sha256": hashlib.sha256(stdout_raw).hexdigest(),
              "stderr_sha256": hashlib.sha256(stderr_raw).hexdigest(),
              "stdout_tail": stdout_raw[-524288:].decode(errors="replace"),
              "stderr_tail": stderr_raw[-12000:].decode(errors="replace")}
    write_json(folder / "process-result.json", result)
    return result


def _terminate(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content
                         if isinstance(x, dict) and x.get("type") == "text")
    return ""


def pi_session_usage(path):
    """Same component accounting as the frozen benchmark, including compaction."""
    keys = {"input": "input_tokens", "output": "output_tokens", "cacheRead": "cache_read_tokens",
            "cacheWrite": "cache_write_tokens"}
    totals = {k: 0 for k in keys.values()}
    seen, costs, errors = {}, [], []
    requests = 0
    if not path.exists():
        return None
    for line in path.read_text(errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            errors.append("malformed_line")
            continue
        message = item.get("message", {})
        usage = None
        if item.get("type") == "message" and message.get("role") == "assistant":
            usage = message.get("usage")
        elif item.get("type") in {"compaction", "branch_summary"}:
            usage = item.get("usage")
        if not isinstance(usage, dict):
            continue
        identity = item.get("id")
        if not identity:
            errors.append("missing_entry_id")
            continue
        if identity in seen:
            if seen[identity] != line:
                errors.append("conflicting_duplicate")
            continue
        seen[identity] = line
        values = [usage.get(k, 0) for k in keys]
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in values):
            errors.append("invalid_components")
            continue
        if usage.get("totalTokens", sum(values)) != sum(values):
            errors.append("component_sum_mismatch")
        for key, value in zip(keys.values(), values):
            totals[key] += value
        cost = (usage.get("cost") or {}).get("total")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            costs.append(cost)
        requests += 1
    if not requests:
        return None
    return {**totals, "total_tokens": sum(totals.values()), "requests": requests,
            "cost_usd": sum(costs) if len(costs) == requests and sum(costs) > 0 else None,
            "accounting_valid": not errors, "accounting_errors": sorted(set(errors)),
            "cost_note": "Zero/unconfigured Pi price fields are unknown monetary cost, not free usage."}


class Backend:
    def __init__(self, config, cancelled=None):
        self.config = config
        self.cancelled = cancelled or threading.Event()

    def call(self, request, cwd, folder, timeout, deadline):
        role = request["role"]
        override = self.config.get("role_commands", {}).get(role)
        kind = "command" if override else self.config.get("type", "pi")
        if kind == "command":
            argv = override or self.config.get("command")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                raise ValueError("Command backend needs a nonempty argv array")
            request_path = folder / "request.json"
            write_json(request_path, request)
            argv = [x.replace("{request}", str(request_path)) for x in argv]
            result = run_process(argv, cwd, folder, json.dumps(request, ensure_ascii=False),
                                 timeout, self.cancelled, deadline)
            if result["returncode"] != 0 or result["timed_out"] or result["interrupted"]:
                return {"ok": False, "text": "", "usage": None, "process": result}
            envelope = object_from_text(result["stdout_tail"])
            if not isinstance(envelope.get("text"), str):
                raise ValueError("Backend response requires a text string")
            return {"ok": True, "text": envelope["text"], "usage": envelope.get("usage"),
                    "process": result}
        if kind != "pi":
            raise ValueError("Supported backends: pi, command")
        session = folder / "session.jsonl"
        argv = [pi_executable(self.config), "--print", "--mode", "json", "--session", str(session)]
        for key in ("provider", "model", "thinking"):
            if self.config.get(key):
                argv.extend(["--" + key, self.config[key]])
        argv += self.config.get("args", [])
        prompt = request["instructions"] + "\n\nINPUT JSON\n" + json.dumps(request["input"], ensure_ascii=False)
        result = run_process(argv, cwd, folder, prompt, timeout, self.cancelled, deadline)
        latest = ""
        # Read full stream, not its tail; the final answer may precede later tool events.
        for line in (folder / "stdout.log").read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "message_end":
                message = event.get("message", {})
                if message.get("role") == "assistant":
                    latest = text_content(message.get("content")) or latest
            elif event.get("type") == "agent_end":
                for message in event.get("messages", []):
                    if message.get("role") == "assistant":
                        latest = text_content(message.get("content")) or latest
        usage = pi_session_usage(session)
        return {"ok": result["returncode"] == 0 and not result["timed_out"]
                and not result["interrupted"] and bool(latest),
                "text": latest, "usage": usage, "process": result}
