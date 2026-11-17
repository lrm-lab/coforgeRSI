"""Start the copied demo with repository-local credentials and Pi installation."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def runtime_env():
    env = dict(os.environ)
    credential_file = ROOT / 'private/team-credentials.json'
    if credential_file.exists():
        saved = json.loads(credential_file.read_text())
        for name in ('RSI_WEB_MODEL_API_KEY', 'RSI_PI_MODELS_JSON'):
            env.setdefault(name, saved[name])
    env['PATH'] = os.pathsep.join([
        str(ROOT / 'private/pi/node_modules/.bin'),
        str(ROOT / 'private/node/bin'), env.get('PATH', '')])
    return env


def main():
    env = runtime_env()
    if not env.get('RSI_WEB_MODEL_API_KEY') or not env.get('RSI_PI_MODELS_JSON'):
        raise SystemExit('尚未配置团队凭据，请先运行 ./setup.sh')
    host = env.get('RSI_WEB_HOST', '127.0.0.1')
    port = env.get('RSI_WEB_PORT', '18766')
    print(f'RSI 页面：http://{host}:{port} （Ctrl+C 停止网页）', flush=True)
    os.chdir(ROOT)
    os.execve(sys.executable, [sys.executable, '-u', str(ROOT / 'server.py'),
                             '--host', host, '--port', port, *sys.argv[1:]], env)


if __name__ == '__main__':
    main()
