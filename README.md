# DeepSeek Harness Workbench

Windows 本地创作与自动化工作台，包含两个部分：

- `dsh-companion/`：本地调度、长期记忆、任务队列、额度记录和 Web 工作台。
- `creator/`：创作者 MCP 工具、平台登录核验、浏览器桥接和控制台。

## 安装

环境：Windows 10/11、Python 3.11+、Node.js 22+、Google Chrome。

```powershell
git clone https://github.com/mlTiantong/deepseek-harness-workbench.git
cd deepseek-harness-workbench

py -3.11 -m venv dsh-companion\.venv
.\dsh-companion\.venv\Scripts\python.exe -m pip install -e .\dsh-companion
cd dsh-companion
npm ci --prefix .\runtime --no-audit --no-fund
.\.venv\Scripts\dsh-companion.exe run
```

启动后打开 `http://127.0.0.1:8765/`，在工作台中填写模型地址、模型名称和 API Key。

创作者工作台需要先安装 DeepSeek Harness 桌面版，然后在仓库根目录执行：

```powershell
py -3.11 -m venv creator\.venv
.\creator\.venv\Scripts\python.exe -m pip install -r creator\requirements.lock.txt
Copy-Item creator\SOUL.md.example creator\SOUL.md
Copy-Item creator\POLICY.md.example creator\POLICY.md
Copy-Item creator\INTERESTS.md.example creator\INTERESTS.md
Copy-Item creator\OWNER_CONTACT.json.example creator\OWNER_CONTACT.json
Copy-Item creator\config.example.json creator\config.json
.\知游工作台.cmd
```

## 实际使用的插件

| 插件 | 版本 | 功能 |
| --- | --- | --- |
| `dsh-bili-scroll` | 2.2.1 | B 站搜索、字幕、画面分析、日报和互动 |
| `dsh-remotion` | 0.7.0 | 横屏/竖屏视频、动画、模板和 MP4 导出 |
| `dsh-wechat` | 0.13.3 | 微信聊天、文件、图片、语音和会话控制 |
| `dsh-wechat-mp` | 0.3.0 | 公众号 Markdown 排版、图片上传和草稿箱 |
| `dsh-xiaohongshu-viral-note` | 0.1.0 | 小红书选题、文案、账号分析、登录和受控发布 |
| `dsh-web-tools` | Git 版本 | 网页搜索和小红书笔记阅读 |
| `dsh-soul-md` | 0.10.3 | 人设卡和分层记忆 |
| `dsh-douyin-life` | 0.2.1 | 抖音工作流扩展 |
| `dsh_douyin_tools` | Git 子模块 | 抖音网页 MCP 和相关工具 |

插件来源和链接见 [`PLUGINS.md`](PLUGINS.md)。
