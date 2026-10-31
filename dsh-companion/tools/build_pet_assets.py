"""Rebuild the bundled mini-whale animations from the pinned upstream WebM clips.

Run from dsh-companion: .venv\\Scripts\\python.exe tools\\build_pet_assets.py
The originals stay in downloads; runtime playback needs only Pillow.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import httpx
import imageio_ffmpeg
from PIL import Image, ImageChops, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT.parent / 'downloads/pet-candidates/mini-whale/webm'
ASSETS = ROOT / 'src/dsh_companion/desktop_pet/assets'
COMMIT = '0b8a6ad98ad2bc2bee986cd027936a5eeb3accc8'
BASE = f'https://raw.githubusercontent.com/PC2005-cloud/dsh-pet/{COMMIT}/dsh-pet/assets/webm/'
SOURCES = {
    'idle': '待机呼吸休闲', 'reply': '工作状态-忙碌点按',
    'watch': '碎碎念-对屏碎碎念', 'read': '工作状态-思考冒泡',
    'write': '轻快记录', 'code': '写代码', 'think': '深度思考碎碎念',
    'organize': '工作状态-清点归档', 'celebrate': '工作状态-雀跃庆祝',
    'sleep': '睡觉-循环', 'dance': '可爱宅舞', 'cube': '原地专心玩魔方',
    'hotpot': '涮火锅', 'wave': '点击回应-元气挥手',
    'drag': '被鼠标拖拽悬空反馈', 'draw': '写福字', 'search': '东张西望',
}
FPS = 15
SIZE = (512, 288)


def decode(path: Path):
    process = subprocess.Popen([
        imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
        '-c:v', 'libvpx-vp9', '-i', str(path), '-vf', f'fps={FPS},scale={SIZE[0]}:{SIZE[1]}',
        '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1',
    ], stdout=subprocess.PIPE)
    byte_count = SIZE[0] * SIZE[1] * 4
    frames = []
    while raw := process.stdout.read(byte_count):
        if len(raw) != byte_count:
            raise RuntimeError(f'Incomplete RGBA frame: {path}')
        frame = Image.frombytes('RGBA', SIZE, raw)
        # VP9 alpha can contain near-zero codec noise outside the character.
        frame.putalpha(frame.getchannel('A').point(lambda a: 0 if a < 16 else a))
        frames.append(frame)
    process.stdout.close()
    if process.wait():
        raise RuntimeError(f'FFmpeg failed: {path}')
    return frames


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    union = Image.new('L', SIZE)
    metadata = {}
    for key, source in SOURCES.items():
        path = CACHE / f'{key}.webm'
        if not path.exists():
            response = httpx.get(BASE + source + '.webm', follow_redirects=True, timeout=60)
            response.raise_for_status()
            path.write_bytes(response.content)
        frames = decode(path)
        durations = [round((i+1)*1000/FPS) - round(i*1000/FPS) for i in range(len(frames))]
        for frame in frames:
            union = ImageChops.lighter(union, frame.getchannel('A').point(lambda a: 255 if a >= 40 else 0))
        frames[0].save(CACHE / f'{key}-decoded.webp', save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, lossless=True, method=0)
        metadata[key] = {'source': source + '.webm', 'durations': durations}
        print(f'Decoded {key}: {len(frames)} frames', flush=True)
    left, top, right, bottom = union.getbbox()
    bounds = (max(0,left-5), max(0,top-5), min(SIZE[0],right+5), min(SIZE[1],bottom+5))
    for key, clip in metadata.items():
        frames = []
        with Image.open(CACHE / f'{key}-decoded.webp') as image:
            for index in range(image.n_frames):
                image.seek(index)
                subject = image.convert('RGBA').crop(bounds)
                subject = ImageOps.contain(subject, (360,360), Image.Resampling.LANCZOS)
                canvas = Image.new('RGBA', (360,360))
                canvas.alpha_composite(subject, ((360-subject.width)//2, (360-subject.height)//2))
                frames.append(canvas)
        target = ASSETS / f'{key}.webp'
        frames[0].save(target, save_all=True, append_images=frames[1:], duration=clip['durations'],
                       loop=0, quality=88, method=4, minimize_size=True)
        print(f'Bundled {key}: {target.stat().st_size//1024} KB', flush=True)
    (ASSETS/'animations.json').write_text(json.dumps({
        'upstream': 'https://github.com/PC2005-cloud/dsh-pet', 'commit': COMMIT,
        'fps': FPS, 'size': [360,360], 'crop': bounds, 'clips': metadata,
    }, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__': main()
