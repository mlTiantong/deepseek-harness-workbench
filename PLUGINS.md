# 插件清单

以下是本地工作台中使用或验证过的 DSH 插件。版本来自本地 profile 配置；没有随本仓库上传插件安装包、用户数据或本地 `node_modules`。

| 插件 | 版本 | 用途 | 来源 |
| --- | --- | --- | --- |
| `dsh-bili-scroll` | 2.2.1 | B 站搜索、字幕、画面分析、日报和互动工具 | [c1overzz/dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) |
| `dsh-remotion` | 0.7.0 | 横屏/竖屏视频、动画、模板和 MP4 导出 | 本地 profile 依赖，按上游包许可证使用 |
| `dsh-wechat` | 0.13.3 | 微信聊天、文件、图片、语音和会话控制 | [pan17/dsh-wechat](https://github.com/pan17/dsh-wechat) |
| `dsh-wechat-mp` | 0.3.0 | 公众号 Markdown 排版、图片上传和草稿箱 | 本地 profile 依赖，按上游包许可证使用 |
| `dsh-xiaohongshu-viral-note` | 0.1.0 | 小红书选题、文案、账号分析、登录和受控发布工作流 | [xuboboo/dsh-xiaohongshu-viral-note](https://github.com/xuboboo/dsh-xiaohongshu-viral-note) |
| `dsh-web-tools` | Git 版本 | 网页搜索和小红书笔记阅读 | [A3Boy/dsh-web-tools](https://github.com/A3Boy/dsh-web-tools) |
| `dsh-soul-md` | 0.10.3 | 人设卡和分层记忆 UI | DSH profile 依赖，按上游包许可证使用 |
| `dsh-douyin-life` | 0.2.1 | 本地抖音工作流扩展 | 本项目本地构建包，未上传私有运行数据 |
| `dsh_douyin_tools` | Git 子模块 | Companion 使用的抖音网页 MCP 和相关工具 | [kdcadmin/dsh_douyin_tools](https://github.com/kdcadmin/dsh_douyin_tools) |

## 本项目桌宠

集成 [wraven68/deepseek-harness-pet](https://github.com/wraven68/deepseek-harness-pet) 的 MIT 桌宠窗口代码，固定上游版本 `727554719360d58c49a16d3f4a4cc736ec95b4d8`。角色改用 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 的 17 段迷你 Q 版透明动画（署名、非商业、CC BY-NC-SA 4.0）。本项目用 Companion 真实任务和工具状态区分回消息、刷视频、读评论、写稿等动作，保留角色分工、工作台入口与暂停 / 恢复。无需给现有 DSH profile 安装插件。

## 运行时依赖

`dsh-companion` 还依赖 `deepseek-harness-sdk`、`@deepseek-ai/dsh`、`bilibili-api-python`、`douyin-im`、Playwright、FastAPI、MCP SDK、imageio-ffmpeg 和 faster-whisper。完整版本约束见 [`dsh-companion/pyproject.toml`](dsh-companion/pyproject.toml) 与 [`dsh-companion/runtime/package.json`](dsh-companion/runtime/package.json)。

插件能否执行取决于平台登录状态、账号权限、网页版本、地区和第三方服务可用性。请先在测试账号和单条内容上人工核对，不要把自动化执行结果当作平台官方确认。
