from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml

from fastapi.testclient import TestClient

from dsh_companion.app import create_app
from dsh_companion.billing import DEFAULT_PRICING, estimate_cny
from dsh_companion.dsh_runtime import DshRuntime
from dsh_companion.store import Store


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.store = Store(self.base/'db.sqlite')
        self.runtime = DshRuntime(self.store, Path.cwd(), data_dir=self.base/'runtime')
        self.runtime.var.mkdir(parents=True); self.runtime.contexts.mkdir(); self.runtime.controls.mkdir()

    def test_official_fixed_cny_rates_and_cache_are_disjoint(self):
        price = DEFAULT_PRICING['profiles'][0]
        usage = {'inputTokens': 1_000_000, 'cacheReadTokens': 1_000_000,
                 'cacheWriteTokens': 0, 'outputTokens': 1_000_000}
        self.assertEqual(estimate_cny(usage, price), 10.04)
        self.store.record_model_usage(source_id='s:1', source='model_call', session_id='s', actor_id='main',
                                      usage=usage, profile=price)
        self.store.record_model_usage(source_id='s:1', source='model_call', session_id='s', actor_id='main',
                                      usage=usage, profile=price)
        result = self.store.model_usage_summary()
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(result['total_tokens'], 3_000_000)
        self.assertEqual(result['estimated_cost_cny'], 10.04)
        # 后续更改单价不重算过去调用。
        price2 = dict(price, output=1000)
        self.store.record_model_usage(source_id='s:2', source='model_call', session_id='s', actor_id='main',
                                      usage={'inputTokens': 0, 'outputTokens': 1_000_000}, profile=price2)
        self.assertEqual(self.store.model_usage_summary()['estimated_cost_cny'], 1010.04)

    def test_unknown_usage_is_not_presented_as_a_complete_bill(self):
        self.store.record_model_usage(source_id='s:1', source='model_call', session_id='s', actor_id='main')
        result = self.store.model_usage_summary()
        self.assertEqual(result['unreported_calls'], 1)
        self.assertEqual(result['total_tokens'], 0)

    def test_historical_projection_is_imported_once(self):
        self.store.update_actor('main', {'session_id':'old-session'})
        folder = self.runtime.home/'storages/session_projcache/sessions'; folder.mkdir(parents=True)
        data={'record':{'rows':{'tokenUsage':{'val':{'totals':{'uncachedInputTokens':100,
            'cacheReadTokens':1000,'cacheWriteTokens':0,'outputTokens':20}}}}}}
        (folder/'old-session.json').write_text(json.dumps(data),encoding='utf-8')
        self.runtime.import_historical_usage(); self.runtime.import_historical_usage()
        result = self.store.model_usage_summary()
        self.assertEqual(result['historical_sessions'], 1)
        self.assertEqual(result['total_tokens'], 1120)

    def test_runtime_counts_each_reported_model_step(self):
        def fake_run(prompt, session_id, on_notification):
            for seq, usage in [(2, {'inputTokens':100, 'cacheReadTokens':500, 'outputTokens':20}),
                               (3, {'inputTokens':200, 'outputTokens':50})]:
                event = {'type':'assistant/message','seq':seq,'time':100000,'data':{'usage':usage}}
                on_notification(SimpleNamespace(method='session.event',payload={'sessionId':session_id,'event':event}))
            return SimpleNamespace(finish_reason='completed', events=[], session_id=session_id, final_response='ok')
        self.runtime.harness=SimpleNamespace(run=fake_run)
        with patch.object(self.runtime,'prepare_session',return_value='s'):
            self.runtime.run_turn('main','hi',job_id='job1')
        result = self.store.model_usage_summary()
        self.assertEqual(result['model_calls'], 2)
        self.assertEqual(result['total_tokens'], 870)
        self.assertEqual(result['cache_unspecified_calls'], 1)

    def test_model_controls_change_patch(self):
        config = self.store.get_config('dsh')
        config.update(provider='companion-model', model='example', base_url='https://example.test/v1',
                      context_length=128000, max_tokens=4096, api_format='openai-responses',
                      reasoning_effort='high', compatibility='standard')
        patch_file=self.runtime._patch(config); entries=yaml.safe_load(patch_file.read_text(encoding='utf-8'))
        provider=next(x for x in entries if x.get('id')=='llm-pi-ai')['config']['providers']['companion-model']
        self.assertEqual(provider['api'], 'openai-responses')
        self.assertEqual(provider['models'][0]['contextWindow'], 128000)
        self.assertEqual(provider['models'][0]['maxTokens'], 4096)
        self.assertIn('high',provider['models'][0]['reasoningEfforts'])
        self.assertNotIn('compat',provider)


class BillingApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.app = create_app(Path.cwd(), data_dir=self.tmp.name)
        self.client = TestClient(self.app)

    def test_pricing_selection_and_usage_endpoint(self):
        config=self.client.get('/api/config').json()
        pricing=config['pricing']
        pricing['profiles'].append({'id':'custom','name':'Custom','uncached_input':3,'cached_input':0.3,'output':9})
        pricing['selected_profile']='custom'
        response=self.client.put('/api/config/pricing',json={'value':pricing})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/config').json()['pricing']['selected_profile'],'custom')
        response=self.client.get('/api/usage');self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['total_tokens'],0)

    def test_model_route_passes_context_and_reasoning(self):
        mocked=AsyncMock(return_value={'ok':True})
        with patch.object(self.app.state.companion,'change_model',mocked):
            response=self.client.post('/api/model',json={'model':'example','base_url':'https://example.test/v1',
                'api_key':'','api_format':'openai-responses','compatibility':'standard',
                'context_length':128000,'max_tokens':4096,'reasoning_effort':'high'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(mocked.await_args.kwargs['context_length'],128000)
        self.assertEqual(mocked.await_args.kwargs['reasoning_effort'],'high')


if __name__=='__main__': unittest.main()
