from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .engine import Controller, validate_limits
from .backend import pi_executable, process_alive
from .storage import append_feedback, lock, read_json, write_json

PROJECT = Path(__file__).resolve().parents[1]


def config_at(path):
    config = json.loads(Path(path).expanduser().read_text()) if path else {
        "backend": {"type": "pi", "provider": "zhizengzeng", "model": "deepseek-v4.1-flash",
                    "env_file": "~/.pi/agent/zhizengzeng.env", "args": ["--offline"]}}
    def expand(value):
        if isinstance(value, str):
            return value.replace("{project}", str(PROJECT)).replace("{python}", sys.executable)
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(x) for x in value]
        return value
    return expand(config)


def parser():
    p = argparse.ArgumentParser(description="Environment-grounded goal loops with validated experience evolution")
    commands = p.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="Start an arbitrary goal using the configured agent framework")
    ask.add_argument("question")
    ask.add_argument("--workspace", help="Optional input workspace; omitted means a fresh empty workspace")
    ask.add_argument("--output", help="New goal directory, outside the input workspace")
    ask.add_argument("--config", help="JSON config; default is existing Pi zhizengzeng/deepseek-v4.1-flash")
    ask.add_argument("--memory", help="Persistent experience directory; default outputs parent/memory")
    ask.add_argument("--verify-command", help="JSON argv array, e.g. '[\"python3\",\"check.py\"]'")
    ask.add_argument("--verify-mode", choices=["exit_code", "json"], default="exit_code")
    ask.add_argument("--check-ids", help="Comma-separated sealed ids for JSON verifier")
    ask.add_argument("--baseline-existing", action="store_true", help="Evaluate the supplied artifact before agent edits; not an agent baseline comparison")
    resume = commands.add_parser("resume", help="Continue from persisted results without replaying completed calls")
    resume.add_argument("goal")
    resume.add_argument("--extra-agent-calls", type=int, default=0)
    resume.add_argument("--extra-seconds", type=int, default=0)
    status = commands.add_parser("status")
    status.add_argument("goal")
    feedback = commands.add_parser("feedback", help="Queue human steering for the next planning boundary")
    feedback.add_argument("goal")
    feedback.add_argument("text")
    reconcile = commands.add_parser("reconcile", help="Mark a dead, uncertain call failed after checking its external effects")
    reconcile.add_argument("goal")
    reconcile.add_argument("--call", required=True)
    reconcile.add_argument("--kind", choices=["agent", "verifier"], default="agent")
    reconcile.add_argument("--mark-failed", action="store_true", required=True,
                           help="Explicitly confirms that external effects have been checked; does not replay the call")
    demo = commands.add_parser("demo", help="Deterministic agent fixture + real Python checker; no model calls")
    demo.add_argument("--output")
    doctor = commands.add_parser("doctor", help="Check local runtime/config availability without calling a model")
    doctor.add_argument("--config")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "ask":
            config = config_at(args.config)
            verifier = config.get("verifier")
            if args.verify_command:
                verifier = {"command": json.loads(args.verify_command), "mode": args.verify_mode,
                            "authority": "user_provided"}
                if args.verify_mode == "json":
                    verifier["check_ids"] = (args.check_ids or "").split(",") if args.check_ids else []
            import uuid
            output = (Path(args.output).expanduser() if args.output else
                      Path.home() / ".local/share/general-rsi/goals" / (time.strftime("goal-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]))
            empty_workspace = Path(tempfile.mkdtemp(prefix="rsi-input-")) if args.workspace is None else None
            try:
                controller = Controller.create(output, args.question, args.workspace or empty_workspace, config, verifier, args.memory)
            finally:
                if empty_workspace:
                    shutil.rmtree(empty_workspace)
            if args.baseline_existing:
                controller.state["baseline_existing"] = True
                controller.save()
            report = controller.run()
        elif args.command == "resume":
            controller = Controller(args.goal)
            if args.extra_agent_calls < 0 or args.extra_seconds < 0:
                raise ValueError("Budget extensions must be nonnegative")
            with lock(controller.root / ".controller.lock"):
                if controller.state["status"] in {"protocol_error", "execution_error"}:
                    controller.state["protocol_revision"] = controller.state.get("protocol_revision", 0) + 1
                controller.state["limits"]["max_agent_calls"] += args.extra_agent_calls
                if args.extra_seconds:
                    if controller.state["deadline"] is None:
                        raise ValueError("This goal has no time limit; omit --extra-seconds")
                    controller.state["deadline"] += args.extra_seconds
                validate_limits(controller.state["limits"])
                controller.save()
            report = controller.run()
        elif args.command == "status":
            report = Controller(args.goal).report()
        elif args.command == "feedback":
            append_feedback(Path(args.goal).resolve(), args.text)
            report = {"queued": True, "goal": str(Path(args.goal).resolve()),
                      "takes_effect": "Next planning boundary; sealed acceptance is unchanged"}
        elif args.command == "reconcile":
            controller = Controller(args.goal)
            if not args.call or Path(args.call).name != args.call:
                raise ValueError("Invalid call id")
            folder = controller.root / ("calls" if args.kind == "agent" else "verifications") / args.call
            with lock(controller.root / ".controller.lock"):
                receipt = "result.json" if args.kind == "agent" else "process-result.json"
                if (not (folder / "started.json").exists() or (folder / receipt).exists()
                        or (args.kind == "verifier" and (folder / "feedback.json").exists())):
                    raise ValueError("Call is not an uncertain started call")
                if process_alive(read_json(folder / "process.json", {})):
                    raise ValueError("Call process is still alive; inspect/stop it through its owner first")
                if args.kind == "agent":
                    result = {"ok": False, "text": "", "usage": None,
                              "error": "Operator reconciled uncertain execution and marked it failed; not replayed"}
                    write_json(folder / "result.json", result)
                    controller.settle(args.call, result)
                    controller.state.update(status="execution_error", reason=result["error"])
                    controller.save()
                else:
                    write_json(folder / "feedback.json", {
                        "valid": False, "passed": False, "score": 0.0, "checks": [],
                        "diagnostic": "Operator reconciled uncertain verification as failed; no passing measurement claimed",
                        "verifier_seal": controller.state.get("verifier_seal")})
                controller.event("call_reconciled", call_id=args.call, call_kind=args.kind)
            report = {"reconciled": args.call, "kind": args.kind, "replayed": False,
                      "usage": "unknown, not zero" if args.kind == "agent" else "No model call"}
        elif args.command == "doctor":
            config = config_at(args.config)
            backend = config["backend"]
            executable = pi_executable(backend) if backend.get("type", "pi") == "pi" else backend.get("command", [""])[0]
            report = {"python": sys.version.split()[0], "backend_type": backend.get("type", "pi"),
                      "executable_found": bool(shutil.which(executable)), "executable": executable,
                      "provider": backend.get("provider"), "model": backend.get("model"),
                      "env_file_exists": Path(backend.get("env_file", "/nonexistent")).expanduser().is_file(),
                      "model_called": False, "note": "Runtime presence does not prove credentials/model availability; run a small real goal."}
        else:
            output = Path(args.output).resolve() if args.output else Path(tempfile.mkdtemp(prefix="rsi-demo-")) / "goal"
            workspace = Path(tempfile.mkdtemp(prefix="rsi-demo-input-"))
            (workspace / "task.txt").write_text("Find the minimum of (x-7)^2 for integer x in [-20,20]. Write result.json with x.\n")
            config = config_at(PROJECT / "examples/demo.json")
            controller = Controller.create(output, "Minimize the integer quadratic and produce a verified answer",
                                           workspace, config, config["verifier"])
            report = controller.run()
            report["demo_notice"] = "Deterministic scripted agents, actual subprocess calculations; not evidence of real-model quality."
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("status") in {None, "solved"} else 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
