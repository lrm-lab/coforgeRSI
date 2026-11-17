# 团队成员直接运行

适用 Linux、macOS；Windows 使用 WSL。需要 Python 3.11+、Git、GitHub CLI（`gh`）、OpenSSL 和 tar。Ubuntu 安装：

```bash
sudo apt-get update
sudo apt-get install -y python3 git gh openssl ca-certificates
```

macOS 可用 Homebrew 安装依赖（使用 Homebrew 的 OpenSSL 3）：

```bash
brew install python gh openssl@3
export PATH="$(brew --prefix openssl@3)/bin:$PATH"
```

Node.js 和 Pi 会自动安装到仓库的 `private/`，不要求预先配置模型账户，不修改全局 Node/Pi 配置。

```bash
git clone https://github.com/lrm-lab/coforgeRSI.git
cd coforgeRSI
./setup.sh
```

首次运行如未登录 GitHub，会启动 `gh auth login`。登录 **lrm-lab 成员或此仓库协作者**的账户即可，无需代码 Write 权限。脚本自动创建配置请求，GitHub 根据请求作者的成员/协作者身份启动工作流，领取加密配置，安装 Pi，然后启动网页。打开 **http://127.0.0.1:18766** 即可发起真实模型任务。使用团队共享的模型额度。

后续启动：`./start.sh`。只安装和配置：`./setup.sh --no-start`。更新了仓库凭据后：`./setup.sh --refresh-credentials`。

使用其他地址或端口：`RSI_WEB_HOST=0.0.0.0 RSI_WEB_PORT=18766 ./start.sh`。默认仅监听本机；原网页按受信任内网设计，没有新增登录和容器隔离。

## 团队凭据如何分发

仓库的 Actions Secrets 已配置 `RSI_WEB_MODEL_API_KEY` 和 `RSI_PI_MODELS_JSON`。前者是实际模型 key，后者是线上模型配置，key 字段只引用前者。原部署的 Agent 与语义评分器使用同一凭据；网页没有单独的登录密码。

GitHub Secrets 不会随 git clone 下发，也没有读取明文的 API。`setup.sh` 为每次领取临时生成接收密钥，通过一条自动配置 Issue 提交公开证书；工作流仅接受 GitHub 标记为 `MEMBER`、`OWNER` 或 `COLLABORATOR` 的请求，不相信请求正文自报的身份。工作流使用 OpenSSL CMS / AES-256-GCM 加密配置，只上传密文 artifact。只有发起这次领取的本机临时私钥能解密，其他人下载该 artifact 不能得到配置。工作流不执行仓库代码，不接收凭据明文作为输入，不在日志中打印凭据。请求完成后 Issue 自动关闭，不发布评论。

普通公开读者和仅提交过 PR 的贡献者无法领取；PR 不触发凭据分发。管理员也可手动运行相同工作流。仓库需保持 Issues、Actions 和 artifact 可用。密文 artifact 保留一天；临时私钥在领取结束时删除。本机凭据在 `private/team-credentials.json`，目录权限 0700、文件权限 0600，已被 `.gitignore` 排除。运行进程通过环境传入 key，Pi 模型文件仍只保存变量引用。

已通过其他受控渠道配置环境时，也可导出这两个同名变量再运行 `./setup.sh`，脚本会使用现有环境，无需触发工作流。管理员轮换 Secrets 后，成员需要重新领取。移除 GitHub 权限不会自动撤回成员已经领取的模型 key；撤回模型访问需要轮换上游 key。

## 仓库范围

来源：`10.110.158.144:/home/jianghp3/rsi-web-20261114`，用户服务 `rsi-web-demo.service`，2026-11-17 抓取。包含网页、RSI 核心、三个历史运行时、测试、评分适配器和原有证据摘要。与服务器相比，只为团队部署增加了环境配置入口、安装/启动脚本、CI 和加密凭据分发。

原服务器约 25GB 的任务输入、模型会话、工作区与历史结果未上传；`/?job=20261116-182333-53711560` 这条历史记录继续保留在原服务器。新机器启动后有预置 GAIA 回放，并可创建自己的任务。大型 benchmark PDF/归档和虚拟环境未放入 Git：需要时执行 `python3 benchmarks/prepare_three_tasks.py` 获取三题附件，下载器按来源摘要校验。附加 benchmark 依赖按原脚本使用独立虚拟环境安装；日常网页和 RSI 核心不需要这些附件。

原线上源码摘要保存在 `deployment/SERVER-WEB-MANIFEST.json`；`WEB-MANIFEST.json` 描述此次 GitHub 交付文件。原 `rsi/SOURCE-MANIFEST.json` 和 `runtimes/` 内核心源码不变。原启动方式和运维说明见 README 后文。
