import copy
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from general_rsi.backend import pi_session_usage, run_process
from general_rsi.cli import PROJECT, config_at, main
from general_rsi.engine import Controller
from general_rsi.protocol import check_decision, feedback_from_process, retention_gate
from general_rsi.storage import MemoryBank, append_feedback, digest, read_json, write_json


def row(name, score, tokens=100, valid=True):
    return {"id": name, "feedback": {"valid": valid, "passed": score == 1, "score": score},
            "usage": {"total_tokens": tokens}}


class GateTests(unittest.TestCase):
    def test_replicated_quality_gain(self):
        self.assertTrue(retention_gate([row("a", 1), row("b", 1)], [row("parent", 0)])["accepted"])

    def test_tie_with_baseline_is_allowed(self):
        gate = retention_gate([row("a", 1), row("b", 0)], [row("parent", 0)])
        self.assertTrue(gate["accepted"])

    def test_actual_b_scores_are_accepted(self):
        gate = retention_gate([row("a", 1), row("b", .9667)], [row("parent", .9667)])
        self.assertTrue(gate["accepted"])
        self.assertAlmostEqual(gate["validation_mean_score"], .98335)

    def test_regression_from_parent_above_initial_baseline_is_allowed(self):
        gate = retention_gate([row("a", .96), row("b", .94)], [row("parent", .99)], [row("baseline", .9)])
        self.assertTrue(gate["accepted"])
        self.assertFalse(gate["quality_gain"])

    def test_one_below_baseline_is_rejected_even_if_average_is_higher(self):
        gate = retention_gate([row("a", 1), row("b", .89)], [row("parent", .9)])
        self.assertFalse(gate["accepted"])
        self.assertIn("validation_regressed_below_baseline", gate["reasons"])

    def test_all_ties_do_not_require_token_gain(self):
        gate = retention_gate([row("a", .9667, 200), row("b", .9667, 200)], [row("parent", .9667, 100)])
        self.assertTrue(gate["accepted"])
        self.assertFalse(gate["quality_gain"])
        self.assertFalse(gate["token_gain"])

    def test_duplicate_execution_rejected(self):
        self.assertFalse(retention_gate([row("a", 1), row("a", 1)], [row("parent", 0)])["accepted"])

    def test_regression_cannot_be_bought_by_tokens(self):
        self.assertFalse(retention_gate([row("a", .8, 1), row("b", .8, 1)], [row("parent", .9, 1000)])["accepted"])

    def test_equal_quality_token_saving_is_eligible(self):
        gate = retention_gate([row("a", 1, 50), row("b", 1, 60)], [row("parent", 1, 100)])
        self.assertTrue(gate["accepted"])
        self.assertTrue(gate["token_gain"])

    def test_missing_usage_does_not_become_zero_cost_gain(self):
        self.assertFalse(retention_gate([row("a", 1, None), row("b", 1, None)], [row("parent", 1, 100)])["token_gain"])

    def test_inconsistent_usage_cannot_claim_efficiency_gain(self):
        validation = [row("a", 1, 1), row("b", 1, 1)]
        validation[0]["usage"]["accounting_valid"] = False
        self.assertFalse(retention_gate(validation, [row("parent", 1, 100)])["token_gain"])

    def test_invalid_execution_cannot_be_retained(self):
        self.assertFalse(retention_gate([row("a", 1, valid=False), row("b", 1)], [row("parent", 0)])["accepted"])


class EnvironmentTests(unittest.TestCase):
    def process(self, output, returncode=0):
        return {"returncode": returncode, "timed_out": False, "elapsed_seconds": .1,
                "stdout_sha256": "receipt", "stdout_tail": json.dumps(output), "stderr_tail": ""}

    def spec(self):
        return {"mode": "json", "check_ids": ["requirement"]}

    def test_model_success_text_is_not_verification(self):
        result = feedback_from_process(self.process({"passed": True, "score": 1}), self.spec())
        self.assertFalse(result["valid"])

    def test_cannot_delete_failed_requirement(self):
        data = {"passed": True, "score": 1, "checks": [{"id": "easier", "passed": True, "evidence": "ran"}]}
        self.assertFalse(feedback_from_process(self.process(data), self.spec())["valid"])

    def test_conflicting_self_verdict_rejected(self):
        data = {"passed": True, "score": 1, "checks": [{"id": "requirement", "passed": False, "evidence": "test failed"}]}
        self.assertFalse(feedback_from_process(self.process(data), self.spec())["valid"])

    def test_finite_score_required(self):
        data = {"passed": True, "score": float("nan"), "checks": [{"id": "requirement", "passed": True, "evidence": "ran"}]}
        self.assertFalse(feedback_from_process(self.process(data), self.spec())["valid"])

    def test_actual_exit_status_overrides_printed_success(self):
        result = feedback_from_process(self.process({"passed": True}, returncode=1), {"mode": "exit_code"})
        self.assertFalse(result["passed"])

    def test_subprocess_timeout_is_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_process([sys.executable, "-c", "import time; time.sleep(10)"], folder,
                                 Path(folder) / "process", "", .15)
            self.assertTrue(result["timed_out"])
            self.assertLess(result["elapsed_seconds"], 3)

    def test_uncapped_call_still_obeys_goal_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            result = run_process([sys.executable, "-c", "import time; time.sleep(10)"], folder,
                                 Path(folder) / "process", "", None, deadline=time.time()+.15)
            self.assertTrue(result["timed_out"])
            self.assertLess(result["elapsed_seconds"], 3)

    def test_no_time_limits_still_allows_cancellation(self):
        with tempfile.TemporaryDirectory() as folder:
            cancelled = threading.Event()
            cancelled.set()
            result = run_process([sys.executable, "-c", "import time; time.sleep(10)"], folder,
                                 Path(folder) / "process", "", None, cancelled=cancelled)
            self.assertTrue(result["interrupted"])
            self.assertFalse(result["timed_out"])
            self.assertLess(result["elapsed_seconds"], 3)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "source"
        self.workspace.mkdir()
        (self.workspace / "task.txt").write_text("Find integer x minimizing (x-7)^2 on [-20,20].")
        self.config = config_at(PROJECT / "examples/demo.json")

    def tearDown(self):
        self.temp.cleanup()

    def controller(self, name="goal", config=None, question="integer quadratic minimization"):
        config = config or self.config
        return Controller.create(self.root / name, question, self.workspace, config,
                                 config["verifier"], self.root / "memory")

    def test_complete_rsi_loop_and_distinct_validation(self):
        controller = self.controller()
        result = controller.run()
        self.assertEqual(result["status"], "solved")
        self.assertEqual(result["validated_experience_count"], 1)
        self.assertEqual(result["agent_calls"], 8)
        attempts = controller.all_attempts()
        validation = [x for x in attempts if x["stage"] == "validation"]
        self.assertEqual(len(validation), 2)
        self.assertEqual(len({x["workspace"] for x in validation}), 2)
        self.assertEqual(len({x["experience_sha256"] for x in validation}), 1)
        self.assertEqual(controller.state["baseline"][0]["feedback"]["score"], 0)
        self.assertFalse((self.workspace / "result.json").exists())
        events = [json.loads(x)["type"] for x in (controller.root / "events.jsonl").read_text().splitlines()]
        self.assertIn("research_recorded", events)
        self.assertIn("experience_retained", events)
        self.assertEqual(events[-1], "goal_verified")

    def test_completed_resume_does_not_repeat_calls(self):
        controller = self.controller()
        first = controller.run()
        events_before = (controller.root / "events.jsonl").read_bytes()
        second = Controller(controller.root).run()
        self.assertEqual(first["agent_calls"], second["agent_calls"])
        self.assertEqual(events_before, (controller.root / "events.jsonl").read_bytes())

    def test_stops_after_passing_artifact_and_baseline_preserving_repeats(self):
        verifier = self.root / 'partial_verifier.py'
        verifier.write_text('''import json, sys
from pathlib import Path
name = Path(sys.argv[1]).name
score = 1.0 if name in ('round-0-candidate-0', 'round-0-candidate-0-validation-0') else .9667
passed = score == 1.0
print(json.dumps({'passed':passed, 'score':score, 'checks':[
 {'id':'integer_domain','passed':True,'evidence':'fixture domain check'},
 {'id':'minimum','passed':passed,'evidence':'fixture quality check'}]}))
''')
        config = copy.deepcopy(self.config)
        config['verifier']['command'] = [sys.executable, str(verifier), '{workspace}']
        controller = self.controller(config=config)
        result = controller.run()
        self.assertEqual(result['status'], 'solved')
        self.assertEqual(result['round_index'], 1)
        self.assertEqual(result['agent_calls'], 8)
        gate = controller.state['rounds'][0]['evaluated'][0]
        self.assertTrue(gate['accepted'])
        self.assertAlmostEqual(gate['validation_mean_score'], .98335)
        final = read_json(controller.root/'attempts'/f"{result['answer_attempt']}.json")
        self.assertTrue(final['feedback']['passed'])
        repeated = read_json(controller.root/'attempts/round-0-candidate-0-validation-1.json')
        self.assertFalse(repeated['feedback']['passed'])

    def test_budget_stop_then_resume_uses_completed_calls(self):
        config = copy.deepcopy(self.config)
        config["limits"]["max_agent_calls"] = 4
        controller = self.controller(config=config)
        first = controller.run()
        self.assertEqual(first["status"], "budget_exhausted")
        self.assertEqual(first["agent_calls"], 4)
        controller.state["limits"]["max_agent_calls"] = 20
        controller.save()
        result = Controller(controller.root).run()
        self.assertEqual(result["status"], "solved")
        self.assertEqual(result["agent_calls"], 8)

    def test_retained_experience_is_reused_by_later_goal(self):
        first = self.controller("first")
        self.assertEqual(first.run()["status"], "solved")
        second = self.controller("second")
        self.assertTrue(second.state["experiences"]["baseline"]["recalled_refs"])
        result = second.run()
        self.assertEqual(result["status"], "solved")
        self.assertEqual(second.state["baseline"][0]["feedback"]["score"], 1)
        self.assertEqual(result["round_index"], 0)

    def test_input_tampering_blocks_completion(self):
        controller = self.controller()
        (controller.root / "input/task.txt").write_text("Different task")
        result = controller.run()
        self.assertEqual(result["status"], "integrity_error")
        self.assertEqual(result["agent_calls"], 0)

    def test_unknown_completion_is_not_silently_replayed(self):
        controller = self.controller()
        write_json(controller.root / "calls/bootstrap/started.json", {"role": "bootstrap"})
        result = controller.run()
        self.assertEqual(result["status"], "needs_reconciliation")
        self.assertEqual(result["agent_calls"], 0)

    def test_human_feedback_can_be_queued_while_controller_exists(self):
        controller = self.controller()
        append_feedback(controller.root, "Use a smaller experiment first")
        self.assertIn("smaller", controller.context()["human_feedback"][0]["text"])

    def test_unverified_memory_cannot_be_promoted(self):
        with self.assertRaises(ValueError):
            MemoryBank(self.root / "memory").deposit({"id": "fake", "status": "self_assessed"})

    def test_candidate_cannot_edit_its_acceptance_script(self):
        checker = self.workspace / "check.py"
        checker.write_text("raise SystemExit(1)\n")
        config = copy.deepcopy(self.config)
        config["verifier"] = {"command": [sys.executable, "check.py"], "mode": "exit_code", "authority": "user_provided"}
        controller = self.controller(config=config)
        controller.bootstrap()
        workspace_copy = controller.ensure_workspace("tamper")
        (workspace_copy / "check.py").write_text("raise SystemExit(0)\n")
        result = controller.evaluate("tamper", workspace_copy)
        self.assertFalse(result["valid"])
        self.assertIn("modified", result["diagnostic"])
        self.assertEqual(controller.state["verifier_calls"], 0)

    def test_controller_version_change_blocks_unfinished_goal(self):
        controller = self.controller()
        controller.state["implementation_hashes"]["engine.py"] = "different-version"
        controller.save()
        self.assertEqual(controller.run()["status"], "integrity_error")
        self.assertEqual(controller.state["agent_calls"], 0)

    def test_completed_verifier_receipt_is_not_charged_twice(self):
        controller = self.controller()
        controller.bootstrap()
        workspace = controller.ensure_workspace("receipt")
        first = controller.evaluate("receipt", workspace)
        (controller.root / "verifications/receipt/feedback.json").unlink()
        second = controller.evaluate("receipt", workspace)
        self.assertEqual(first, second)
        self.assertEqual(controller.state["verifier_calls"], 1)

    def test_uncertain_verifier_requires_reconciliation_and_cannot_pass(self):
        controller = self.controller()
        controller.bootstrap()
        folder = controller.root / "verifications/uncertain"
        write_json(folder / "started.json", {"time": time.time()})
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["reconcile", str(controller.root), "--kind", "verifier",
                         "--call", "uncertain", "--mark-failed"])
        self.assertEqual(code, 0)
        feedback = controller.evaluate("uncertain", controller.ensure_workspace("uncertain"))
        self.assertFalse(feedback["valid"])
        self.assertFalse(feedback["passed"])
        self.assertEqual(controller.state["verifier_calls"], 0)

    def test_failed_bootstrap_resume_uses_new_id_without_overwriting_failure(self):
        controller = self.controller()
        write_json(controller.root / "calls/bootstrap/result.json",
                   {"ok": False, "text": "", "usage": None, "error": "transport failed"})
        self.assertEqual(controller.run()["status"], "execution_error")
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["resume", str(controller.root)])
        self.assertEqual(code, 0)
        self.assertFalse(read_json(controller.root / "calls/bootstrap/result.json")["ok"])
        self.assertTrue((controller.root / "calls/bootstrap-protocol-1/result.json").exists())

    def test_question_only_cli_without_workspace(self):
        output = self.root / "question-only"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["ask", "integer quadratic minimization", "--output", str(output),
                         "--config", str(PROJECT / "examples/demo.json")])
        self.assertEqual(code, 0)
        self.assertEqual(read_json(output / "report.json")["status"], "solved")

    def test_default_has_no_time_limits_and_can_resume(self):
        config = copy.deepcopy(self.config)
        for key in ["agent_timeout", "verifier_timeout", "max_seconds"]:
            config["limits"].pop(key, None)
        controller = self.controller(config=config)
        for key in ["agent_timeout", "verifier_timeout", "max_seconds"]:
            self.assertIsNone(controller.state["limits"][key])
        self.assertIsNone(controller.state["deadline"])
        self.assertIsNone(controller.context()["remaining_seconds"])
        # Resuming an unfinished no-deadline goal must not try None + 0.
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["resume", str(controller.root)]), 0)
        self.assertEqual(Controller(controller.root).state["status"], "solved")


class UsageTests(unittest.TestCase):
    def test_compaction_included_and_zero_prices_not_claimed_free(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "session.jsonl"
            usage = {"input": 10, "output": 5, "cacheRead": 20, "cacheWrite": 0, "totalTokens": 35, "cost": {"total": 0}}
            events = [{"id": "a", "type": "message", "message": {"role": "assistant", "usage": usage}},
                      {"id": "b", "type": "compaction", "usage": usage}]
            p.write_text("\n".join(json.dumps(x) for x in events))
            result = pi_session_usage(p)
            self.assertEqual(result["total_tokens"], 70)
            self.assertEqual(result["requests"], 2)
            self.assertIsNone(result["cost_usd"])


if __name__ == "__main__":
    unittest.main()
