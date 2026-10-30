# Creator 工作台

`creator/` 是一个可选的 DeepSeek Harness MCP 扩展，提供本地创作者记忆、平台登录核验、浏览器桥接和控制台。它适合 Windows 本地使用，不会自动获得任何平台账号权限。

## 安装

在仓库根目录执行：

```powershell
py -3.11 -m venv creator\.venv
.\creator\.venv\Scripts\python.exe -m pip install -r creator\requirements.lock.txt
Copy-Item creator\SOUL.md.example creator\SOUL.md
Copy-Item creator\POLICY.md.example creator\POLICY.md
Copy-Item creator\INTERESTS.md.example creator\INTERESTS.md
Copy-Item creator\OWNER_CONTACT.json.example creator\OWNER_CONTACT.json
Copy-Item creator\config.example.json creator\config.json
```

然后运行 `知游工作台.cmd`。首次使用前需要在官方 DSH 应用中安装并启用对应插件，再由用户本人完成平台登录和账号核验。

## 安全说明

人设、联系人、登录态、浏览器目录、草稿和运行日志均为本地数据，已被 `.gitignore` 排除。发布、评论、私信、点赞等对外动作应保持关闭，除非用户已在本机确认账号、内容和操作范围。验证码、Cookie、API Key 和个人会话不得写入仓库或日志。
