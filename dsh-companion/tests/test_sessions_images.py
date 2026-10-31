import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from dsh_companion.app import create_app
from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.image_generation import ImageGeneration
from dsh_companion.store import Store

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+bjxoAAAAASUVORK5CYII=')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'db.sqlite')
        self.runtime = DshRuntime(self.store, Path.cwd(), data_dir=self.root)

    def test_context_uses_current_projected_occupancy(self):
        self.store.update_actor('main', {'session_id':'one'})
        folder=self.runtime.home/'storages/session_projcache/sessions';folder.mkdir(parents=True)
        (folder/'one.json').write_text(json.dumps({'record':{'rows':{'contextPressure':{'val':{
            'pressureTokens':5000,'surfaceTokens':1200,'sampledSurfaceTokens':1500,'contextWindow':10000}}}}}),encoding='utf-8')
        rows=self.runtime.session_contexts();main=next(x for x in rows if x['id']=='main')
        self.assertEqual(main['context_tokens'],4700)
        self.assertEqual(main['context_window'],10000)
        self.assertEqual(len(rows),len(self.store.list_actors()))
        self.assertIsNone(next(x for x in rows if x['id']=='douyin_chat')['context_tokens'])

    def test_live_context_takes_precedence_and_has_revision(self):
        self.store.update_actor('main', {'session_id':'one'})
        self.runtime.controls.mkdir(parents=True)
        (self.runtime.controls/'one.context.json').write_text(json.dumps({'projectedTokens':2400,'revision':77}),encoding='utf-8')
        main=next(x for x in self.runtime.session_contexts() if x['id']=='main')
        self.assertEqual(main['context_tokens'],2400)
        self.assertEqual(main['context_revision'],77)

    def test_upgrade_old_auto_setting_to_individual_disabled_settings(self):
        self.store.set_config('session_compaction',{'enabled':False,'threshold_tokens':120000})
        upgraded=Store(self.root/'db.sqlite').get_config('session_compaction')
        self.assertEqual(set(upgraded),{x['id'] for x in self.store.list_actors()})
        self.assertEqual(upgraded['main'],{'enabled':False,'threshold_tokens':120000})
        self.assertEqual(upgraded['douyin_chat'],{'enabled':False,'threshold_tokens':120000})

    def test_auto_compaction_settings_are_independent_per_session(self):
        app=create_app(Path.cwd(),data_dir=self.root/'auto'); companion=app.state.companion; client=TestClient(app)
        for actor in ('main','douyin_chat','bili_group'):
            companion.store.update_actor(actor,{'session_id':'session-'+actor})
        actors=companion.store.list_actors()
        for actor in actors:
            actor['context_tokens']=150000 if actor['id'] in ('main','douyin_chat') else 20000
            actor['context_revision']=12
        with patch.object(companion.runtime,'session_contexts',return_value=actors):
            self.assertEqual(companion.schedule_auto_compactions(),[])
            client.put('/api/sessions/main/auto-compaction',json={'enabled':True,'threshold_tokens':100000})
            client.put('/api/sessions/douyin_chat/auto-compaction',json={'enabled':True,'threshold_tokens':160000})
            jobs=companion.schedule_auto_compactions()
            self.assertEqual([x['actor_id'] for x in jobs],['main'])
            self.assertTrue(jobs[0]['payload']['automatic'])
            self.assertEqual(companion.schedule_auto_compactions(),[])
            client.put('/api/sessions/douyin_chat/auto-compaction',json={'enabled':True,'threshold_tokens':140000})
            jobs += companion.schedule_auto_compactions()
            self.assertEqual({x['actor_id'] for x in jobs},{'main','douyin_chat'})
            self.assertFalse(companion.store.get_config('session_compaction')['bili_group']['enabled'])
            for job in jobs:
                companion.store.start_job(job['id']);companion.store.finish_job(job['id'],result={'compacted':False})
            for actor in actors:
                if actor['id'] in ('main','douyin_chat'):
                    threshold=100000 if actor['id']=='main' else 140000
                    actor['metadata']['last_auto_compaction']={'session_id':actor['session_id'],'revision':12,'threshold_tokens':threshold}
            self.assertEqual(companion.schedule_auto_compactions(),[])
            next(x for x in actors if x['id']=='main')['context_revision']=13
            self.assertEqual([x['actor_id'] for x in companion.schedule_auto_compactions()],['main'])

    def test_reset_replaces_one_session_but_keeps_memory_and_billing(self):
        self.store.update_actor('main', {'session_id':'old','metadata':{'example':'retained'}})
        self.store.update_actor('douyin_chat', {'session_id':'untouched'})
        self.store.put_memory('context','shared',memory_id='active_context')
        self.store.record_model_usage(source_id='old:1',source='model_call',session_id='old',actor_id='main',
                                      usage={'inputTokens':10,'outputTokens':3})
        self.runtime.live_sessions.add('old')
        self.runtime.reset_session('main')
        self.assertIsNone(self.store.get_actor('main')['session_id'])
        self.assertEqual(self.store.get_actor('douyin_chat')['session_id'],'untouched')
        self.assertEqual(self.store.get_actor('main')['metadata']['example'],'retained')
        self.assertIn('session_reset_at',self.store.get_actor('main')['metadata'])
        self.assertEqual(self.store.get_memory('active_context')['content'],'shared')
        self.assertEqual(self.store.model_usage_summary()['total_tokens'],13)
        self.assertNotIn('old',self.runtime.live_sessions)

    def test_native_auto_compaction_is_disabled_for_user_control(self):
        import yaml
        config=self.store.get_config('dsh')
        patch_data=yaml.safe_load(self.runtime._patch(config).read_text(encoding='utf-8'))
        entry=next(item for item in patch_data if item.get('id')=='compaction-basic')
        self.assertTrue(entry['disabled'])
        inserted=next(item['insert'] for item in patch_data if 'insert' in item)
        self.assertFalse(next(item for item in inserted if item['id']=='companion-compaction')['config']['auto'])

    def test_compaction_summary_usage_is_counted(self):
        record=self.runtime._usage_recorder('main','one','', 'job')
        record(SimpleNamespace(method='session.event',payload={'sessionId':'one','event':{
            'type':'compaction/summary','seq':5,'data':{'llmStreamCall':True,
            'usage':{'inputTokens':10,'cacheReadTokens':80,'outputTokens':5}}}}))
        self.assertEqual(self.store.model_usage_summary()['total_tokens'],95)

    def test_chunked_compaction_summary_usage_is_counted(self):
        record=self.runtime._usage_recorder('main','one','', 'job')
        record(SimpleNamespace(method='session.event',payload={'sessionId':'one','event':{
            'type':'compaction/summary','seq':6,'data':{'usage':{'inputTokens':30,'outputTokens':7}}}}))
        self.assertEqual(self.store.model_usage_summary()['total_tokens'],37)

    def test_overflow_compacts_and_retries_when_no_tool_was_called(self):
        self.runtime.contexts.mkdir(parents=True)
        self.store.update_actor('main', {'session_id': 'one'})
        failed=SimpleNamespace(finish_reason='error',events=[{'type':'turn/end','data':{'reason':
            {'error':{'code':'CONTEXT_WINDOW_EXCEEDED'}}}}])
        success=SimpleNamespace(finish_reason='completed',events=[],session_id='one',final_response='ok')
        self.runtime.harness=SimpleNamespace(run=unittest.mock.Mock(side_effect=[failed,success]))
        with patch.object(self.runtime,'prepare_session',return_value='one'), \
             patch.object(self.runtime,'compact_session',return_value={'compacted':True}) as compact:
            self.assertEqual(self.runtime.run_turn('main','hello')['response'],'ok')
        compact.assert_called_once()
        self.assertEqual(self.runtime.harness.run.call_count,2)

    def test_overflow_never_replays_after_a_tool_call(self):
        self.runtime.contexts.mkdir(parents=True)
        self.store.update_actor('main', {'session_id': 'one'})
        failed=SimpleNamespace(finish_reason='error',events=[{'type':'tool/call'},
            {'type':'turn/end','data':{'reason':{'error':{'code':'CONTEXT_WINDOW_EXCEEDED'}}}}])
        self.runtime.harness=SimpleNamespace(run=unittest.mock.Mock(return_value=failed))
        with patch.object(self.runtime,'prepare_session',return_value='one'), \
             patch.object(self.runtime,'compact_session',return_value={'compacted':True}):
            with self.assertRaisesRegex(RuntimeError,'未自动重试'):
                self.runtime.run_turn('main','hello')
        self.assertEqual(self.runtime.harness.run.call_count,1)

    def test_autonomous_turn_uses_direction_and_current_progress_not_fixed_browsing(self):
        job={'actor_id':'main','kind':'autonomous_main','id':'job','payload':{},'event_id':None}
        cfg=self.store.get_config('autonomy'); cfg['direction']='研究城市夜景，做一份拍摄指南'
        self.store.set_config('autonomy',cfg)
        self.store.create_job('media','scheduled',{'instruction':'整理夜景拍摄参考'})
        self.runtime.memory.add_scratchpad_note('已确定手持和三脚架两条路线')
        for enabled in (True, False):
            cfg=self.store.get_config('platforms'); cfg['douyin']['enabled']=enabled
            self.store.set_config('platforms',cfg)
            with patch.object(self.runtime,'run_turn',return_value={'ok':True}) as run:
                self.runtime.execute_job_sync(job)
                prompt=run.call_args.args[1]
                self.assertIn('研究城市夜景',prompt)
                self.assertIn('整理夜景拍摄参考',prompt)
                self.assertIn('手持和三脚架',prompt)
                self.assertIn('不强制刷推荐',prompt)
                self.assertNotIn('实际调用 browse_douyin_recommended',prompt)

    def test_session_controls_enqueue_non_autonomous_work_for_each_actor(self):
        app=create_app(Path.cwd(),data_dir=self.root/'app'); client=TestClient(app)
        for actor in ('main','douyin_chat','bili_group','publisher','media'):
            response=client.post(f'/api/sessions/{actor}/compact')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['actor_id'],actor)
            self.assertEqual(response.json()['kind'],'session_compact')
            self.assertEqual(response.json()['autonomous'],0)
        self.assertEqual(client.post('/api/sessions/main/reset').json()['kind'],'session_reset')
        self.assertEqual(len(client.get('/api/sessions').json()),len(app.state.companion.store.list_actors()))

    def test_creation_folder_can_be_opened(self):
        app=create_app(Path.cwd(),data_dir=self.root/'creation'); client=TestClient(app)
        with patch.object(app.state.companion.creation, 'open_folder', return_value=str(self.root/'content')) as opened:
            response=client.post('/api/creation/open')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['folder'], str(self.root/'content'))
        opened.assert_called_once_with()


class ImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_endpoint_key_and_base64_or_url_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store=Store(root/'db.sqlite'); image=ImageGeneration(store,root)
            config=image.configure(url='https://example.test/v1',api_key='secret',model='picture-model')
            self.assertTrue(config['key_saved']);self.assertNotIn('api_key',config)
            image.configure(url='https://example.test/v1',model='picture-model')
            received=[]
            def handle(request):
                received.append(request)
                if request.url.path == '/v1/images/generations':
                    return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(PNG).decode()},
                                                          {'url':'https://cdn.test/picture.png'}]})
                return httpx.Response(200,content=PNG)
            transport=httpx.MockTransport(handle)
            real_client=httpx.AsyncClient
            with patch('dsh_companion.image_generation.httpx.AsyncClient',
                       side_effect=lambda **kwargs:real_client(transport=transport,**kwargs)):
                result=await image.generate('a green tree',size='1024x1024')
            self.assertEqual(received[0].url.path,'/v1/images/generations')
            self.assertEqual(received[0].headers['Authorization'],'Bearer secret')
            self.assertEqual(json.loads(received[0].content)['model'],'picture-model')
            self.assertEqual(len(result['images']),2)
            self.assertEqual(Path(result['images'][0]['path']).read_bytes(),PNG)
            self.assertEqual(Path(result['images'][1]['path']).read_bytes(),PNG)

    async def test_custom_full_endpoint_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store=Store(root/'db.sqlite'); image=ImageGeneration(store,root)
            image.configure(url='https://example.test/custom/images/generations',api_key='key')
            requests=[]
            def handle(request):
                requests.append(request)
                return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(PNG).decode()}]})
            real_client=httpx.AsyncClient
            with patch('dsh_companion.image_generation.httpx.AsyncClient',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(handle),**kw)):
                await image.generate('hello')
            self.assertEqual(requests[0].url.path,'/custom/images/generations')


if __name__ == '__main__': unittest.main()
