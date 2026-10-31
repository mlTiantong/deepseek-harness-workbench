"""Desktop commands use the existing main actor, including while autonomy sleeps."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
from queue import SimpleQueue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError, HTTPError

from fastapi.testclient import TestClient
from dsh_companion.app import create_app
from dsh_companion.desktop_pet import client
from dsh_companion.desktop_pet.window import HarnessPet
from dsh_companion.pet import desktop_status, PET_TASKS

ROOT = Path(__file__).resolve().parents[1]


class PetCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.app = create_app(ROOT, data_dir=self.tmp.name)
        self.companion = self.app.state.companion
        self.store = self.companion.store
        self.client = TestClient(self.app)

    def enqueue(self, action, text=""):
        response = self.client.post('/api/pet/task', json={'action': action, 'text': text})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_presets_and_custom_are_one_off_main_jobs(self):
        self.companion.scheduler.pause()
        autonomy = self.store.get_config('autonomy')
        self.store.set_config('autonomy', {**autonomy, 'enabled': False})
        before = {key: self.store.get_config(key) for key in ('paused', 'autonomy', 'platforms')}
        for action in (*PET_TASKS, 'custom'):
            with self.subTest(action=action):
                job = self.enqueue(action, '  去看看猫猫视频  ')
                self.assertEqual((job['actor_id'], job['kind'], job['status']), ('main', 'desktop_task', 'ready'))
                self.assertFalse(job['autonomous'])
                self.assertEqual(job['payload']['source'], 'desktop_pet')
                self.assertEqual(job['payload']['requested_action'], action)
                if action == 'custom':
                    self.assertEqual(job['payload']['text'], '去看看猫猫视频')
        self.assertEqual(before, {key: self.store.get_config(key) for key in before})
        self.assertEqual(self.store.list_timers(), [])

    def test_invalid_command_does_not_queue(self):
        for body in ({'action': 'dance'}, {'text': '   '}, {}):
            response = self.client.post('/api/pet/task', json=body)
            self.assertEqual(response.status_code, 400)
            self.assertIn('detail', response.json())
        self.assertEqual(self.store.list_jobs(), [])

    def test_status_uses_real_job_state_action_and_result(self):
        for action, (_, animation, _) in PET_TASKS.items():
            job = self.enqueue(action)
            status = desktop_status(self.companion)
            self.assertEqual(status['command']['status'], 'ready')
            self.assertEqual(status['todos'][0]['action'], animation)
            self.store.start_job(job['id'])
            status = desktop_status(self.companion)
            self.assertEqual((status['state'], status['action']), ('working', animation))
            self.assertEqual(status['command']['status'], 'running')
            self.store.finish_job(job['id'], result={'response': '看到了可爱猫猫。' * 40})
            command = desktop_status(self.companion)['command']
            self.assertEqual(command['status'], 'succeeded')
            self.assertEqual(len(command['response']), 240)
        job = self.enqueue('custom', '做一份计划')
        self.store.start_job(job['id']); self.store.finish_job(job['id'], error='平台没登录')
        self.assertEqual(desktop_status(self.companion)['command']['error'], '平台没登录')

    def test_runtime_prompt_includes_task_and_current_context(self):
        direction = '研究夜景拍摄'
        self.store.set_config('autonomy', {**self.store.get_config('autonomy'), 'direction': direction})
        job = self.enqueue('custom', '找一部猫猫视频，读评论')
        with patch.object(self.companion.runtime, 'run_turn', return_value={'response': '好了'}) as turn:
            self.assertEqual(self.companion.runtime.execute_job_sync(job)['response'], '好了')
        self.assertEqual(turn.call_args.args[0], 'main')
        prompt = turn.call_args.args[1]
        for text in ('companion-main', job['payload']['text'], direction, '一次性任务', '直接执行'):
            self.assertIn(text, prompt)
        self.assertEqual(turn.call_args.kwargs['job_id'], job['id'])

    async def test_paused_scheduler_executes_serially_in_existing_main_session(self):
        self.companion.scheduler.pause()
        self.store.set_config('autonomy', {**self.store.get_config('autonomy'), 'enabled': False})
        manual = self.store.create_job('main', 'manual', {'message': '先聊两句'})
        commands = [self.enqueue(action) for action in ('wake', 'watch', 'socialize')]
        scheduler = self.companion.scheduler
        entered, release = asyncio.Event(), asyncio.Event()
        original = scheduler.execute
        async def held(job):
            entered.set()
            await release.wait()
            return await original(job)
        scheduler.execute = held
        with patch.object(self.companion.runtime, 'prepare_session', return_value='existing-main') as session, \
             patch.object(self.companion.runtime, 'run_turn', return_value={'response': '办好了'}) as turn:
            for job in (manual, *commands):
                entered.clear(); release.clear()
                self.assertEqual(await scheduler.dispatch_due(), 1)
                await entered.wait()
                self.assertEqual(await scheduler.dispatch_due(), 0)
                self.assertEqual(scheduler.running['main'][0], job['id'])
                release.set(); await scheduler.running['main'][1]
                self.assertEqual(self.store.get_job(job['id'])['status'], 'succeeded')
            self.assertEqual([call.args[0] for call in session.call_args_list], ['main'] * 4)
            self.assertEqual([call.kwargs['job_id'] for call in turn.call_args_list], [manual['id'], *[j['id'] for j in commands]])
        self.assertTrue(self.store.get_config('paused'))
        self.assertFalse(self.store.get_config('autonomy')['enabled'])

    def test_http_client_posts_utf8_json_to_project(self):
        with patch.object(client, 'urlopen', return_value=BytesIO(b'{"id":"task"}')) as send:
            self.assertEqual(client.assign_task('custom', '去刷猫猫'), {'id': 'task'})
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, client.base_url() + '/api/pet/task')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(json.loads(request.data), {'action': 'custom', 'text': '去刷猫猫'})
        self.assertEqual(request.get_header('Content-type'), 'application/json')

    def test_assignment_worker_never_touches_tk_and_preserves_errors(self):
        pet = HarnessPet.__new__(HarnessPet)
        pet.root = MagicMock(); pet.task_updates = SimpleQueue()
        with patch('dsh_companion.desktop_pet.window.assign_task', return_value={'id': 'job'}):
            pet._assign_task_worker('watch', '')
        self.assertEqual(pet.task_updates.get(), ('watch', '', {'id': 'job'}, ''))
        with patch('dsh_companion.desktop_pet.window.assign_task', side_effect=URLError('offline')):
            pet._assign_task_worker('custom', '看看评论')
        self.assertEqual(pet.task_updates.get(), ('custom', '看看评论', None, '工作台没连接'))
        with patch('dsh_companion.desktop_pet.window.assign_task',
                   side_effect=HTTPError('http://local', 500, 'error', {}, BytesIO(b'Internal Server Error'))):
            pet._assign_task_worker('custom', '继续干活')
        self.assertEqual(pet.task_updates.get(), ('custom', '继续干活', None, '派活没成功 · HTTP 500'))
        pet.root.after.assert_not_called()


if __name__ == '__main__':
    unittest.main()
