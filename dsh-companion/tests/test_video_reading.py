import asyncio
import base64
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import imageio_ffmpeg

from dsh_companion.platform_service import PlatformService
from dsh_companion.platforms.bilibili import BilibiliAdapter
from dsh_companion.store import Store
from dsh_companion.video_media import extract_frame, sample_times
from dsh_companion.platforms.douyin_web import DouyinWeb
from dsh_companion.platforms import douyin_web_server as server
import json
from io import BytesIO
from PIL import Image


class VideoReadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_switch_audio_switch_and_daily_quota(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'db.sqlite')
            platforms = store.get_config('platforms')
            platforms['bilibili']['enabled'] = True
            store.set_config('platforms', platforms)
            store.set_config('media', {'enabled': True, 'audio_enabled': False, 'daily_limit': 1, 'asr_model': 'base'})
            service = PlatformService(store, folder, SimpleNamespace(notify=lambda: None))
            fake = AsyncMock(return_value={'content_id': 'BVtest', 'duration': 10,
                                           'frames': [{'time': 5}], 'audio_status': 'disabled'})
            service.adapters['bilibili'].fetch_media_frames = fake
            service.adapters['bilibili'].get_content_comments = AsyncMock(return_value={'items': [], 'count': 0})
            result = await service.fetch_media_frames('bilibili', content_id='BVtest')
            self.assertEqual(result['quota']['remaining'], 0)
            self.assertFalse(fake.await_args.kwargs['include_audio'])
            with self.assertRaisesRegex(ValueError, '额度'):
                await service.fetch_media_frames('bilibili', content_id='BVtest')
            media = store.get_config('media'); media['enabled'] = False; store.set_config('media', media)
            with self.assertRaisesRegex(ValueError, '尚未开启'):
                await service.fetch_media_frames('bilibili', content_id='BVtest')

    async def test_seek_timeout_falls_back_to_download_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'video.mp4'
            path.write_bytes(b'video')
            web = DouyinWeb(Store(Path(folder) / 'db.sqlite'), folder)
            web.call = AsyncMock(side_effect=[RuntimeError('视频定位超时'), {'path': str(path)}])
            with patch('dsh_companion.platforms.douyin_web.probe_duration', return_value=10), \
                 patch('dsh_companion.platforms.douyin_web.extract_frame', side_effect=lambda p, t: {'time': t, 'data': 'eA==', 'mime_type': 'image/jpeg'}):
                result = await web.fetch_media_frames(content_id='123', frame_count=2)
            self.assertEqual([f['time'] for f in result['frames']], [2.5, 7.5])
            self.assertEqual(result['frame_source'], 'download')
            self.assertFalse(path.exists())

    async def test_audio_error_keeps_visual_result(self):
        with tempfile.TemporaryDirectory() as folder:
            web = DouyinWeb(Store(Path(folder) / 'db.sqlite'), folder)
            web.call = AsyncMock(side_effect=[{'content_id':'123', 'duration': 2, 'frames': [{'time':1}]}, RuntimeError('视频超过下载大小上限')])
            result = await web.fetch_media_frames(content_id='123', include_audio=True)
            self.assertEqual(result['audio_status'], 'error')
            self.assertIn('上限', result['audio_error'])
            self.assertEqual(len(result['frames']), 1)

    async def test_note_images_are_returned_without_audio_download(self):
        with tempfile.TemporaryDirectory() as folder:
            web = DouyinWeb(Store(Path(folder) / 'db.sqlite'), folder)
            web.call = AsyncMock(return_value={'content_id':'123', 'content_type':'image', 'duration':0, 'frames':[{'data':'eA==', 'mime_type':'image/jpeg'}]})
            result = await web.fetch_media_frames(content_id='123', include_audio=True)
            self.assertEqual(web.call.await_count, 1)
            self.assertEqual(result['audio_status'], 'disabled')
            with patch.dict('os.environ', {'COMPANION_DATABASE': str(Path(folder) / 'mcp.sqlite')}):
                from dsh_companion.mcp_tools import _video_content
                self.assertEqual(_video_content(result)[2].type, 'image')

    async def test_note_detail_downloads_images_in_order(self):
        picture = BytesIO(); Image.new('RGB', (4, 4), 'red').save(picture, 'PNG')
        page = SimpleNamespace(goto=AsyncMock())
        class Net:
            def __init__(self, *args): pass
            async def wait_for(self, *args, **kwargs):
                return {'data': {'aweme_detail': {'aweme_id': '123', 'desc': 'note', 'images': [
                    {'url_list':['https://p.byteimg.com/one']}, {'url_list':['https://p.byteimg.com/two']}]}}}
            def close(self): pass
        async def download(page, url): return picture.getvalue()
        browser = SimpleNamespace(lock=asyncio.Lock(), page=AsyncMock(return_value=page))
        with patch.object(server.upstream, '_browser', browser), patch.object(server.upstream, 'Net', Net), \
             patch.object(server, '_download_note_image', download):
            result = json.loads(await server.companion_video_frames('123'))
        self.assertEqual(result['content_type'], 'image')
        self.assertEqual(result['image_count'], 2)
        self.assertTrue(base64.b64decode(result['frames'][0]['data']).startswith(b'\xff\xd8'))

    async def test_bilibili_upload_defaults_and_real_ffmpeg_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'clip.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=10:duration=1',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
            frame = extract_frame(str(source), sample_times(1, 1)[0])
            self.assertTrue(base64.b64decode(frame['data']).startswith(b'\xff\xd8'))
            adapter = BilibiliAdapter(Store(root / 'db.sqlite'), root)
            adapter._credential = lambda: object()
            with patch('dsh_companion.platforms.bilibili.video_uploader.VideoUploader') as uploader:
                uploader.return_value.start = AsyncMock(return_value={'bvid': 'BVtest'})
                result = await adapter.publish_content(title='标题', files=[str(source)])
            meta = uploader.call_args.args[1]
            self.assertEqual((meta.tid, meta.original, meta.tags), (230, True, ['原创']))
            self.assertEqual(result['bvid'], 'BVtest')


if __name__ == '__main__':
    unittest.main()