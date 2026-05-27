"""Edge-TTS 批量包装。

依赖：edge-tts python 包（需在 pyproject 加 `edge-tts>=7`）。
对外只暴露 `synthesize_batch(items, voice, rate)` 同步函数；内部跑 asyncio。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger('publisher_hub.video_gen.tts')

# 中文女声推荐表。`name` 暴露给前端选择，`code` 是 Microsoft Voice 名。
VOICE_PRESETS: dict[str, str] = {
    'zh-xiaoxiao-female': 'zh-CN-XiaoxiaoNeural',   # 默认：温柔女声
    'zh-xiaoyi-female':   'zh-CN-XiaoyiNeural',     # 知性女声
    'zh-yunjian-male':    'zh-CN-YunjianNeural',    # 沉稳男声
    'zh-yunxi-male':      'zh-CN-YunxiNeural',      # 阳光男声
    'zh-yunyang-male':    'zh-CN-YunyangNeural',    # 新闻男声
}
DEFAULT_VOICE_KEY = 'zh-xiaoxiao-female'


@dataclass
class TTSItem:
    text: str
    out_path: Path


def _resolve_voice(voice_key_or_code: str) -> str:
    """支持传 preset key（'zh-xiaoxiao-female'）或直接传完整 voice code（'zh-CN-XiaoxiaoNeural'）。"""
    if voice_key_or_code in VOICE_PRESETS:
        return VOICE_PRESETS[voice_key_or_code]
    if voice_key_or_code.startswith('zh-') and voice_key_or_code.endswith('Neural'):
        return voice_key_or_code
    log.warning('[tts] 未识别的 voice=%s，回退到默认', voice_key_or_code)
    return VOICE_PRESETS[DEFAULT_VOICE_KEY]


async def _one_async(
    text: str, out_path: Path, voice: str, rate: str, max_retry: int = 3
) -> None:
    """单条 edge-tts。微软偶发 SSL RST，retry 3 次。"""
    import edge_tts
    last_err: Exception | None = None
    for attempt in range(max_retry):
        if attempt > 0:
            wait = 3 * attempt
            log.info('[tts] retry %d after %ds', attempt, wait)
            await asyncio.sleep(wait)
        try:
            com = edge_tts.Communicate(text, voice=voice, rate=rate)
            await com.save(str(out_path))
            return
        except Exception as e:
            last_err = e
            log.warning(
                '[tts] attempt=%d 失败 voice=%s text=%r err=%s',
                attempt, voice, text[:30], e,
            )
    raise RuntimeError(f'edge-tts 重试 {max_retry} 次仍失败: {last_err}')


async def _batch_async(
    items: Sequence[TTSItem], voice: str, rate: str, gap_seconds: float = 1.5
) -> None:
    """串行跑，段间 sleep 避免被微软端速率拒。"""
    for i, it in enumerate(items):
        if i > 0:
            await asyncio.sleep(gap_seconds)
        log.info('[tts] [%d/%d] voice=%s text=%r', i + 1, len(items), voice, it.text[:30])
        await _one_async(it.text, it.out_path, voice=voice, rate=rate)
        log.info('[tts] [%d/%d] ✓ %s', i + 1, len(items), it.out_path)


def synthesize_batch(
    items: Sequence[TTSItem],
    voice: str = DEFAULT_VOICE_KEY,
    rate: str = '+5%',
) -> None:
    """同步入口。串行生成，全做完才返回。"""
    if not items:
        return
    voice_code = _resolve_voice(voice)
    asyncio.run(_batch_async(items, voice_code, rate))


# ── 静音模式（voice='none' / 空）──────────────────────────────────────────
# 用户选「无配音」时：每段生一段对应时长的静音 mp3，让 Remotion 拿来当 scene 时长基准。
# 时长 = max(MIN_SILENT_SEC, 字符数 / READ_CHAR_PER_SEC)。

MIN_SILENT_SEC = 2.5          # 最短场景时长（让 BGM / Ken Burns 有发挥）
READ_CHAR_PER_SEC = 5.0       # 人眼读字幕约 5 字/秒（用来估时长）

SILENT_VOICE_KEYS = {'none', '', 'silent', 'mute'}


def estimate_silent_duration(text: str) -> float:
    """字幕字符数 → 应该展示几秒（无配音模式用）。"""
    n = len(text or '')
    return max(MIN_SILENT_SEC, n / READ_CHAR_PER_SEC)


def synthesize_silence(items: Sequence[TTSItem]) -> None:
    """给 voice='none' 的场景生静音 mp3，时长按 estimate_silent_duration 算。

    用 ffmpeg lavfi anullsrc：
      ffmpeg -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \\
             -t <sec> -acodec libmp3lame -b:a 64k -y <out>
    """
    if not items:
        return
    for i, it in enumerate(items, 1):
        sec = round(estimate_silent_duration(it.text), 2)
        log.info('[tts] [%d/%d] 静音 %ss → %s', i, len(items), sec, it.out_path)
        cmd = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-f', 'lavfi',
            '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-t', str(sec),
            '-acodec', 'libmp3lame', '-b:a', '64k',
            '-y', str(it.out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(
                f'ffmpeg 生静音失败 returncode={proc.returncode}\nstderr: {proc.stderr[-300:]}'
            )


def is_silent(voice: str) -> bool:
    return (voice or '').strip().lower() in SILENT_VOICE_KEYS


def probe_duration_seconds(audio_path: Path) -> float:
    """用 ffprobe 量音频秒数。失败抛 RuntimeError。"""
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-show_entries', 'format=duration',
             '-of', 'default=nw=1:nk=1', str(audio_path)],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
        v = float(out)
        if v <= 0:
            raise ValueError('duration <= 0')
        return v
    except (subprocess.CalledProcessError, ValueError) as e:
        raise RuntimeError(f'无法 ffprobe {audio_path}: {e}')
