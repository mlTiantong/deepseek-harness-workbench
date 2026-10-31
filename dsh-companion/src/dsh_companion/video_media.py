"""用 FFmpeg 抽帧、制作封面，并在本机转写视频语音。"""
from __future__ import annotations

import base64
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def sample_times(duration: float, count: int) -> list[float]:
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法获取视频时长，请指定时间点")
    if not 1 <= count <= 16:
        raise ValueError("抽帧数量需要在 1 到 16 之间")
    return [round(duration * (i + 0.5) / count, 2) for i in range(count)]


def probe_duration(source: str) -> float:
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", source],
                            capture_output=True, creationflags=NO_WINDOW)
    text = result.stderr.decode("utf-8", errors="replace")
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        raise ValueError("无法获取下载视频的时长")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

def extract_frame(source: str, timestamp: float, *, headers: dict[str, str] | None = None) -> dict:
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error"]
    if headers:
        command += ["-headers", "".join(f"{key}: {value}\r\n" for key, value in headers.items())]
    command += ["-ss", str(timestamp), "-i", source, "-frames:v", "1",
                "-vf", "scale='min(960,iw)':-2", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    result = subprocess.run(command, capture_output=True, check=True, creationflags=NO_WINDOW)
    if not result.stdout:
        raise ValueError(f"{timestamp} 秒处没有视频画面")
    return {"time": timestamp, "mime_type": "image/jpeg", "data": base64.b64encode(result.stdout).decode("ascii")}


def create_cover(source: str, destination: str | Path) -> None:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                    "-i", source, "-vf", "thumbnail=30", "-frames:v", "1", "-y", str(destination)],
                   capture_output=True, check=True, creationflags=NO_WINDOW)


_whisper_models: dict[str, object] = {}


def transcribe_audio(source: str, *, headers: dict[str, str] | None = None,
                     model_name: str = "base") -> dict:
    """提取单声道语音，在本机用 Whisper + VAD 转写；不会上传音频。"""
    import wave
    import numpy as np
    from faster_whisper import WhisperModel

    with tempfile.TemporaryDirectory(prefix="companion-video-audio-") as temp:
        wav = str(Path(temp) / "voice.wav")
        command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error"]
        if headers:
            command += ["-headers", "".join(f"{key}: {value}\r\n" for key, value in headers.items())]
        command += ["-i", source, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", wav]
        result = subprocess.run(command, capture_output=True, creationflags=NO_WINDOW)
        if result.returncode:
            error = result.stderr.decode("utf-8", errors="replace")
            if "does not contain any stream" in error or "matches no streams" in error:
                return {"audio_status": "no_audio", "transcript": "", "audio_understood": False}
            raise RuntimeError("提取视频音轨失败：" + error[-800:])
        if model_name not in _whisper_models:
            _whisper_models[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
        with wave.open(wav, "rb") as audio_file:
            samples = np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = _whisper_models[model_name].transcribe(
            samples, vad_filter=True, beam_size=1, condition_on_previous_text=False)
        lines = [f"{segment.start:.1f}–{segment.end:.1f}s {segment.text.strip()}"
                 for segment in segments if segment.text.strip()]
    return {"audio_status": "transcribed" if lines else "no_speech", "transcript": "\n".join(lines),
            "audio_understood": bool(lines), "transcript_language": info.language,
            "transcript_model": model_name}
