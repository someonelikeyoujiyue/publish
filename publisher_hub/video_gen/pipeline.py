"""短视频生成主编排。

入口：VideoGenPipeline.run_job(job_id, db, config, prompts) — 同步阻塞跑一条任务。
调用方（routes/api/video.py）负责把这个调用丢到后台 thread。

job 数据流：
  1. 从 DB 读 job 记录（含 user_id、topic、可选 narrations、可选 image_paths）
  2. status → 'processing'
  3. narration：用户给了就用，没给就 LLM 生（n_scenes 默认 3）
  4. image：用户给了就用，不够 / 没给从 assets/video-defaults/ 补到 scene 数
  5. TTS：每段 narration 跑 edge-tts → mp3 落到 <job_dir>/audio/
  6. 量音频时长 + 拼 input.json
  7. spawn node remotion → mp4 落到 <job_dir>/output.mp4
  8. status → 'done'，output_path 写入 DB
"""
from __future__ import annotations

import json
import logging
import random
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import narration as narration_mod
from . import remotion as remotion_mod
from . import tts as tts_mod

log = logging.getLogger('publisher_hub.video_gen.pipeline')


class VideoJobError(Exception):
    """流程里任何不可恢复的失败统一抛这个。catch 的地方负责标 DB failed + 错误信息。"""


# 默认 narration 段数（用户没给文案时 LLM 生几段；同时控制视频时长）
DEFAULT_N_SCENES = 3
# 默认音色
DEFAULT_VOICE = tts_mod.DEFAULT_VOICE_KEY
DEFAULT_RATE = '+5%'
# 默认输出分辨率（抖音/视频号/快手通用）
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_FPS = 30
# scene 末尾留白（秒），避免字幕跟下一段硬切
SCENE_TAIL_GAP = 0.4


def _project_root() -> Path:
    """publisher-hub/ 绝对路径。"""
    return Path(__file__).resolve().parent.parent.parent


def default_video_jobs_dir() -> Path:
    """data/video-jobs/ 默认位置（生产服务器 /data/publisher-hub/data/video-jobs/）。"""
    return _project_root() / 'data' / 'video-jobs'


def default_assets_dir() -> Path:
    """assets/video-defaults/"""
    return _project_root() / 'assets' / 'video-defaults'


@dataclass
class _ResolvedAssets:
    images: list[Path]
    bgm: Optional[Path]


def _pick_default_images(n: int, defaults_dir: Path) -> list[Path]:
    """从 assets/video-defaults/ 随机挑 n 张图（不够循环填）。"""
    candidates = [
        p for p in sorted(defaults_dir.iterdir())
        if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}
    ]
    if not candidates:
        raise VideoJobError(f'默认图库为空: {defaults_dir}')
    # 不够就循环填（凑数总比报错好）
    picked: list[Path] = []
    shuffled = candidates.copy()
    random.shuffle(shuffled)
    while len(picked) < n:
        for c in shuffled:
            picked.append(c)
            if len(picked) >= n:
                break
    return picked[:n]


def _resolve_assets(
    user_image_paths: list[str],
    n_needed: int,
    defaults_dir: Path,
    bgm_path: Optional[str] = None,
) -> _ResolvedAssets:
    """把"用户传的图 + 默认图"凑到 n_needed 张。BGM 没指定用默认。"""
    images: list[Path] = []
    for raw in (user_image_paths or []):
        p = Path(raw)
        if p.is_absolute() and p.exists():
            images.append(p)
        else:
            log.warning('[pipeline] 用户图不存在/不是绝对路径，跳过: %s', raw)
    if len(images) < n_needed:
        need = n_needed - len(images)
        log.info('[pipeline] 用户图 %d 张不够 %d，从默认库补 %d', len(images), n_needed, need)
        images.extend(_pick_default_images(need, defaults_dir))

    # BGM：用户没指定 → 默认（如果默认目录里有 bgm-default.mp3）
    bgm_final: Optional[Path] = None
    if bgm_path:
        p = Path(bgm_path)
        if p.exists():
            bgm_final = p
        else:
            log.warning('[pipeline] 用户 bgm 不存在: %s', bgm_path)
    if bgm_final is None:
        cand = defaults_dir / 'bgm-default.mp3'
        if cand.exists():
            bgm_final = cand

    return _ResolvedAssets(images=images[:n_needed], bgm=bgm_final)


class VideoGenPipeline:
    def __init__(self, config: dict, prompts: dict):
        self.config = config
        self.prompts = prompts
        # video_gen 段允许覆盖 LLM 配置（默认复用全局 llm 段，可以单独指定更便宜的模型）
        vg_cfg = (config.get('video_gen') or {})
        self.llm_cfg = vg_cfg.get('llm') or config.get('llm')
        if not self.llm_cfg:
            raise VideoJobError('config.yaml 既没有 video_gen.llm 也没有 llm 段')
        self.default_voice = vg_cfg.get('voice', DEFAULT_VOICE)
        self.default_rate = vg_cfg.get('rate', DEFAULT_RATE)
        self.fps = int(vg_cfg.get('fps', DEFAULT_FPS))
        self.width = int(vg_cfg.get('width', DEFAULT_WIDTH))
        self.height = int(vg_cfg.get('height', DEFAULT_HEIGHT))
        self.defaults_dir = Path(vg_cfg.get('defaults_dir') or default_assets_dir())
        self.jobs_root = Path(vg_cfg.get('jobs_dir') or default_video_jobs_dir())
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    # ── 主入口 ────────────────────────────────────────────────────────

    def run_job(self, job_id: int, db) -> Path:
        """阻塞跑一条 job，返回 mp4 路径。失败统一抛 VideoJobError。"""
        job = db.get_video_job(job_id)
        if not job:
            raise VideoJobError(f'job {job_id} 不存在')

        log.info('[pipeline] ▶ job=%d user=%s topic=%r',
                 job_id, job.get('user_id'), (job.get('topic') or '')[:40])

        db.update_video_job(job_id, status='processing')

        job_dir = self.jobs_root / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        audio_dir = job_dir / 'audio'
        audio_dir.mkdir(exist_ok=True)

        try:
            mp4 = self._run_inner(job_id, job, db, job_dir, audio_dir)
        except VideoJobError as e:
            log.warning('[pipeline] ✗ job=%d 失败: %s', job_id, e)
            db.update_video_job(job_id, status='failed', error_msg=str(e))
            raise
        except Exception as e:
            log.exception('[pipeline] ✗ job=%d 未捕获异常', job_id)
            db.update_video_job(job_id, status='failed', error_msg=f'{type(e).__name__}: {e}')
            raise VideoJobError(str(e)) from e

        log.info('[pipeline] ✓ job=%d → %s', job_id, mp4)
        return mp4

    # ── 内部 ──────────────────────────────────────────────────────────

    def _run_inner(self, job_id: int, job: dict, db, job_dir: Path, audio_dir: Path) -> Path:
        topic = (job.get('topic') or '').strip()
        user_narrations = job.get('narrations') or []
        user_images = job.get('image_paths') or []
        voice = job.get('voice') or self.default_voice
        rate = job.get('rate') or self.default_rate
        bgm_input = job.get('bgm_path')

        # 1. narration: 用户给了就用，没给就 LLM 生
        if user_narrations:
            narrations = [n.strip() for n in user_narrations if isinstance(n, str) and n.strip()]
            if not narrations:
                raise VideoJobError('提供的 narrations 全空')
            video_title = (job.get('title') or topic or '').strip()
            log.info('[pipeline] 用户提供 narrations %d 段，跳过 LLM', len(narrations))
        else:
            if not topic:
                raise VideoJobError('既没提供 narrations 也没 topic，没法生文案')
            n_scenes = int(job.get('n_scenes') or DEFAULT_N_SCENES)
            n_scenes = max(2, min(6, n_scenes))  # 钳制范围
            result = narration_mod.generate(
                topic=topic, n_scenes=n_scenes,
                config={'llm': self.llm_cfg},
                prompts=self.prompts,
            )
            narrations = result.narrations
            video_title = result.title or topic
            db.update_video_job(
                job_id,
                title=video_title,
                narrations=narrations,
            )

        # 2. asset: 用户图 + 默认补足
        assets = _resolve_assets(
            user_image_paths=user_images,
            n_needed=len(narrations),
            defaults_dir=self.defaults_dir,
            bgm_path=bgm_input,
        )
        if len(assets.images) != len(narrations):
            raise VideoJobError(
                f'image 数 {len(assets.images)} 与 narration 数 {len(narrations)} 不一致'
            )

        # 3. TTS
        tts_items: list[tts_mod.TTSItem] = []
        for i, n in enumerate(narrations, 1):
            tts_items.append(tts_mod.TTSItem(
                text=n,
                out_path=audio_dir / f'scene_{i:02d}.mp3',
            ))
        log.info('[pipeline] TTS %d 段 voice=%s rate=%s', len(tts_items), voice, rate)
        try:
            tts_mod.synthesize_batch(tts_items, voice=voice, rate=rate)
        except Exception as e:
            raise VideoJobError(f'TTS 失败: {e}') from e

        # 4. 量时长 + 写 input.json
        scenes = []
        for i, (n, item, asset) in enumerate(zip(narrations, tts_items, assets.images), 1):
            dur = tts_mod.probe_duration_seconds(item.out_path)
            scenes.append({
                'asset':       str(asset.resolve()),
                'assetType':   'image',
                'narration':   n,
                'audio':       str(item.out_path.resolve()),
                'durationSec': round(dur + SCENE_TAIL_GAP, 3),
            })
        total_sec = sum(s['durationSec'] for s in scenes)

        input_json = {
            'title':     video_title,
            'scenes':    scenes,
            'fps':       self.fps,
            'width':     self.width,
            'height':    self.height,
            'bgm':       str(assets.bgm.resolve()) if assets.bgm else None,
            'bgmVolume': float(job.get('bgm_volume') or 0.2),
            'outputName': 'output.mp4',
        }
        (job_dir / 'input.json').write_text(
            json.dumps(input_json, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        log.info('[pipeline] 总时长 %.2fs, %d scenes → 调 remotion 渲染', total_sec, len(scenes))

        # 5. 渲染
        try:
            out_mp4 = remotion_mod.render_job(job_dir)
        except Exception as e:
            raise VideoJobError(f'remotion 渲染失败: {e}') from e

        # 6. 写 DB
        size_bytes = out_mp4.stat().st_size
        db.update_video_job(
            job_id,
            status='done',
            output_path=str(out_mp4.resolve()),
            duration_sec=round(total_sec, 2),
            file_size=size_bytes,
        )
        return out_mp4
