from __future__ import annotations
import json
import unittest
from pathlib import Path
from PIL import Image, ImageChops
from dsh_companion.desktop_pet.animation import AnimationPlayer, status_animation

ASSETS = Path(__file__).resolve().parents[1]/'src/dsh_companion/desktop_pet/assets'

class PetAnimationTests(unittest.TestCase):
    def test_every_clip_has_real_frames_correct_timing_and_transparency(self):
        manifest = json.loads((ASSETS/'animations.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['clips']),17)
        for name, clip in manifest['clips'].items():
            with self.subTest(clip=name), Image.open(ASSETS/f'{name}.webp') as image:
                self.assertEqual(image.n_frames, len(clip['durations']))
                self.assertGreater(image.n_frames,100)
                first = image.convert('RGBA')
                self.assertEqual(first.getchannel('A').getextrema(), (0,255))
                self.assertEqual(first.getpixel((0,0))[3],0)
                image.seek(image.n_frames//3)
                middle = image.convert('RGB')
                self.assertIsNotNone(ImageChops.difference(first.convert('RGB'),middle).getbbox())
                durations=[]
                for i in range(image.n_frames):
                    image.seek(i);image.load();durations.append(image.info['duration'])
                self.assertEqual(durations,clip['durations'])

    def test_clock_advances_skips_and_loops_actual_frames(self):
        player=AnimationPlayer(ASSETS)
        self.addCleanup(player.close)
        player.select('reply',100)
        first=player.frame(100,(180,180))
        self.assertEqual(player.frame_index,0)
        self.assertIsNone(player.frame(100.01,(180,180)))
        later=player.frame(100.8,(180,180))
        self.assertGreater(player.frame_index,8)
        self.assertIsNotNone(ImageChops.difference(first.convert('RGB'),later.convert('RGB')).getbbox())
        self.assertEqual(set(later.getchannel('A').tobytes()),{0,255})
        player.frame(100+player.duration('reply')+.001,(180,180))
        self.assertEqual(player.frame_index,0)
        self.assertEqual(player.frame(100+player.duration('reply')+.001,(250,250),force=True).size,(250,250))
        player.select('watch',120)
        player.frame(120,(180,180))
        self.assertEqual(player.name,'watch')
        self.assertEqual(player.frame_index,0)

    def test_pause_completion_and_existing_server_task_names(self):
        self.assertEqual(status_animation({'state':'paused','action':'reply'}),'sleep')
        self.assertEqual(status_animation({'state':'completed'}),'celebrate')
        self.assertEqual(status_animation({'state':'offline'}),'sleep')
        self.assertEqual(status_animation({'state':'working','currentTask':'回私信'}),'reply')
        self.assertEqual(status_animation({'state':'working','currentTask':'看视频 / 图文'}),'watch')
        self.assertEqual(status_animation({'state':'working','currentTask':'读评论'}),'read')
        self.assertEqual(status_animation({'state':'idle'}),'idle')

if __name__=='__main__': unittest.main()
