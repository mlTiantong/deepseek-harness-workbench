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

## 评论、人格与自主方向

- `watch_video` 和 `fetch_media_frames` 返回 **画面 / 图文 + 可用语音 / 字幕 + 优先评论**。默认读取 30 条，工作台可设 10–50 条；按置顶、作者补充、高赞排序。同一条评论只读一次，评论不足时返回实际数量。
- 可传 `comment_limit=50` 增加评论量，或用 `include_comments=false` 只看作品。B 站会翻页获取目标数量；抖音直接读取作品评论接口。评论字段保留作者、点赞数和原始评论 ID。
- 默认人设为空，不再附带毒舌人格。已有自定义人设保留；记忆页提供「清空人设」。
- 「设置 → 生物钟与作息节拍 → 自主方向」填写想长期推进的目标。每次自主唤醒都会带上方向、近期记忆、未完成任务、执行结果和可用能力，自己选行动、分工和后续安排，不固定刷推荐。

## 评论、私信图片与表情包

- 视频 / 图文工具默认合并 30 条优先评论，可设 10–50；B 站、抖音均已实测。空评论区返回 `empty`，不编造评论。
- 抖音私信和群聊图片支持原图地址与 AES-GCM 解密；表情包读取真实 CDN `url_list`，保留备用地址，同时兼容旧事件记录。
- GIF / 动态 WebP 表情按时间抽取最多 4 帧 PNG 给模型，原始动图仍保存在媒体库，可供复用。
- 微信收到的本地图片接入同一视觉链路；B 站私信 / 群聊的图片消息（类型 2 / 6）也转为图片输入。
- `平台 → 巡游规则` 中的图片与表情选项 控制读取和保存。当前模型实测可识别图像内容，不只接收图片链接。
- 完整自动化测试：`python -m unittest discover -s tests -p "test_*.py"`；只读实机验收：`python tools/verify_live_reading.py`。实机验收使用现有入站素材与当前模型，不发送私信，结果只写到忽略的 `var/feature-qa/`。

## 桌上的大肥鱼

迷你 Q 版鲸鱼娘使用 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 的 17 段真正多帧透明动画；窗口沿用 wraven68 项目的 MIT 代码。动画署名与非商业许可见 `src/dsh_companion/desktop_pet/ASSET_LICENSE.md`。

先启动工作台，在设置页点「叫桌宠出来」，或运行仓库根目录的 `启动桌宠.cmd`。打开后随工作台自动启动；点「收起桌宠」取消自动启动。

- 透明置顶、真正多帧动画，随工作 / 完成 / 休息切换动作。
- 状态条显示「大肥鱼在读评论」「大肥鱼在画图」「大肥鱼在回私信」等真实动作。
- 点击角色展开浅色半透明小卡片（248 px 宽、198–258 px 高，82% 不透明度）：最多 3 条分工 / 排队事项，更多任务去工作台看；保留工作台入口。
- 拖动位置，Ctrl + 滚轮缩放；位置和尺寸保存在本项目 `var/`。
- 回消息 / 群聊 / 回评论 → 忙碌点按；刷视频 / 图文 → 对屏观看；读评论 → 思考冒泡；写稿 → 轻快记录；画图 → 提笔书写；制作代码 → 敲键盘；完成 → 雀跃庆祝；休息 → 睡觉。
- 待命时随机呼吸、跳舞、玩魔方、吃火锅；点击挥手，拖拽有悬空动作。右键也可点播待命动作。
- 卡片「唤醒」让主会话立即活动一轮；「刷视频」找作品、阅读画面 / 可用语音和评论；「找人聊」结合已有联系人 / 群实际发起交流。下方输入一句安排，回车或点「派活」即可。
- 手动派活经 `/api/pet/task` 进入现有主会话队列；忙时排队，开始执行才切动作。卡片底部显示排队、执行、完成的一句话结果或失败原因，完整记录在工作台。
- 单次唤醒和派活可在暂停自主时执行，不开启连续自主、不改变平台开关。右键「唤醒自主」仍是恢复连续自主。
- 桌宠只连接本项目的 Companion API，不扫描其他 DSH 会话，不运行第二套模型与自主调度。

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py"
node tests/test_context_plugin.mjs
python tests/verify_workbench_ui.py
python tests/verify_desktop_pet.py
```

测试可能需要额外的浏览器或模型运行时。涉及真实平台的测试必须使用测试账号，并在执行前确认不会发送或发布内容。

## 依赖与插件

主要依赖包括 `deepseek-harness-sdk`、`@deepseek-ai/dsh`、`bilibili-api-python`、`douyin-im`、Playwright、FastAPI 和 MCP SDK。DSH 插件清单和第三方来源见仓库根目录的 [`PLUGINS.md`](../PLUGINS.md) 与 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
