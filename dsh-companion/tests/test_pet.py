from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from dsh_companion.app import create_app
from dsh_companion.pet import desktop_status, DesktopPet
from dsh_companion.desktop_pet.animation import status_animation

ROOT=Path(__file__).resolve().parents[1]


class PetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.app=create_app(ROOT,data_dir=self.root)
        self.companion=self.app.state.companion
        self.client=TestClient(self.app)

    def test_live_role_tool_and_pause_state(self):
        store=self.companion.store
        self.assertEqual(self.client.get('/api/pet').json()['state'],'idle')
        job=store.create_job('creator','creation',{'instruction':'做一份夜景拍摄指南'})
        store.start_job(job['id'])
        self.assertEqual(desktop_status(self.companion)['title'],'大肥鱼在创作')
        callback=self.companion.runtime._usage_recorder('creator','session','',job['id'])
        callback(SimpleNamespace(method='session.event',payload={'event':{
            'type':'tool/call','data':{'name':'mcp__companion__generate_image','callId':'call'}}}))
        state=self.client.get('/api/pet').json()
        self.assertEqual(state['title'],'大肥鱼在画图')
        self.assertEqual(state['action'],'draw')
        self.assertEqual(state['activeCount'],1)
        self.assertEqual(state['todos'][0]['status'],'in_progress')
        callback(SimpleNamespace(method='session.event',payload={'event':{'type':'tool/result','data':{}}}))
        store.finish_job(job['id'],result={'response':'封面已完成'})
        self.assertEqual(desktop_status(self.companion)['state'],'completed')
        self.client.post('/api/control/pause')
        self.assertEqual(desktop_status(self.companion)['state'],'paused')
        self.client.post('/api/control/resume')
        self.assertFalse(desktop_status(self.companion)['paused'])

    async def test_reading_phase_is_visible_while_comments_are_being_fetched(self):
        store=self.companion.store
        media=store.get_config('media');media['enabled']=True;store.set_config('media',media)
        platforms=store.get_config('platforms');platforms['douyin']['enabled']=True;store.set_config('platforms',platforms)
        adapter=self.companion.platforms.adapters['douyin']
        adapter.fetch_media_frames=AsyncMock(return_value={'content_id':'123','duration':1,
            'frames':[],'audio_status':'disabled'})
        entered=asyncio.Event();release=asyncio.Event()
        async def comments(**kwargs):
            entered.set();await release.wait();return {'items':[],'count':0}
        adapter.get_content_comments=comments
        task=asyncio.create_task(self.companion.platforms.fetch_media_frames('douyin',content_id='123'))
        await entered.wait()
        self.assertEqual(desktop_status(self.companion)['title'],'大肥鱼在读评论')
        self.assertEqual(desktop_status(self.companion)['action'],'read')
        release.set();await task
        self.assertIsNone(self.companion.platforms.media_activity)

    def test_real_tool_events_select_distinct_animations(self):
        store = self.companion.store
        job = store.create_job('main', 'autonomy', {})
        store.start_job(job['id'])
        callback = self.companion.runtime._usage_recorder('main', 'session', '', job['id'])
        for tool, action in [('send_message','reply'), ('send_group_message','reply'),
                             ('reply_comment','reply'), ('watch_video','watch'),
                             ('browse_douyin_recommended','watch'), ('get_content_comments','read'),
                             ('write','write'), ('edit','write'), ('shell','code'),
                             ('generate_image','draw'), ('get_decision_context','think'),
                             ('search_content','search'), ('publish_content','organize')]:
            with self.subTest(tool=tool):
                callback(SimpleNamespace(method='session.event', payload={'event': {
                    'type':'tool/call', 'data':{'name':'mcp__companion__'+tool, 'callId':tool}}}))
                status = self.client.get('/api/pet').json()
                self.assertEqual(status['action'], action)
                self.assertEqual(status_animation(status), action)
                callback(SimpleNamespace(method='session.event', payload={'event': {
                    'type':'tool/result', 'data':{'callId':tool}}}))
        self.assertEqual(desktop_status(self.companion)['action'], 'think')
        store.finish_job(job['id'], result={})
        self.assertEqual(desktop_status(self.companion)['action'], 'celebrate')

    def test_reply_role_and_user_chat_stay_visible_without_a_tool(self):
        store = self.companion.store
        background = store.create_job('creator', 'creation', {})
        store.start_job(background['id'])
        self.assertEqual(desktop_status(self.companion)['action'], 'write')
        chat = store.create_job('main', 'manual', {'message':'你好'})
        store.start_job(chat['id'])
        status = desktop_status(self.companion)
        self.assertEqual(status['action'], 'reply')
        self.assertEqual(status['currentTask'], '陪你聊天')
        self.assertEqual([todo['action'] for todo in status['todos']], ['write','reply'])
        self.companion.platforms.media_activity = {'phase':'frames','platform':'douyin','content_id':'1'}
        self.assertEqual(desktop_status(self.companion)['action'], 'watch')
        self.companion.platforms.media_activity = None

    def test_start_stop_persists_autostart_and_reuses_project_process(self):
        process=MagicMock();process.poll.return_value=None
        with patch('dsh_companion.pet.subprocess.Popen',return_value=process) as launch:
            self.assertTrue(self.client.post('/api/pet/start').json()['running'])
            self.client.post('/api/pet/start')
            self.assertEqual(launch.call_count,1)
            args=launch.call_args
            self.assertIn('dsh_companion.desktop_pet.window',args.args[0])
            self.assertEqual(args.kwargs['env']['COMPANION_API_URL'],'http://127.0.0.1:8765')
            self.assertTrue(self.companion.store.get_config('desktop_pet')['enabled'])
            self.assertFalse(self.client.post('/api/pet/stop').json()['running'])
            process.terminate.assert_called_once()
            self.assertFalse(self.companion.store.get_config('desktop_pet')['enabled'])

    def test_http_worker_queues_status_without_touching_tk(self):
        from queue import SimpleQueue
        from dsh_companion.desktop_pet.window import HarnessPet
        pet=HarnessPet.__new__(HarnessPet)
        pet.status_updates=SimpleQueue()
        pet.root=MagicMock()
        status={'state':'working','action':'read'}
        with patch('dsh_companion.desktop_pet.window.read_status',return_value=status):
            pet._read_status_worker()
        self.assertEqual(pet.status_updates.get(),status)
        pet.root.after.assert_not_called()

    def test_desktop_client_reads_only_project_api(self):
        from dsh_companion.desktop_pet import client
        from io import BytesIO
        with patch.object(client,'urlopen',return_value=BytesIO(b'{"title":"test","state":"working"}')) as read:
            self.assertEqual(client.read_status()['title'],'test')
        self.assertTrue(read.call_args.args[0].endswith('/api/pet'))


if __name__=='__main__':unittest.main()
