from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image

from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.message_media import cache_image, normalize_message_content, visual_blocks_from_path
from dsh_companion.platform_service import PlatformService
from dsh_companion.platforms.bilibili import BilibiliAdapter
from dsh_companion.platforms.douyin_im import DouyinIM
from dsh_companion.store import Store


class GroupQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'companion.sqlite3')
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True, groups=True, group_reply_interval_seconds=300)
        self.store.set_config('platforms', cfg)

    def add(self, group, message, when, sender='anyone'):
        return self.store.ingest_group_event('douyin', {'text': message}, account_id='self',
            conversation_id=group, external_id=f'{group}:{message}', sender_id=sender, now=when)

    def test_accumulates_messages_and_rate_limits_each_group(self):
        first, job, created = self.add('A', 'one', 100)
        _, same, _ = self.add('A', '@two', 101, sender='someone_else')
        self.assertEqual(job['id'], same['id'])
        _, other, _ = self.add('B', 'different group', 102)
        running = self.store.start_job(job['id'], now=105)
        self.assertEqual([e['payload']['text'] for e in running['payload']['messages']], ['one', '@two'])
        _, following, _ = self.add('A', 'arrived while running', 106)
        self.store.record_group_sent('douyin', 'self', 'A', now=110)
        self.store.finish_job(job['id'], result={'ok': True}, now=111)
        ready = self.store.ready_by_actor(now=111)
        self.assertEqual([j['id'] for j in ready], [other['id']])
        self.store.start_job(other['id'], now=111)
        self.store.finish_job(other['id'], now=112)
        self.assertEqual(self.store.ready_by_actor(now=409), [])
        self.assertEqual(self.store.ready_by_actor(now=410)[0]['id'], following['id'])
        batch = self.store.start_job(following['id'], now=410)['payload']['messages']
        self.assertEqual([e['payload']['text'] for e in batch], ['arrived while running'])
        self.assertEqual(self.store.get_event(first['id'])['status'], 'succeeded')

    def test_disabled_and_main_delegation(self):
        task = self.store.create_group_activity('douyin', 'A', '主动聊一个话题', account_id='self', available_at=100)
        self.store.record_group_sent('douyin', 'self', 'A', now=100)
        self.assertFalse(self.store.ready_by_actor(now=399))
        self.assertEqual(self.store.ready_by_actor(now=400)[0]['id'], task['id'])
        cfg = self.store.get_config('platforms'); cfg['douyin']['groups'] = False
        self.store.set_config('platforms', cfg)
        self.assertEqual(self.store.ready_by_actor(now=401), [])

    def test_recovery_and_dedup(self):
        event, job, _ = self.add('A', 'one', 100)
        _, _, created = self.add('A', 'one', 101)
        self.assertFalse(created)
        self.store.start_job(job['id'], now=102)
        self.assertEqual(self.store.get_event(event['id'])['status'], 'running')
        self.store.recover_running(now=103)
        self.assertEqual(self.store.get_event(event['id'])['status'], 'queued')
        self.assertEqual(len(self.store.start_job(job['id'], now=104)['payload']['messages']), 1)

    def test_passive_message_not_stopped_by_autonomy_pause(self):
        task = self.store.create_group_activity('douyin', 'A', '主动聊', account_id='self', available_at=100)
        _, passive, _ = self.add('A', 'new message', 101)
        self.assertNotEqual(passive['id'], task['id'])
        self.assertEqual(self.store.ready_by_actor(now=102, autonomous_paused=True)[0]['id'], passive['id'])


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        image = Image.new('RGB', (3, 3), 'red'); output = io.BytesIO(); image.save(output, format='PNG')
        self.png = output.getvalue()

    def test_cache_image_and_decrypt_image_and_emoji(self):
        key = bytes(range(32)); nonce = bytes(range(12))
        encrypted = nonce + AESGCM(key).encrypt(nonce, self.png, None)
        def handler(request):
            return httpx.Response(200, content=encrypted if request.url.path == '/encrypted' else self.png)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            content = {'kind': 'image', 'image': {'originUrls': ['https://images.test/encrypted'], 'skey': key.hex()}}
            block = cache_image(content, Path(self.tmp.name), index=1, client=client)
            self.assertEqual(block['mimeType'], 'image/png')
            self.assertEqual(base64.b64decode(block['data']), self.png)
            emoji = cache_image({'kind': 'emoji', 'url': 'https://images.test/emoji'},
                                Path(self.tmp.name), index=2, client=client)
            self.assertEqual(base64.b64decode(emoji['data']), self.png)

    def test_sticker_uri_is_not_a_download_url_and_old_messages_are_repaired(self):
        payload={'content':{'kind':'emoji','url':'tos-cn-i/sticker-id'}, 'raw_content':json.dumps({
            'url':{'uri':'tos-cn-i/sticker-id','url_list':['https://cdn.test/bad','https://cdn.test/good']}})}
        normalized=normalize_message_content(payload)
        self.assertEqual(normalized['url'],'https://cdn.test/bad')
        self.assertEqual(payload['content']['url'],'tos-cn-i/sticker-id')
        requested=[]
        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(403) if request.url.path=='/bad' else httpx.Response(200,content=self.png)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            block=cache_image(normalized,Path(self.tmp.name),index=1,client=client)
        self.assertEqual(requested,['https://cdn.test/bad','https://cdn.test/good'])
        self.assertEqual(base64.b64decode(block['data']),self.png)

    def test_animated_stickers_sample_the_whole_clip_as_png(self):
        frames=[Image.new('RGB',(30,30),color) for color in ('red','green','blue','yellow','purple')]
        path=Path(self.tmp.name)/'sticker.gif'
        frames[0].save(path,save_all=True,append_images=frames[1:],duration=[500,100,100,100,500],loop=0)
        blocks=visual_blocks_from_path(path)
        images=[block for block in blocks if block['type']=='image']
        self.assertGreaterEqual(len(images),2)
        self.assertLessEqual(len(images),4)
        self.assertTrue(all(block['mimeType']=='image/png' for block in images))
        first=Image.open(io.BytesIO(base64.b64decode(images[0]['data']))).convert('RGB')
        last=Image.open(io.BytesIO(base64.b64decode(images[-1]['data']))).convert('RGB')
        self.assertEqual(first.getpixel((0,0)),(255,0,0))
        self.assertEqual(last.getpixel((0,0)),(128,0,128))

    def test_history_only_images_are_saved_and_sent_across_multiple_jobs(self):
        path=Path(self.tmp.name)/'photo.png';path.write_bytes(self.png)
        store=Store(Path(self.tmp.name)/'db.sqlite3')
        runtime=DshRuntime(store,Path.cwd(),data_dir=Path(self.tmp.name)/'data')
        for number in range(2):
            job={'id':f'job-{number}','event_id':None,'actor_id':'douyin_group','kind':'group_interaction',
                 'payload':{'platform':'douyin','messages':[{'platform':'douyin','sender_id':'other',
                     'payload':{'content':{'kind':'image','path':str(path)}}}]}}
            with patch.object(runtime,'run_turn',return_value={'ok':True}) as turn:
                runtime.execute_job_sync(job)
                self.assertEqual(len([b for b in turn.call_args.args[1] if b.get('type')=='image']),1)
        saved=store.list_media()
        self.assertEqual(len(saved),2)
        self.assertEqual({row['event_id'] for row in saved},{'job:job-0','job:job-1'})
        self.assertTrue(all(Path(row['path']).exists() for row in saved))

    def test_runtime_passes_image_blocks_and_removes_temporary_files(self):
        store = Store(Path(self.tmp.name) / 'db.sqlite3')
        cfg = store.get_config('platforms'); cfg['douyin']['read_message_images'] = True; store.set_config('platforms', cfg)
        event, job, _ = store.ingest_event('douyin', 'message', {'text': '',
            'content': {'kind': 'emoji', 'url': 'https://images.test/emoji'}},
            account_id='self', conversation_id='DM', sender_id='other', external_id='1')
        runtime = DshRuntime(store, Path.cwd(), data_dir=Path(self.tmp.name) / 'data')
        observed = {}
        def fake_cache(content, folder, *, index):
            (folder / 'cache.png').write_bytes(self.png)
            return {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(self.png).decode()}
        def fake_turn(actor, prompt, **kwargs):
            observed['prompt'] = prompt
            observed['cache_paths'] = list((Path(self.tmp.name) / 'data' / 'message-media-cache').rglob('cache.png'))
            return {'ok': True}
        with patch('dsh_companion.dsh_runtime.cache_image', fake_cache), patch.object(runtime, 'run_turn', fake_turn):
            runtime.execute_job_sync(job)
        self.assertEqual(observed['prompt'][-1]['type'], 'image')
        self.assertEqual(len(observed['cache_paths']), 1)
        self.assertFalse(observed['cache_paths'][0].exists())


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True, groups=True, group_started_at=100, group_reply_interval_seconds=0.02)
        cfg['bilibili'].update(enabled=True, groups=True)
        self.store.set_config('platforms', cfg)

    async def test_douyin_group_catchup_processes_every_message(self):
        im = DouyinIM(self.store, Path.cwd())
        history = [{'platform': 'douyin', 'event_type': 'group_message', 'account_id': 'self',
            'external_id': f'group:{i}', 'conversation_id': 'group', 'sender_id': f'user{i}',
            'sender_name': '', 'occurred_at': 100+i, 'payload': {'text': str(i)}} for i in range(3)]
        im.list_joined_groups = AsyncMock(return_value=[{'account_id': 'self', 'conversation_id': 'group'}])
        im.call = AsyncMock(return_value=history)
        self.assertEqual(len(await im.poll_groups()), 3)
        self.assertEqual(len(await im.poll_groups()), 0)
        batch = self.store.start_job(self.store.list_jobs()[0]['id'])['payload']['messages']
        self.assertEqual([item['payload']['text'] for item in batch], ['0', '1', '2'])

    async def test_group_send_interval_and_opt_in(self):
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        send = AsyncMock(return_value={'status': 'ok'})
        service.adapters['douyin'] = SimpleNamespace(send_group_message=send)
        payload = {'account_id': 'self', 'conversation_id': 'g', 'text': 'hi'}
        await service.action('douyin', 'send_group_message', payload)
        await service.action('douyin', 'send_group_message', payload)
        self.assertEqual(send.await_count, 2)
        cfg = self.store.get_config('platforms'); cfg['douyin']['groups'] = False
        self.store.set_config('platforms', cfg)
        with self.assertRaisesRegex(RuntimeError, '尚未启用'):
            await service.action('douyin', 'send_group_message', payload)

    async def test_share_link_is_sent_as_text_and_recorded(self):
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        send = AsyncMock(return_value={'status': 'ok'})
        service.adapters['douyin'] = SimpleNamespace(send_group_message=send)
        text = '这条有意思 https://www.douyin.com/video/1234567890123456789'
        payload = {'account_id': 'self', 'conversation_id': 'g', 'text': text}
        result = await service.action('douyin', 'send_group_message', payload)
        send.assert_awaited_once_with(**payload)
        action = self.store.get_action(result['action_id'])
        self.assertEqual(action['status'], 'succeeded')
        self.assertEqual(action['payload']['text'], text)

    async def test_bilibili_private_and_group_picture_payloads_are_visual(self):
        adapter=BilibiliAdapter(self.store,Path.cwd(),directory=Path(self.tmp.name))
        adapter._credential=lambda:SimpleNamespace(dedeuserid='99')
        messages=[{'msg_seqno':i,'sender_uid':88,'msg_type':kind,'timestamp':i,'msg_key':i,
                   'content':json.dumps({'url':'//cdn.test/photo.png','width':3,'height':3})}
                  for i,kind in ((11,2),(12,6))]
        page={'messages':messages,'has_more':False,'min_seqno':11}
        for session_type in (1,2):
            adapter._sessions=AsyncMock(return_value=[{'talker_id':123,'session_type':session_type,
                'ack_seqno':10,'max_seqno':12,'at_seqno':12}])
            with patch('dsh_companion.platforms.bilibili.session.fetch_session_msgs',AsyncMock(return_value=page)):
                events=await (adapter.poll_messages() if session_type==1 else adapter.poll_groups())
                history=await adapter.get_group_history(account_id='99',conversation_id='123')
            self.assertEqual(len(events),2)
            for event in events+history:
                self.assertEqual(event['payload']['content']['kind'],'image')
                self.assertEqual(event['payload']['content']['image']['originUrls'],['https://cdn.test/photo.png'])

    async def test_bilibili_groups_fetch_session_type_two(self):
        adapter = BilibiliAdapter(self.store, Path.cwd(), directory=Path(self.tmp.name))
        adapter._credential = lambda: SimpleNamespace(dedeuserid='self')
        adapter._sessions = AsyncMock(return_value=[{'talker_id': 123, 'session_type': 2,
            'ack_seqno': 10, 'max_seqno': 12, 'group_name': '测试', 'at_seqno': 12}])
        page = {'messages': [{'msg_seqno': i, 'sender_uid': 88, 'msg_type': 1,
            'timestamp': i, 'msg_key': i, 'content': json.dumps({'content': str(i)})} for i in (11, 12)],
            'has_more': False, 'min_seqno': 11}
        with patch('dsh_companion.platforms.bilibili.session.fetch_session_msgs', AsyncMock(return_value=page)) as fetch:
            events = await adapter.poll_groups()
        self.assertEqual(len(events), 2)
        self.assertEqual(fetch.await_args.kwargs['session_type'], 2)
        job = self.store.list_jobs()[0]
        messages = self.store.start_job(job['id'])['payload']['messages']
        self.assertTrue(messages[-1]['payload']['is_mention_me'])


if __name__ == '__main__':
    unittest.main()
