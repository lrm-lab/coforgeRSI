"""Demo-specific Pi configuration. Credentials are passed through the process environment."""
import copy
import json
import os
from pathlib import Path
import re

MAX_OUTPUT_TOKENS = 32768


def process_env(root, fixture=False):
    env = dict(os.environ)
    if fixture:return env
    source = Path.home()/'.pi/agent/models.json'
    config = json.loads(env['RSI_PI_MODELS_JSON']) if env.get('RSI_PI_MODELS_JSON') else json.loads(source.read_text())
    provider = copy.deepcopy(config['providers']['zhizengzeng'])
    credential = provider['apiKey']
    if credential.startswith('!'):
        raise ValueError('演示配置不执行凭据命令，请使用环境变量或 Pi 账户配置')
    credential = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)',
                        lambda m:env.get(m[1] or m[2], ''), credential)
    credential = env.get(credential, credential)
    if not credential:raise ValueError('未找到演示模型凭据')
    env['RSI_WEB_MODEL_API_KEY'] = credential
    provider['apiKey'] = '${RSI_WEB_MODEL_API_KEY}'
    model = next(m for m in provider['models'] if m['id']=='deepseek-v4.1-flash')
    model['maxTokens'] = MAX_OUTPUT_TOKENS
    # DeepSeek's documented Chat Completions field; avoid relying on proxy aliases.
    model['compat'] = {**model.get('compat', {}), 'maxTokensField':'max_tokens'}
    provider['models'] = [model]
    agent = root/'private/pi-agent'
    agent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = agent/'models.json'
    encoded = json.dumps({'providers':{'zhizengzeng':provider}}, ensure_ascii=False, indent=2)+'\n'
    if not target.exists() or target.read_text()!=encoded:
        from general_rsi.storage import write_json
        write_json(target, json.loads(encoded))
    env['PI_CODING_AGENT_DIR'] = str(agent)
    return env
