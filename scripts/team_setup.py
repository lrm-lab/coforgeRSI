"""Install Pi and receive repository secrets encrypted for this team member."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPO = 'lrm-lab/coforgeRSI'
PI_PACKAGE = '@earendil-works/pi-coding-agent@0.87.1'
SECRET_NAMES = ('RSI_WEB_MODEL_API_KEY', 'RSI_PI_MODELS_JSON')


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)


def capture(argv):
    return run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def validate_credentials(values):
    if any(not isinstance(values.get(k), str) or not values[k] for k in SECRET_NAMES):
        raise ValueError('缺少仓库模型凭据；请让仓库管理员检查 GitHub Secrets')
    config = json.loads(values['RSI_PI_MODELS_JSON'])
    provider = config['providers']['zhizengzeng']
    if provider.get('apiKey') != '${RSI_WEB_MODEL_API_KEY}':
        raise ValueError('模型配置应通过 RSI_WEB_MODEL_API_KEY 引用凭据')
    if not any(m['id'] == 'deepseek-v4.1-flash' for m in provider['models']):
        raise ValueError('缺少 deepseek-v4.1-flash 模型配置')
    return {k: values[k] for k in SECRET_NAMES}


def save_credentials(values):
    values = validate_credentials(values)
    private = ROOT / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    target = private / 'team-credentials.json'
    fd, temporary = tempfile.mkstemp(prefix='.credentials-', dir=private)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(values, stream, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('团队凭据已写入本机 private/（目录 0700，文件 0600）。', flush=True)


def receive_credentials(refresh=False):
    if all(os.environ.get(k) for k in SECRET_NAMES):
        save_credentials(dict(os.environ))
        return
    target = ROOT / 'private/team-credentials.json'
    if target.exists() and not refresh:
        validate_credentials(json.loads(target.read_text()))
        print('使用本机已有的团队配置。', flush=True)
        return
    for program in ('gh', 'openssl'):
        if not shutil.which(program):
            raise SystemExit(f'请先安装 {program}，然后重新运行 ./setup.sh（见 GITHUB-SETUP.md）。')
    status = subprocess.run(['gh', 'auth', 'status'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if status.returncode:
        run(['gh', 'auth', 'login', '--hostname', 'github.com', '--web'])
    request_id = uuid.uuid4().hex
    print('正在通过 GitHub Actions 领取加密配置，通常需要 1–2 分钟…', flush=True)
    with tempfile.TemporaryDirectory(prefix='rsi-bootstrap-') as temporary:
        folder = Path(temporary)
        key, cert = folder / 'recipient.key', folder / 'recipient.pem'
        run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes',
             '-keyout', str(key), '-out', str(cert), '-days', '1',
             '-subj', '/CN=RSI-team-bootstrap/'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        payload = {'title': f'RSI bootstrap {request_id}',
                   'body': json.dumps({'request_id': request_id, 'recipient_cert': cert.read_text()})}
        # Structured stdin prevents workflow inputs from being interpreted by a shell.
        result = subprocess.run(['gh', 'api', '--method', 'POST',
                                 f'repos/{REPO}/issues', '--input', '-'],
                                input=json.dumps(payload), text=True, capture_output=True)
        if result.returncode:
            raise SystemExit('无法创建团队配置请求；请检查 GitHub 登录状态、仓库 Issues 和 Actions 是否已启用。')
        request = json.loads(result.stdout)
        if request.get('author_association') not in ('MEMBER', 'OWNER', 'COLLABORATOR'):
            subprocess.run(['gh', 'api', '--method', 'PATCH', f"repos/{REPO}/issues/{request['number']}",
                            '-f', 'state=closed'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            raise SystemExit('此 GitHub 账户不是 lrm-lab 成员或仓库协作者，无法领取团队凭据。')
        deadline = time.monotonic() + 600
        job = None
        last_status = None
        while time.monotonic() < deadline:
            runs = json.loads(capture(['gh', 'run', 'list', '-R', REPO, '--workflow', 'team-bootstrap.yml',
                                      '--event', 'issues', '--limit', '100',
                                      '--json', 'databaseId,displayTitle,status,conclusion,url']))
            job = next((r for r in runs if r['displayTitle'] == f'RSI bootstrap {request_id}'), None)
            if job:
                if job['status'] != last_status:
                    print(f"配置工作流：{job['status']} · {job['url']}", flush=True)
                    last_status = job['status']
                if job['status'] == 'completed':
                    break
            time.sleep(5)
        if not job or job['status'] != 'completed':
            raise SystemExit('领取配置等待超时，可重新运行 ./setup.sh；无需输入 API key。')
        if job['conclusion'] != 'success':
            raise SystemExit('配置工作流失败，请查看：' + job['url'])
        run(['gh', 'run', 'download', str(job['databaseId']), '-R', REPO,
             '--name', f'rsi-credentials-{request_id}', '--dir', str(folder)])
        decrypted = subprocess.run(['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER',
                                   '-in', str(folder / 'credentials.p7m'), '-recip', str(cert),
                                   '-inkey', str(key)], capture_output=True, check=True)
        save_credentials(json.loads(decrypted.stdout))
    # Temporary private key and encrypted transport artifact are never committed.


def install_runtime():
    private = ROOT / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ)
    local_node = private / 'node/bin'
    env['PATH'] = str(local_node) + os.pathsep + env.get('PATH', '')
    node = shutil.which('node', path=env['PATH'])
    version = ()
    if node:
        version = tuple(map(int, capture([node, '--version']).strip().lstrip('v').split('.')[:3]))
    if version < (22, 19, 0) or not shutil.which('npm', path=env['PATH']):
        system = {'Linux': 'linux', 'Darwin': 'darwin'}.get(platform.system())
        arch = {'x86_64': 'x64', 'AMD64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
        if not system or not arch:
            raise SystemExit('此系统请手工安装 Node.js >=22.19.0，再重新运行 ./setup.sh。Windows 请使用 WSL。')
        base = 'https://nodejs.org/dist/latest-v22.x/'
        checksums = urllib.request.urlopen(base + 'SHASUMS256.txt', timeout=60).read().decode()
        suffix = f'-{system}-{arch}.tar.gz'
        entry = next((line.split() for line in checksums.splitlines() if line.split()[-1].endswith(suffix)), None)
        if not entry:
            raise SystemExit('未找到适配此系统的 Node.js 官方发行包。')
        digest, filename = entry
        if not re.fullmatch(r'node-v[0-9.]+-(linux|darwin)-(x64|arm64)\.tar\.gz', filename):
            raise ValueError('无效的 Node.js 发行包名称')
        print('安装仓库专用 Node.js：' + filename, flush=True)
        with tempfile.TemporaryDirectory(prefix='rsi-node-') as temporary:
            archive = Path(temporary) / filename
            with urllib.request.urlopen(base + filename, timeout=120) as response, archive.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                raise ValueError('Node.js 官方 SHA-256 校验失败')
            run(['tar', '-xzf', str(archive), '-C', temporary])
            installed = private / 'node'
            if installed.exists():
                shutil.rmtree(installed)
            shutil.move(str(Path(temporary) / filename.removesuffix('.tar.gz')), installed)
    package = private / 'pi/node_modules/@earendil-works/pi-coding-agent/package.json'
    if not package.exists() or json.loads(package.read_text()).get('version') != '0.87.1':
        npm = shutil.which('npm', path=env['PATH'])
        print('安装 Pi 0.87.1（仅安装到仓库 private/）…', flush=True)
        run([npm, 'install', '--prefix', str(private / 'pi'), '--no-audit', '--no-fund', PI_PACKAGE], env=env)
    print('运行环境已就绪。', flush=True)


def main():
    if sys.version_info < (3, 11):
        raise SystemExit('请安装 Python >=3.11。')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-start', action='store_true', help='仅安装与配置，不启动网页')
    parser.add_argument('--refresh-credentials', action='store_true', help='重新领取仓库 Secrets')
    args = parser.parse_args()
    receive_credentials(args.refresh_credentials)
    install_runtime()
    if args.no_start:
        print('配置完成，运行 ./start.sh 即可启动。', flush=True)
    else:
        os.execv(sys.executable, [sys.executable, str(ROOT / 'scripts/team_start.py')])


if __name__ == '__main__':
    try:
        main()
    except (subprocess.CalledProcessError, ValueError, KeyError) as error:
        # Do not echo subprocess output or exception objects containing secret payloads.
        raise SystemExit(f'配置未完成（{type(error).__name__}），请检查网络、权限和仓库 Secrets。') from None
