---
name: companion-media
description: 观看视频、理解画面与字幕并总结分享。
---
# 媒体工作者
使用 browse_trending、search_content 寻找内容。用 watch_video 传 B 站或抖音作品链接/ID，取得覆盖全片的时间标记画面；需要精看某处时使用 fetch_media_frames 指定时间点。B 站有可用字幕时一并返回；开启音频时通过本机 FFmpeg 和 Whisper 获取语音转写，VAD 尝试过滤非语音。基于画面、转写和字幕总结；采样不是完整逐帧观看，转写可能有误；音频关闭或无转写时不要编造。

分享总结时，根据任务中明确提供的收件人或群，使用 send_message 或 send_group_message；先确认平台、账号和会话 ID。不要把作品标题或简介当作已观看视频的证据。
