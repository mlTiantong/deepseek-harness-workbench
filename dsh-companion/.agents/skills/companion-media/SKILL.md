---
name: companion-media
description: 媒体浏览与视频帧理解的预留工作者接口。
---
# 媒体工作者
使用 browse_trending 浏览热门内容，用 search_content 搜索作品，再通过 get_content、get_content_comments 阅读作品介绍及评论；有价值的发现可写入记忆流水或转交主会话。抖音热榜返回话题；传入话题 ID 或关键词可获取相关作品。

通用视频帧接口尚未接入；没有取得真实画面时不要声称已经看过画面。

抖音可使用 browse_douyin_recommended 读取推荐流，使用 inspect_douyin_video 看最多三帧真实画面；没有音频和完整视频理解。本人作品通过 list_my_douyin_posts 获取，不要把管理页当分享链接。
