# DSH Companion

基于 DeepSeek Harness 的独立本地创作助手。它把长期记忆、任务调度、额度记录、平台适配器和 Web 工作台放在项目自己的目录中，不修改用户已有的 DSH profile 或登录态。

## 功能

- 本地 SQLite 持久化：记忆、事件、任务、角色、动作和模型用量。
- FastAPI Web 工作台：配置模型、平台、创作文件夹、额度和调度规则。
- DSH 会话执行：实际思考、工具调用和对外动作交给项目自己的 DSH 会话。
- B 站、抖音和微信适配器；B 站和抖音支持采样视频画面、本地语音转写与总结。小红书仍为预留接口。

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

## 视频阅读和投稿

在「平台」页的「视频阅读与语音」中开启视频阅读、选择是否本地转写语音，并设置 B 站与抖音共用的每日观看次数（0 表示不限）。每次成功阅读计一次；失败不计。各平台仍须启用并按需要登录。默认使用 CPU 的 Whisper base；首次开启语音时需要下载模型，之后本地运行。音频不上传转写服务；采样画面和转写文本会发送给配置的视觉模型。也可选更快的 tiny 或更准确的 small。FFmpeg 提取音轨，VAD 尽量过滤非语音，转写可能有误，不保证能完全排除音乐或歌声。

在会话中提供 B 站 BV 号、抖音作品 ID 或视频链接，要求总结即可。`watch_video` 默认按时间顺序采样 8 帧并返回本地语音转写；B 站有可用字幕时也会返回（默认读取第一个分 P）；抖音下载源文件当前有 256 MiB 的上限；音频读取失败时仍返回已获取的画面并注明原因。`fetch_media_frames` 可指定时间点，`include_audio=false` 可只看画面（总开关关闭时不会转写）。采样不是逐帧观看；模型需要支持图片输入。群聊分享卡可用其中的作品 ID 读取，所得总结可发送到指定群或私信。

B 站视频投稿可直接调用 `publish_content`：未指定 `metadata.tid` 时默认软件应用分区 230，默认原创并生成视频封面；可以通过 `metadata.tid`、`metadata.original`、`metadata.source` 和 `metadata.cover` 覆盖。接口返回 BV 号表示投稿已提交，不代表已通过审核或公开可见。

宣传片可用 `python promo/render.py --format both` 重新生成横屏和竖屏 MP4；输出到 `content/`，使用本机 Chrome、FFmpeg 和合成配乐。

## 测试

```powershell
python -m pytest
```

测试可能需要额外的浏览器或模型运行时。涉及真实平台的测试必须使用测试账号，并在执行前确认不会发送或发布内容。

## 依赖与插件

主要依赖包括 `deepseek-harness-sdk`、`@deepseek-ai/dsh`、`bilibili-api-python`、`douyin-im`、Playwright、FastAPI 和 MCP SDK。DSH 插件清单和第三方来源见仓库根目录的 [`PLUGINS.md`](../PLUGINS.md) 与 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
