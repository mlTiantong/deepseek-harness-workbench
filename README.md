# DeepSeek Harness Workbench

基于 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的 Windows 本地创作与自动化工作台。仓库保存本项目的工作台代码、创作者 MCP 工具和独立的 `dsh-companion` 服务；官方 DSH 应用本体、运行时、浏览器登录态和本地数据不放在仓库中。

## 功能

- `dsh-companion/`：独立的本地调度器、长期记忆、任务队列、额度记录和 Web 工作台。
- `creator/`：可选的创作者 MCP 服务、平台登录核验、浏览器桥接和工作台控制台。
- 根目录启动脚本：为 Windows 提供 DSH、创作者工作台和 Companion 的启动入口。
- 平台能力：通过 DSH 插件和本地适配器连接 B 站、抖音、微信、小红书等平台；涉及登录、发信和发布的操作默认需要用户自行核验。

## 安全边界

本仓库**不包含** API Key、Cookie、二维码、浏览器用户目录、登录态、SQLite 数据库、聊天记录、个人联系人配置、视频产物或官方打包程序。请勿提交 `DeepSeek-API.txt`、`.credentials.yaml`、`data/` 运行数据、`creator/state/` 和 `dsh-companion/var/`。

## 安装（Windows）

环境要求：Windows 10/11、Python 3.11+、Node.js 22+、Google Chrome，以及一个 OpenAI-compatible 模型接口。

```powershell
git clone https://github.com/mlTiantong/deepseek-harness-workbench.git
cd deepseek-harness-workbench

# Companion 服务
py -3.11 -m venv dsh-companion\.venv
.\dsh-companion\.venv\Scripts\python.exe -m pip install -e .\dsh-companion
cd dsh-companion
npm ci --prefix .\runtime --no-audit --no-fund
.\.venv\Scripts\dsh-companion.exe run
```

启动后访问 `http://127.0.0.1:8765/`。模型配置可在工作台中填写，也可以先设置 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_DEFAULT_MODEL`，再运行根目录的 `configure.py`。密钥只写入被 Git 忽略的本地 `data/.credentials.yaml`。

创作者工作台是可选模块：它还需要单独安装官方 DeepSeek Harness 桌面应用；若应用不在仓库的默认 `app/` 目录，请设置 `DSH_APP_PATH`。然后安装 `creator/requirements.lock.txt`，复制 `creator/*.example` 为本地配置文件，再运行 `知游工作台.cmd`。完整说明见 [`dsh-companion/README.md`](dsh-companion/README.md) 和 [`creator/README.md`](creator/README.md)。

## 使用的插件与依赖

本项目使用的 DSH 插件、版本和来源列在 [`PLUGINS.md`](PLUGINS.md)。插件由各自作者维护，版本、平台规则和网页选择器可能变化；本仓库不重新分发或重新授权这些第三方项目。许可证与来源见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 上游项目

官方 DeepSeek Harness 源码和开发文档请参阅 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。本仓库不复制上游源码，也不替代上游项目的安全说明和许可证。

## 许可证

本仓库原创代码采用 [MIT License](LICENSE)。第三方插件、依赖和 `dsh-companion/vendor/` 下的代码继续遵循其原始许可证。
