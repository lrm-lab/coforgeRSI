"""Actual calculation, independent of the scripted/real agent's self-report."""
import json
import sys
from pathlib import Path

try:
    x = json.loads((Path(sys.argv[1]) / "result.json").read_text())["x"]
    domain = isinstance(x, int) and not isinstance(x, bool) and -20 <= x <= 20
except (OSError, ValueError, KeyError, TypeError):
    x, domain = None, False
objective = (x - 7) ** 2 if domain else None
best = min((point - 7) ** 2 for point in range(-20, 21))
optimal = domain and objective == best
print(json.dumps({"passed": bool(domain and optimal),
    "score": max(0.0, 1.0 - objective / 729) if domain else 0.0,
    "checks": [{"id": "integer_domain", "passed": domain, "evidence": f"Observed x={x}; allowed integers [-20,20]."},
               {"id": "minimum", "passed": optimal, "evidence": f"Computed objective={objective}; enumerated minimum={best}."}],
    "metrics": {"x": x, "objective": objective, "minimum": best},
    "feedback": "Verified minimum" if optimal else "Candidate did not attain the exhaustively calculated minimum"}))
