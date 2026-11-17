"""Deterministic protocol fixture. It is intentionally NOT a real LLM agent."""
import json
import sys
from pathlib import Path

request = json.load(sys.stdin)
role, payload = request["role"], request["input"]
if role == "bootstrap":
    answer = {"acceptance": ["Integer x belongs to [-20,20]", "Objective is globally minimal"],
              "initial_research_question": "How can a finite integer domain be verified exactly?", "blocked": ""}
elif role == "research":
    answer = {"findings": "Exhaustive enumeration is exact on a bounded finite integer domain.",
              "sources": ["task.txt"], "limitations": "This method scales with domain size."}
elif role == "planner":
    answer = {"action": "experiment", "reason": "Baseline skipped complete-domain verification.", "candidates": [
        {"parent_id": "baseline", "observation": "Baseline minimum check failed.",
         "hypothesis": "Complete enumeration avoids a local guess.",
         "experience": "For a finite integer domain, enumerate every permitted point and compare the measured objective before committing the answer.",
         "scope": "integer quadratic finite domain minimization", "expected_effect": "Unmeasured: should identify the minimum.",
         "evidence_refs": ["baseline"]},
        {"parent_id": "baseline", "observation": "A guessed point was not minimal.",
         "hypothesis": "A bounded incomplete search may improve but miss the minimum.",
         "experience": "Search the nonpositive half of the integer domain and retain the best measured point.",
         "scope": "integer domain search", "expected_effect": "Unmeasured partial improvement.",
         "evidence_refs": ["baseline"]}]}
else:
    guidance = payload["experience"]
    if "enumerate every" in guidance:
        x = min(range(-20, 21), key=lambda x: (x - 7) ** 2)
    elif "nonpositive half" in guidance:
        x = min(range(-20, 1), key=lambda x: (x - 7) ** 2)
    else:
        x = -20  # Seed failure, for testing the feedback-and-evolution path.
    Path("result.json").write_text(json.dumps({"x": x}))
    answer = f"x={x}; objective={(x-7)**2}. Result is in result.json."
text = json.dumps(answer) if isinstance(answer, dict) else answer
print(json.dumps({"text": text, "usage": {"total_tokens": 100, "cost_usd": 0.0,
                                           "source": "deterministic_fixture_no_model_call"}}))
