"""Time-based playback of transparent, genuinely multi-frame whale animations."""
from __future__ import annotations
from bisect import bisect_right
from itertools import accumulate
from pathlib import Path
import json
from PIL import Image

# Also lets a newly launched pet follow an already-running Companion server.
TASK_ACTIONS = {
    '看视频 / 图文': 'watch', '看作品': 'watch', '刷抖音推荐': 'watch', '研究作品': 'watch',
    '读评论': 'read', '读作品详情': 'read', '读素材': 'read', '读资料': 'read',
    '回私信': 'reply', '回评论': 'reply', '回复私信': 'reply', '回复评论': 'reply',
    '聊群': 'reply', '群聊互动': 'reply', '陪你聊天': 'reply', '安排群聊': 'reply',
    '写稿': 'write', '打磨内容': 'write', '创作': 'write', '记下一笔': 'write',
    '画图': 'draw', '制作内容': 'code', '整理记忆': 'organize', '发作品': 'organize',
    '发布作品': 'organize', '安排后续': 'organize', '安排分工': 'organize',
    '更新进度': 'organize', '找热点': 'search', '找作品': 'search', '挑素材': 'search',
}


def status_animation(status: dict) -> str:
    state = status.get('state')
    if state == 'completed': return 'celebrate'
    if state in ('paused', 'offline', 'error'): return 'sleep'
    if state == 'working':
        return status.get('action') or TASK_ACTIONS.get(status.get('currentTask'), 'think')
    return 'idle'


class AnimationPlayer:
    """Decode only the active clip; never keep 17 full frame stacks in memory."""
    def __init__(self, assets: Path):
        self.assets = assets
        self.clips = json.loads((assets/'animations.json').read_text(encoding='utf-8'))['clips']
        self.image: Image.Image | None = None
        self.name = ''
        self.frame_index = -1
        self.started_at = 0.0

    def duration(self, name: str) -> float:
        return sum(self.clips[name]['durations']) / 1000

    def select(self, name: str, now: float) -> None:
        if self.image is not None: self.image.close()
        self.image = Image.open(self.assets/f'{name}.webp')
        self.name, self.started_at, self.frame_index = name, now, -1
        self.ends = list(accumulate(self.clips[name]['durations']))

    def frame(self, now: float, size: tuple[int, int], *, force=False) -> Image.Image | None:
        position = (now - self.started_at) * 1000 % self.ends[-1]
        index = bisect_right(self.ends, position)
        if index == self.frame_index and not force: return None
        self.frame_index = index
        self.image.seek(index)
        image = self.image.convert('RGBA').resize(size, Image.Resampling.LANCZOS)
        # Tk composites alpha over its color-key canvas. Binary alpha avoids a
        # neon-green fringe, while retaining transparent background/hair gaps.
        image.putalpha(image.getchannel('A').point(lambda a: 255 if a >= 96 else 0))
        return image

    def close(self) -> None:
        if self.image is not None: self.image.close()
