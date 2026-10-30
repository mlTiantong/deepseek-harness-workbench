"""B 站适配器：项目专属扫码凭据、消息与评论监听、回复和发布。"""
from __future__ import annotations

import asyncio
import json
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import qrcode

from bilibili_api import comment, dynamic, hot, login_v2, search, session, user, video, video_uploader
from bilibili_api.exceptions import ResponseCodeException
from bilibili_api.utils.network import Api, Credential
from bilibili_api.utils.picture import Picture

from ..store import Store


class BilibiliAdapter:
    name = "bilibili"

    def __init__(self, store: Store, root: str | Path, *, directory: str | Path | None = None):
        self.store = store
        self.root = Path(root).resolve()
        self.directory = Path(directory).resolve() if directory else self.root / "var/platforms/bilibili"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.credentials_file = self.directory / "credential.json"
        self.qr_key: str | None = None

    def _credential(self) -> Credential:
        return Credential(**json.loads(self.credentials_file.read_text(encoding="utf-8")))

    async def begin_login(self) -> dict[str, Any]:
        """新建二维码；扫码账号只保存到本项目目录。"""
        data = await Api(**login_v2.API["qrcode"]["web"]["get_qrcode_and_token"],
                         credential=Credential()).result
        self.qr_key = data["qrcode_key"]
        image = self.directory / "login-qr.png"
        qrcode.make(data["url"]).save(image)
        return {"platform": self.name, "state": "scan", "qr_image": str(image)}

    async def login_status(self) -> dict[str, Any]:
        if self.qr_key is None:
            return {"platform": self.name, "state": "not_started"}
        events = await Api(**login_v2.API["qrcode"]["web"]["get_events"],
                           credential=Credential()).update_params(qrcode_key=self.qr_key).result
        code = events["code"]
        if code == 86101:
            return {"platform": self.name, "state": "scan"}
        if code == 86090:
            return {"platform": self.name, "state": "confirm"}
        self.qr_key = None
        if code == 86038:
            return {"platform": self.name, "state": "timeout"}
        if code != 0:
            return {"platform": self.name, "state": "error", "error": events["message"]}

        # 新版扫码结果是跨域票据，登录 cookie 在首次跳转响应头中。
        login_url = events["url"]
        cookies = dict(parse_qsl(urlsplit(login_url).query))
        if not cookies.get("SESSDATA"):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Referer": "https://passport.bilibili.com/",
            }
            async with httpx.AsyncClient(headers=headers, follow_redirects=False, timeout=30) as client:
                response = await client.get(login_url)
            if response.status_code >= 400:
                raise RuntimeError(f"B 站跨域登录失败，HTTP {response.status_code}")
            cookies = {}
            for value in response.headers.get_list("set-cookie"):
                parsed = SimpleCookie()
                parsed.load(value)
                cookies.update({key: item.value for key, item in parsed.items()})
        credential = Credential(sessdata=cookies.get("SESSDATA"), bili_jct=cookies.get("bili_jct"),
                                dedeuserid=cookies.get("DedeUserID"),
                                ac_time_value=events.get("refresh_token"))
        if not credential.sessdata or not credential.bili_jct:
            return {"platform": self.name, "state": "error",
                    "error": "扫码已确认，但未获取到完整登录凭据，请重新扫码。"}
        info = await user.get_self_info(credential)
        credential.dedeuserid = str(info["mid"])
        keys = ("sessdata", "bili_jct", "buvid3", "buvid4", "dedeuserid", "ac_time_value")
        self.credentials_file.write_text(json.dumps({key: getattr(credential, key) for key in keys},
                                                     ensure_ascii=False), encoding="utf-8")
        return {"platform": self.name, "state": "done", "account_id": credential.dedeuserid,
                "nickname": info["name"]}

    async def check_auth(self) -> dict[str, Any]:
        if not self.credentials_file.exists():
            return {"platform": self.name, "authenticated": False, "reason": "尚未在本项目登录"}
        credential = self._credential()
        if not credential.sessdata:
            return {"platform": self.name, "authenticated": False, "reason": "未获取有效登录凭据，请重新扫码"}
        try:
            info = await user.get_self_info(credential)
        except ResponseCodeException as exc:
            if exc.code == -101:
                return {"platform": self.name, "authenticated": False, "reason": "登录已失效"}
            raise
        return {"platform": self.name, "authenticated": True,
                "account_id": str(info["mid"]), "nickname": info["name"]}

    async def _sessions(self, credential: Credential) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        end_ts = None
        while True:
            params: dict[str, Any] = {"session_type": 4, "group_fold": 0, "unfollow_fold": 0,
                                      "sort_rule": 2, "size": 100, "build": 0, "mobi_app": "web"}
            if end_ts is not None:
                params["end_ts"] = end_ts
            page = await Api(**session.API["session"]["get"], credential=credential).update_params(**params).result
            items = page.get("session_list") or []
            result.extend(items)
            if not page["has_more"] or not items:
                return result
            end_ts = items[-1]["session_ts"]

    async def poll_messages(self) -> list[dict[str, Any]]:
        credential = self._credential()
        me = int(credential.dedeuserid or (await user.get_self_info(credential))["mid"])
        state = self.store.get_config("bilibili_message_cursors") or {}
        slots = asyncio.Semaphore(8)

        async def collect(conversation: dict[str, Any]) -> tuple[int, dict[str, Any], list[dict[str, Any]], int]:
            talker = int(conversation["talker_id"])
            last_seen = int(state.get(str(talker), conversation["ack_seqno"]))
            messages: list[dict[str, Any]] = []
            if int(conversation["max_seqno"]) > last_seen:
                async with slots:
                    begin_seqno = 0
                    while True:
                        page = await session.fetch_session_msgs(talker, credential, begin_seqno=begin_seqno)
                        batch = page.get("messages") or []
                        messages.extend(message for message in batch if int(message["msg_seqno"]) > last_seen)
                        if not page["has_more"] or not batch or int(page["min_seqno"]) <= last_seen:
                            break
                        begin_seqno = int(page["min_seqno"])
            return talker, conversation, messages, last_seen

        conversations = [item for item in await self._sessions(credential)
                         if item["session_type"] == 1 and not item.get("system_msg_type", 0)]
        results = await asyncio.gather(*(collect(conversation) for conversation in conversations))
        staged: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for talker, conversation, messages, last_seen in results:
            staged.extend((talker, conversation, message) for message in messages if int(message["sender_uid"]) != me)
            state[str(talker)] = max([last_seen, *[int(message["msg_seqno"]) for message in messages]])
        events = []
        for talker, conversation, message in sorted(staged, key=lambda row: (int(row[2]["timestamp"]), int(row[2]["msg_seqno"]))):
            body = json.loads(message["content"])
            text = body.get("content", "") if isinstance(body, dict) else str(body)
            payload = {"text": text, "message_type": message["msg_type"],
                       "message": message, "conversation": conversation}
            event, _, created = self.store.ingest_event(
                self.name, "message", payload, account_id=str(me),
                external_id=f"{talker}:{message['msg_key']}", conversation_id=str(talker),
                sender_id=str(message["sender_uid"]), occurred_at=float(message["timestamp"]))
            if created:
                events.append(event)
        self.store.set_config("bilibili_message_cursors", state)
        return events

    async def list_joined_groups(self) -> list[dict[str, Any]]:
        credential = self._credential()
        account_id = str(credential.dedeuserid or (await user.get_self_info(credential))["mid"])
        return [{"platform": self.name, "account_id": account_id, "conversation_id": str(item["talker_id"]),
                 "name": item.get("group_name", "")} for item in await self._sessions(credential)
                if item["session_type"] == 2 and not item.get("system_msg_type", 0)]

    async def get_group_history(self, *, account_id: str, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        page = await session.fetch_session_msgs(int(conversation_id), self._credential(), session_type=2)
        result = []
        for message in sorted(page.get("messages") or [], key=lambda item: int(item["msg_seqno"]))[-limit:]:
            body = json.loads(message["content"])
            result.append({"platform": self.name, "account_id": account_id, "conversation_id": conversation_id,
                           "sender_id": str(message["sender_uid"]), "occurred_at": message["timestamp"],
                           "payload": {"text": body.get("content", "") if isinstance(body, dict) else str(body),
                                       "message_type": message["msg_type"], "message": message}})
        return result

    async def poll_groups(self) -> list[dict[str, Any]]:
        credential = self._credential()
        account_id = str(credential.dedeuserid or (await user.get_self_info(credential))["mid"])
        state = self.store.get_config("bilibili_group_cursors") or {}
        started_at = float(self.store.get_config("platforms")["bilibili"].get("group_started_at") or 0)
        groups = [item for item in await self._sessions(credential)
                  if item["session_type"] == 2 and not item.get("system_msg_type", 0)]
        result = []
        for group in groups:
            group_id = str(group["talker_id"])
            last_seen = int(state.get(group_id, group.get("ack_seqno", 0)))
            messages = []
            if int(group.get("max_seqno", 0)) > last_seen:
                begin = 0
                while True:
                    page = await session.fetch_session_msgs(int(group_id), credential, session_type=2, begin_seqno=begin)
                    batch = page.get("messages") or []
                    messages.extend(item for item in batch if int(item["msg_seqno"]) > last_seen)
                    if not page["has_more"] or not batch or int(page["min_seqno"]) <= last_seen:
                        break
                    begin = int(page["min_seqno"])
            for message in sorted(messages, key=lambda item: int(item["msg_seqno"])):
                if str(message["sender_uid"]) == account_id or float(message["timestamp"]) < started_at:
                    continue
                body = json.loads(message["content"])
                text = body.get("content", "") if isinstance(body, dict) else str(body)
                payload = {"text": text, "message_type": message["msg_type"], "message": message,
                           "group_name": group.get("group_name", ""),
                           "is_mention_me": int(message["msg_seqno"]) == int(group.get("at_seqno") or 0)}
                event, _, created = self.store.ingest_group_event(
                    self.name, payload, account_id=account_id, conversation_id=group_id,
                    external_id=f"{group_id}:{message['msg_key']}", sender_id=str(message["sender_uid"]),
                    occurred_at=float(message["timestamp"]))
                if created:
                    result.append(event)
            state[group_id] = max([last_seen, *[int(item["msg_seqno"]) for item in messages]])
            self.store.set_config("bilibili_group_cursors", state)
        return result

    async def send_group_message(self, *, conversation_id: str, text: str, account_id: str = "") -> dict[str, Any]:
        credential = self._credential()
        me = int(credential.dedeuserid or (await user.get_self_info(credential))["mid"])
        data = {"msg[sender_uid]": me, "msg[receiver_id]": int(conversation_id), "msg[receiver_type]": 2,
                "msg[msg_type]": session.EventType.TEXT.value, "msg[msg_status]": 0,
                "msg[content]": json.dumps({"content": text}, ensure_ascii=False),
                "msg[dev_id]": "A6716E9A-7CE3-47AF-994B-F0B34178D28D", "msg[new_face_version]": 0,
                "msg[timestamp]": int(time.time()), "from_filework": 0, "build": 0, "mobi_app": "web"}
        return await (Api(**session.API["operate"]["send_msg"], credential=credential)
                      .update_params(w_sender_uid=me, w_receiver_id=int(conversation_id)).update_data(**data).result)

    async def send_message(self, *, conversation_id: str, text: str, recipient_id: str = "") -> dict[str, Any]:
        return await session.send_msg(self._credential(), int(recipient_id or conversation_id), session.EventType.TEXT, text)

    async def poll_comments(self) -> list[dict[str, Any]]:
        credential = self._credential()
        account_id = str(credential.dedeuserid or (await user.get_self_info(credential))["mid"])
        events = []
        for event_type, fetch, time_key in (("comment", session.get_replies, "reply_time"),
                                             ("mention", session.get_at, "at_time")):
            key = "bilibili_" + event_type + "_cursor"
            stored = self.store.get_config(key)
            first = await fetch(credential)
            since = float(stored if stored is not None else first.get("last_view_at", 0))
            newest = since
            page = first
            while True:
                items = page.get("items") or []
                for item in reversed(items):
                    occurred = float(item[time_key])
                    if occurred < since:
                        continue
                    person = item["user"]
                    detail = item["item"]
                    comment_id = str(detail["source_id"])
                    payload = {"text": detail.get("source_content", ""), "comment_id": comment_id,
                               "content_id": str(detail["subject_id"]), "resource_type": detail["business_id"],
                               "root_comment_id": str(detail.get("root_id") or comment_id), "notification": item}
                    event, _, created = self.store.ingest_event(
                        self.name, event_type, payload, account_id=account_id,
                        external_id=str(item["id"]), sender_id=str(person["mid"]),
                        sender_name=person["nickname"], occurred_at=occurred)
                    if created:
                        events.append(event)
                    newest = max(newest, occurred)
                cursor = page.get("cursor") or {}
                if cursor.get("is_end", True) or not items or min(float(item[time_key]) for item in items) < since:
                    break
                if event_type == "comment":
                    page = await fetch(credential, last_reply_id=cursor["id"], reply_time=cursor["time"])
                else:
                    page = await fetch(credential, last_id=cursor["id"], at_time=cursor["time"])
            self.store.set_config(key, newest)
        return events

    async def reply_comment(self, *, comment_id: str, text: str, content_id: str = "",
                            resource_type: int = 1, root_comment_id: str = "") -> dict[str, Any]:
        root = int(root_comment_id or comment_id)
        return await comment.send_comment(text, int(content_id), comment.CommentResourceType(resource_type),
                                          root=root, parent=int(comment_id), credential=self._credential())

    async def publish_content(self, *, title: str, content: str = "", files: list[str] | None = None,
                              tags: list[str] | None = None, content_type: str = "video",
                              metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        files, tags, metadata = files or [], tags or [], metadata or {}
        credential = self._credential()
        if content_type in ("text", "image"):
            text = "\n".join(part for part in [content or title, " ".join("#" + tag.strip("#") for tag in tags)] if part)
            draft = dynamic.BuildDynamic.empty().add_text(text)
            if content_type == "image":
                draft.add_image([Picture.from_file(path) for path in files])
            return await dynamic.send_dynamic(draft, credential)
        if content_type == "video":
            meta = video_uploader.VideoMeta(tid=int(metadata["tid"]), title=title, desc=content,
                                             cover=metadata.get("cover", ""), tags=tags,
                                             original=metadata["original"])
            pages = [video_uploader.VideoUploaderPage(path, title) for path in files]
            upload = video_uploader.VideoUploader(pages, meta, credential, cover=metadata.get("cover", ""))
            return await upload.start()
        raise ValueError(f"B 站不支持的内容类型：{content_type}")

    async def browse_trending(self, *, limit: int = 10, trend_id: str = "", keyword: str = "") -> dict[str, Any]:
        if keyword:
            return await self.search_content(keyword=keyword, limit=limit)
        return await hot.get_hot_videos(pn=1, ps=limit)

    async def search_content(self, *, keyword: str, limit: int = 10, order: str = "general") -> dict[str, Any]:
        ordering = {"general": search.OrderVideo.TOTALRANK, "latest": search.OrderVideo.PUBDATE,
                    "popular": search.OrderVideo.CLICK}[order]
        return await search.search_by_type(keyword, search_type=search.SearchObjectType.VIDEO,
                                           order_type=ordering, page=1, page_size=limit)

    async def get_content(self, *, content_id: str, resource_type: int = 1) -> dict[str, Any]:
        credential = self._credential() if self.credentials_file.exists() else None
        if resource_type in (11, 17):
            return await dynamic.Dynamic(int(content_id), credential=credential).get_info()
        target = (video.Video(bvid=content_id, credential=credential) if content_id.upper().startswith("BV")
                  else video.Video(aid=int(content_id), credential=credential))
        return await target.get_info()

    async def get_content_comments(self, *, content_id: str, resource_type: int = 1,
                                   cursor: str = "", limit: int = 20) -> dict[str, Any]:
        credential = self._credential() if self.credentials_file.exists() else None
        oid = video.Video(bvid=content_id).get_aid() if content_id.upper().startswith("BV") else int(content_id)
        return await comment.get_comments_lazy(oid, comment.CommentResourceType(resource_type),
                                               offset=cursor, credential=credential)

    async def fetch_media_frames(self, *, content_id: str, timestamps: list[float]) -> dict[str, Any]:
        raise NotImplementedError("视频帧接口已预留，当前尚未接入")
