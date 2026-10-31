"""Render the two promotional-film compositions using local Chrome and FFmpeg."""
from __future__ import annotations

import argparse
import asyncio
import base64
import math
import subprocess
import tempfile
import wave
from array import array
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DURATION = 30
FPS = 24
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def soundtrack(path: Path) -> None:
    """Original, synthesized ambient arpeggio; no third-party music samples."""
    rate, bpm = 44100, 112
    beat = 60 / bpm
    chords = [(261.63, 329.63, 392.00, 493.88), (196.00, 246.94, 293.66, 392.00),
              (220.00, 261.63, 329.63, 440.00), (174.61, 220.00, 261.63, 349.23)]
    samples = array('h')
    for k in range(rate * DURATION):
        t = k / rate
        chord = chords[int(t / (beat * 4)) % 4]
        sub = t % (beat / 2)
        note = chord[int(t / (beat / 2)) % 4] * (2 if int(t / (beat * 4)) % 2 else 1)
        bell = math.exp(-sub * 8) * (math.sin(2*math.pi*note*t) + .28*math.sin(2*math.pi*note*2.01*t))
        pad = sum(math.sin(2*math.pi*f*t) for f in chord[:3]) / 3
        phase = t % beat
        kick = math.sin(2*math.pi*(48*t-11*math.exp(-phase*18))) * math.exp(-phase*22)
        fade = min(1, t / 1.3, (DURATION-t)/2)
        value = fade * (.10*bell + .055*pad + .09*kick)
        samples.append(int(max(-1, min(1, value)) * 32767))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


async def render(portrait: bool, *, preview: bool = False) -> Path:
    width, height = (1080, 1920) if portrait else (1920, 1080)
    target = ROOT / 'content' / ('大肥鱼-项目宣传视频-竖屏-重制.mp4' if portrait else '大肥鱼-项目宣传视频-横屏-重制.mp4')
    target.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, headless=True,
                                          args=['--disable-background-timer-throttling', '--allow-file-access-from-files'])
        page = await browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
        await page.goto(HERE.joinpath('promo.html').as_uri() + ('?portrait=1' if portrait else ''), wait_until='load')
        await page.wait_for_function('window.ready()')
        await page.evaluate('document.fonts.ready')
        if preview:
            times = [1.7, 5.8, 10, 14, 18.5, 22.5, 27.5]
            images = []
            for t in times:
                data = base64.b64decode(await page.evaluate('(t) => window.renderFrame(t)', t))
                import io
                im = Image.open(io.BytesIO(data)).convert('RGB')
                im.thumbnail((480, 270) if not portrait else (270, 480))
                images.append((t, im))
            cellw, cellh = ((500, 310) if not portrait else (290, 520))
            sheet = Image.new('RGB', (cellw*4, cellh*2), '#e8eff8')
            draw = ImageDraw.Draw(sheet)
            for i, (t, im) in enumerate(images):
                x, y = (i%4)*cellw+(cellw-im.width)//2, (i//4)*cellh
                sheet.paste(im, (x, y)); draw.text((x+4,y+im.height+4), f'{t}s', fill='#27334e')
            preview_dir = ROOT / 'var' / 'video-review'
            preview_dir.mkdir(parents=True, exist_ok=True)
            result = preview_dir / ('preview-vertical.jpg' if portrait else 'preview-horizontal.jpg')
            sheet.save(result, quality=90)
            await browser.close()
            return result
        with tempfile.TemporaryDirectory(prefix='companion-promo-') as folder:
            audio = Path(folder) / 'music.wav'
            soundtrack(audio)
            cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'image2pipe', '-framerate', str(FPS), '-vcodec', 'mjpeg', '-i', 'pipe:0',
                   '-i', str(audio), '-c:v', 'libx264', '-preset', 'medium', '-crf', '19',
                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart',
                   '-shortest', str(target)]
            process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            try:
                for frame in range(DURATION * FPS):
                    data = await page.evaluate('(t) => window.renderFrame(t)', frame / FPS)
                    process.stdin.write(base64.b64decode(data))
                    if frame % (FPS * 5) == 0:
                        print(f"{'竖屏' if portrait else '横屏'} {frame/FPS:.0f}/{DURATION} 秒", flush=True)
                process.stdin.close()
                if process.wait() != 0:
                    raise RuntimeError('FFmpeg 渲染失败')
            finally:
                if process.poll() is None:
                    process.kill()
            await browser.close()
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--format', choices=['horizontal', 'vertical', 'both'], default='both')
    args = parser.parse_args()
    for name in (['horizontal', 'vertical'] if args.format == 'both' else [args.format]):
        print(asyncio.run(render(name == 'vertical', preview=args.preview)), flush=True)


if __name__ == '__main__':
    main()