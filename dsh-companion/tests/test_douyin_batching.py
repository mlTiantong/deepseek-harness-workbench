from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.platform_service import PlatformService
from dsh_companion.platforms.douyin_im import DouyinIM
from dsh_companion.store import Store


class PrivateBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')

    def add(self, conversation, text, now, account='self'):
        return self.store.ingest_douyin_private_event({'text': text}, account_id=account,
            conversation_id=conversation, external_id=f'{conversation}:{text}', sender_id=conversation, now=now)

    def test_batches_for_ten_seconds_per_conversation_and_keeps_new_messages(self):
        first, job, _ = self.add('A', 'one', 100)
        second, same, _ = self.add('A', 'two', 104)
        other, other_job, _ = self.add('B', 'other chat', 102)
        third, same_again, _ = self.add('A', 'three', 109)
        self.assertEqual(job['id'], same['id'])
        self.assertEqual(job['id'], same_again['id'])
        self.assertNotEqual(job['id'], other_job['id'])
        self.assertEqual(same_again['available_at'], 110)
        self.assertEqual(self.store.ready_by_actor(now=109), [])
        self.assertEqual(self.store.ready_by_actor(now=110)[0]['id'], job['id'])
        started = self.store.start_job(job['id'], now=110)
        self.assertEqual([m['payload']['text'] for m in started['payload']['messages']], ['one', 'two', 'three'])
        newer, following, _ = self.add('A', 'while responding', 111)
        self.assertNotEqual(following['id'], job['id'])
        self.assertEqual(following['available_at'], 121)
        self.store.finish_job(job['id'], result={'ok': True}, now=112)
        self.assertEqual([self.store.get_event(item['id'])['status'] for item in (first, second, third)],
                         ['succeeded'] * 3)
        self.assertEqual(self.store.get_event(newer['id'])['status'], 'queued')
        self.assertEqual(self.store.ready_by_actor(now=112)[0]['id'], other_job['id'])
        self.assertFalse(self.add('A', 'one', 113)[2])
        _, account_job, _ = self.add('A', 'different account', 114, account='other-account')
        self.assertNotEqual(account_job['id'], following['id'])

    def test_recovery_and_cancel_preserve_all_batch_events(self):
        first, job, _ = self.add('A', 'one', 100)
        second, _, _ = self.add('A', 'two', 101)
        self.store.start_job(job['id'], now=110)
        self.store.recover_running(now=111)
        self.assertEqual(self.store.get_event(first['id'])['status'], 'queued')
        self.assertEqual(self.store.get_event(second['id'])['status'], 'queued')
        self.store.cancel_job(job['id'], now=112)
        self.assertEqual(self.store.get_event(second['id'])['status'], 'cancelled')
        self.store.retry_job(job['id'], now=113)
        started = self.store.start_job(job['id'], now=113)
        self.assertEqual([m['payload']['text'] for m in started['payload']['messages']], ['one', 'two'])

    def test_runtime_supplies_every_message_and_image_to_one_turn(self):
        cfg = self.store.get_config('platforms')
        cfg['douyin']['read_message_images'] = True
        self.store.set_config('platforms', cfg)
        self.store.ingest_douyin_private_event({'text': 'hello'}, account_id='self', conversation_id='A',
            external_id='1', sender_id='A', now=100)
        _, job, _ = self.store.ingest_douyin_private_event({'text': '', 'content': {'kind': 'emoji', 'url': 'x'}},
            account_id='self', conversation_id='A', external_id='2', sender_id='A', now=101)
        job = self.store.start_job(job['id'], now=110)
        runtime = DshRuntime(self.store, Path.cwd(), data_dir=Path(self.tmp.name) / 'data')
        captured = {}
        def fake_turn(actor, prompt, **kwargs):
            captured.update(actor=actor, prompt=prompt, kwargs=kwargs)
            return {'ok': True}
        with patch('dsh_companion.dsh_runtime.cache_image', return_value={'type': 'image', 'data': 'image'}), \
                patch.object(runtime, 'run_turn', fake_turn):
            runtime.execute_job_sync(job)
        self.assertEqual(captured['actor'], 'douyin_chat')
        prompt = captured['prompt']
        self.assertEqual(sum(block['type'] == 'image' for block in prompt), 1)
        data = json.loads(prompt[0]['text'].split('工作内容：\n', 1)[1])
        self.assertEqual([m['payload']['text'] for m in data['payload']['messages']], ['hello', ''])
        self.assertEqual(data['event']['external_id'], '2')


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db.sqlite3')

    async def test_private_reply_preserves_the_receiving_account(self):
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True)
        self.store.set_config('platforms', cfg)
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        im = service.adapters['douyin'].im
        im.send_message = AsyncMock(return_value={'sent': True})
        await service.action('douyin', 'send_message', {'account_id': 'second', 'conversation_id': 'chat',
                                                       'recipient_id': 'friend', 'text': 'hello'})
        self.assertEqual(im.send_message.await_args.kwargs['account_id'], 'second')

    async def test_private_image_send_passes_paths_and_consumes_daily_quota(self):
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True)
        self.store.set_config('platforms', cfg)
        self.store.set_config('image_messaging', {
            'enabled': True,
            'daily_limits': {'bilibili': 0, 'douyin': 1, 'xiaohongshu': 0},
        })
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        im = service.adapters['douyin'].im
        im.send_message = AsyncMock(return_value={'sent': True})
        payload = {'account_id': 'second', 'conversation_id': 'chat', 'recipient_id': 'friend',
                   'text': 'hello', 'image_paths': ['C:/content/picture.png']}
        await service.action('douyin', 'send_message', payload)
        self.assertEqual(im.send_message.await_args.kwargs['image_paths'], ['C:/content/picture.png'])
        self.assertEqual(service.image_quota('douyin')['used'], 1)
        with self.assertRaisesRegex(RuntimeError, '图片消息额度不足'):
            await service.action('douyin', 'send_message', {**payload, 'image_paths': ['a.png']})
        self.assertEqual(im.send_message.await_count, 1)

    async def test_private_link_send_passes_share_urls(self):
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True)
        self.store.set_config('platforms', cfg)
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        im = service.adapters['douyin'].im
        im.send_message = AsyncMock(return_value={'sent': True})
        payload = {'account_id': 'second', 'conversation_id': 'chat', 'recipient_id': 'friend',
                   'text': '看这个', 'share_urls': ['https://www.douyin.com/video/123456789']}
        await service.action('douyin', 'send_message', payload)
        self.assertEqual(im.send_message.await_args.kwargs['share_urls'], payload['share_urls'])

    async def test_group_link_send_passes_share_urls(self):
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True, groups=True, group_reply_interval_seconds=0)
        self.store.set_config('platforms', cfg)
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        im = service.adapters['douyin'].im
        im.send_group_message = AsyncMock(return_value={'sent': True})
        payload = {'account_id': 'self', 'conversation_id': 'group', 'text': '',
                   'share_urls': ['https://www.douyin.com/video/123456789']}
        await service.action('douyin', 'send_group_message', payload)
        self.assertEqual(im.send_group_message.await_args.kwargs['share_urls'], payload['share_urls'])

    async def test_group_image_send_passes_paths_and_consumes_daily_quota(self):
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True, groups=True, group_reply_interval_seconds=0)
        self.store.set_config('platforms', cfg)
        self.store.set_config('image_messaging', {
            'enabled': True,
            'daily_limits': {'bilibili': 0, 'douyin': 1, 'xiaohongshu': 0},
        })
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        im = service.adapters['douyin'].im
        im.send_group_message = AsyncMock(return_value={'sent': True})
        payload = {'account_id': 'self', 'conversation_id': 'group', 'text': '群图片',
                   'image_paths': ['C:/content/picture.png']}
        await service.action('douyin', 'send_group_message', payload)
        self.assertEqual(im.send_group_message.await_args.kwargs['image_paths'], payload['image_paths'])
        self.assertEqual(service.image_quota('douyin')['used'], 1)
        with self.assertRaisesRegex(RuntimeError, '图片消息额度不足'):
            await service.action('douyin', 'send_group_message', {**payload, 'conversation_id': 'group-2'})

    async def test_large_group_history_does_not_stop_the_shared_reader(self):
        im = DouyinIM(self.store, Path.cwd())
        stdout = asyncio.StreamReader(limit=65536)
        im.process = SimpleNamespace(stdout=stdout)
        loop = asyncio.get_running_loop()
        history, send = loop.create_future(), loop.create_future()
        im.pending = {1: history, 2: send}
        stdout.feed_data((json.dumps({'id': 1, 'result': 'x' * 100000}) + '\n' +
                          json.dumps({'id': 2, 'result': {'sent': True}}) + '\n').encode())
        stdout.feed_eof()
        await im._read_stdout()
        self.assertEqual(len(await history), 100000)
        self.assertTrue((await send)['sent'])

    async def test_stuck_group_send_times_out_without_blocking_private_sends(self):
        cfg = self.store.get_config('platforms')
        cfg['douyin'].update(enabled=True, groups=True, group_reply_interval_seconds=0)
        self.store.set_config('platforms', cfg)
        im = DouyinIM(self.store, Path.cwd())
        stdout = asyncio.StreamReader()
        class Input:
            group_calls = 0
            def write(self, data):
                request = json.loads(data)
                if request['method'] == 'send_group_message':
                    self.group_calls += 1
                    if self.group_calls == 1:
                        return
                stdout.feed_data((json.dumps({'id': request['id'], 'result': {'sent': True}}) + '\n').encode())
            async def drain(self):
                pass
        im.process = SimpleNamespace(returncode=None, stdin=Input(), stdout=stdout)
        im.reader = asyncio.create_task(im._read_stdout())
        service = PlatformService(self.store, Path.cwd(), SimpleNamespace(notify=lambda: None))
        service.adapters['douyin'] = im
        group = {'account_id': 'self', 'conversation_id': 'group', 'text': 'hello'}
        private = {'account_id': 'self', 'conversation_id': 'private', 'text': 'hello'}
        with patch('dsh_companion.platforms.douyin_im.IM_CALL_TIMEOUT', 0.05):
            stuck = asyncio.create_task(service.action('douyin', 'send_group_message', group))
            await asyncio.sleep(0)
            self.assertTrue((await service.action('douyin', 'send_message', private))['result']['sent'])
            with self.assertRaisesRegex(TimeoutError, 'send_group_message'):
                await stuck
            self.assertTrue((await service.action('douyin', 'send_group_message', group))['result']['sent'])
        stdout.feed_eof()
        await im.reader
        with self.store.connect() as db:
            actions = db.execute("SELECT status FROM actions WHERE kind='send_group_message' ORDER BY created_at").fetchall()
        self.assertEqual([a['status'] for a in actions], ['failed', 'succeeded'])


if __name__ == '__main__':
    unittest.main()
