"""Optional JSON-stdin bridge to an existing NexAU Deep Research configuration.

This does not install NexAU, tools or compute resources. Use the Python environment
that already runs the user's Deep Research code. No model call happens at import.
"""
import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-yaml", required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--base-url", default="https://api.zhizengzeng.com/v1")
    parser.add_argument("--key-env", default="ZHIZENGZENG_API_KEY")
    args = parser.parse_args()
    request = json.load(sys.stdin)
    key = os.environ.get(args.key_env) or os.environ.get("ZZZ_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        print("Deep Research credential is not present in the configured environment", file=sys.stderr)
        return 2
    # Libraries may log to stdout. Reserve stdout for the bridge protocol.
    with contextlib.redirect_stdout(sys.stderr):
        from nexau import Agent, AgentConfig
        from nexau.archs.llm import LLMConfig
        config = AgentConfig.from_yaml(args.agent_yaml)
        def configure(current):
            current.llm_config = LLMConfig(model=args.model, base_url=args.base_url, api_key=key)
            for sub in (current.sub_agents or {}).values():
                configure(sub)
        configure(config)
        agent = Agent(config=config)
        payload = request["input"]
        message = ("Research this specific evidence gap for the supplied goal. Return findings, "
                   "sources, limitations, and actionable hypotheses. Do not claim unrun experiments.\n"
                   + json.dumps(payload, ensure_ascii=False))
        response = agent.run(message=message, context={"date": datetime.now(timezone.utc).isoformat()})
    print(json.dumps({"text": response if isinstance(response, str) else json.dumps(response, ensure_ascii=False),
                      "usage": None, "usage_note": "NexAU bridge has no complete usage receipt; unknown is not zero."}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
