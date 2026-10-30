# DSH Companion

基于 DeepSeek Harness 的独立本地创作助手。它把长期记忆、任务调度、额度记录、平台适配器和 Web 工作台放在项目自己的目录中，不修改用户已有的 DSH profile 或登录态。

## 功能

- 本地 SQLite 持久化：记忆、事件、任务、角色、动作和模型用量。
- FastAPI Web 工作台：配置模型、平台、创作文件夹、额度和调度规则。
- DSH 会话执行：实际思考、工具调用和对外动作交给项目自己的 DSH 会话。
- B 站、抖音和微信适配器，以及可选的抖音网页 MCP；小红书和视频理解能力保留为接口或按插件提供。

平台登录、私信、评论和发布均需要用户自行完成登录与核验。平台页面、权限和第三方接口变化时，功能可能失效；不要把测试状态当作稳定承诺。

## 环境要求

- Windows、Python 3.11+、Node.js 22+、Google Chrome。
- 一个 OpenAI-compatible 模型接口。

## 安装与启动

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
npm ci --prefix .\runtime --no-audit --no-fund
.\.venv\Scripts\dsh-companion.exe run
```

启动后访问 `http://127.0.0.1:8765/`。也可以用工作台的模型设置填写 Base URL、模型和密钥，或从本地三行路由文件导入：

```powershell
.\.venv\Scripts\dsh-companion.exe import-model --api-file 'D:\path\model-route.txt' --route-index 0
```

路由文件只应保存在本机，格式为 URL、密钥、模型名三行一组。运行数据和密钥会写入 `var/`，该目录不会提交到 Git。

## 测试

```powershell
python -m pytest
```

测试可能需要额外的浏览器或模型运行时。涉及真实平台的测试必须使用测试账号，并在执行前确认不会发送或发布内容。

## 依赖与插件

主要依赖包括 `deepseek-harness-sdk`、`@deepseek-ai/dsh`、`bilibili-api-python`、`douyin-im`、Playwright、FastAPI 和 MCP SDK。DSH 插件清单和第三方来源见仓库根目录的 [`PLUGINS.md`](../PLUGINS.md) 与 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
