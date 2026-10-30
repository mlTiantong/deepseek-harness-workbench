import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from dsh_companion.platforms.douyin import DouyinAdapter
from dsh_companion.platforms.douyin_web import DouyinWeb
from dsh_companion.platforms.douyin_im import DouyinIM
from dsh_companion.platforms import douyin_web_server as server
from dsh_companion.store import Store


class PostTests(unittest.TestCase):
    def test_posts_preserve_large_ids_and_require_known_type_or_verified_link(self):
        item = {'aweme_id': 7434567890123456789, 'aweme_type': 68, 'author': {'uid': '55'}, 'desc': 'a'}
        post = server._own_post(item, '55')
        self.assertEqual(post['content_id'], '7434567890123456789')
        self.assertEqual(post['share_url'], 'https://www.douyin.com/note/7434567890123456789')
        self.assertEqual(post['visibility'], 'unknown')
        self.assertIsNone(server._own_post(item, 'different'))
        item['aweme_type'] = 999
        self.assertIsNone(server._own_post(item, '55')['share_url'])
        item['share_info'] = {'share_url': 'https://www.douyin.com/video/7434567890123456789?x=1'}
        self.assertEqual(server._own_post(item, '55')['content_type'], 'video')
        item['share_info'] = {'share_url': 'https://evil.test/video/7434567890123456789'}
        self.assertIsNone(server._own_post(item, '55')['share_url'])


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addAsyncCleanup(self.cleanup)
        self.store = Store(Path(self.tmp.name) / 'db.sqlite')

    async def cleanup(self):
        self.tmp.cleanup()

    async def test_recommended_and_own_posts_are_exposed(self):
        adapter = DouyinAdapter(self.store, Path.cwd())
        adapter.web.call = AsyncMock(return_value={'ok': True, 'items': []})
        await adapter.browse_recommended(limit=500, scrolls=500)
        adapter.web.call.assert_awaited_with('douyin_feed', num=30, scrolls=15, skip_live=True)
        await adapter.list_my_posts(limit=10, cursor='123')
        adapter.web.call.assert_awaited_with('companion_my_posts', limit=10, cursor='123')

    async def test_close_handles_an_already_exited_im_process(self):
        im = DouyinIM(self.store, Path.cwd())
        process = SimpleNamespace(returncode=0, stdin=SimpleNamespace(close=lambda: None), wait=AsyncMock())
        im.process = process
        im.reader = None
        im.stderr_reader = None
        await im.close()
        process.wait.assert_awaited_once()
        self.assertIsNone(im.process)

    async def test_publish_matches_only_a_unique_new_post_from_same_account(self):
        web = DouyinWeb(self.store, Path.cwd())
        now = time.time()
        before = {'account_id': 'self', 'items': [{'content_id': '1'}]}
        new = {'content_id': '2', 'title': 'my title\nbody', 'created_at': now,
               'share_url': 'https://www.douyin.com/note/2'}
        web.my_posts = AsyncMock(side_effect=[before, {'account_id': 'self', 'items': [new]}])
        web.call = AsyncMock(return_value={'ok': True, 'published': True, 'management_url': 'manage'})
        result = await web.publish_content(title='my title', content_type='image', files=['a'])
        self.assertEqual(result['share_url'], new['share_url'])
        self.assertEqual(result['share_status'], 'identified_visibility_unknown')
        self.assertNotIn('url', result)

    async def test_publish_never_guesses_on_duplicate_or_account_mismatch(self):
        web = DouyinWeb(self.store, Path.cwd())
        new = {'content_id': '2', 'title': 'title', 'created_at': time.time(), 'share_url': 'https://www.douyin.com/note/2'}
        web.my_posts = AsyncMock(side_effect=[{'account_id': 'self', 'items': []},
                                             {'account_id': 'other', 'items': [new]}])
        web.call = AsyncMock(return_value={'ok': True, 'published': True, 'management_url': 'manage'})
        result = await web.publish_content(title='title', content_type='image', files=['a'])
        self.assertIsNone(result['share_url'])
        self.assertEqual(result['share_status'], 'pending_verification')

    async def test_read_posts_uses_text_json_to_preserve_ids(self):
        class Page:
            async def evaluate(self, script, arg=None):
                if arg is None:
                    return '{"status_code":0,"user":{"uid":9876543210123456789,"sec_uid":"sec"}}'
                self.query = arg
                return ('{"status_code":0,"aweme_list":[{"aweme_id":7434567890123456789,'
                        '"aweme_type":68,"author":{"uid":9876543210123456789}}],'
                        '"has_more":1,"max_cursor":1234567890123456789}')
        page = Page()
        browser = SimpleNamespace(lock=asyncio.Lock(), context=AsyncMock(return_value=object()),
                                  page=AsyncMock(return_value=page))
        with patch.object(server.upstream, '_browser', browser), \
             patch.object(server.upstream, '_goto', AsyncMock()), \
             patch.object(server.upstream, '_login_state', AsyncMock(return_value={'loggedIn': True})):
            result = json.loads(await server.companion_my_posts(limit=5, cursor='0'))
        self.assertEqual(result['items'][0]['content_id'], '7434567890123456789')
        self.assertEqual(result['next_cursor'], '1234567890123456789')
        self.assertEqual(page.query['uid'], 'sec')


if __name__ == '__main__':
    unittest.main()
