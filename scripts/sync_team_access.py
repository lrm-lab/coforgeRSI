"""Administrator: sync current organization members and repository collaborators to the access secret."""
import json
import subprocess

REPO = 'lrm-lab/coforgeRSI'
ORG = 'lrm-lab'


def get_all(endpoint):
    result = subprocess.run(['gh', 'api', '--paginate', '--slurp', endpoint],
                            check=True, capture_output=True, text=True)
    return [item for page in json.loads(result.stdout) for item in page]


def main():
    members = get_all(f'orgs/{ORG}/members?per_page=100')
    collaborators = get_all(f'repos/{REPO}/collaborators?per_page=100')
    identities = sorted({p['id'] for p in members + collaborators if p.get('type') == 'User'})
    if not identities:
        raise SystemExit('未取得成员名单，未修改访问配置。')
    result = subprocess.run(['gh', 'secret', 'set', 'RSI_TEAM_MEMBER_IDS', '-R', REPO],
                            input=json.dumps(identities), text=True, capture_output=True)
    if result.returncode:
        raise SystemExit('同步失败，请使用具有组织 read:org 和仓库 Secrets 管理权限的 gh 账户。')
    print(f'已同步 {len(identities)} 个成员/协作者 ID 到受保护的仓库 Secret；未修改代码权限。')


if __name__ == '__main__':
    main()
