"""短视频生成 JSON API。

POST   /api/users/{user_id}/video/preview    跑 LLM 出 narration 不渲染（给用户预览/微调）
POST   /api/users/{user_id}/video/generate   正式提交一条视频任务（multipart）
GET    /api/users/{user_id}/video/jobs       列出所有任务
GET    /api/users/{user_id}/video/jobs/{id}  查单条
DELETE /api/users/{user_id}/video/jobs/{id}  删（admin only，清磁盘 + DB）
GET    /api/video/options                    voice / bgm 选项列表
GET    /api/video/default-image/{name}       流默认 RSU 图给前端预览用
"""
from __future__ import annotations

import logging
import random
import re
import shutil
import threading
from pathlib import Path

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...config import get_user
from ...video_gen import VideoGenPipeline, VideoJobError
from ...video_gen import narration as narration_mod
from ...video_gen import seed_post as seed_post_mod
from ...video_gen.pipeline import (
    DEFAULT_N_SCENES, DEFAULT_TOPIC_FALLBACK, default_assets_dir,
    default_video_jobs_dir,
)
from ...video_gen.tts import VOICE_PRESETS, DEFAULT_VOICE_KEY
from .auth import require_editor, require_login

log = logging.getLogger('publisher_hub.api.video')
router = APIRouter()


def _job_summary(j: dict) -> dict:
    """白名单序列化 + 把绝对路径转成前端可点的 URL（/video-jobs/<job>/output.mp4）。

    生产环境 nginx 配 alias /video-jobs/ → /data/publisher-hub/data/video-jobs/。
    """
    out = j.get('output_path') or ''
    video_url = ''
    if out:
        m = re.search(r'/video-jobs/(\d+)/(.+)$', out)
        if m:
            video_url = f'/video-jobs/{m.group(1)}/{m.group(2)}'
    return {
        'id':           j['id'],
        'user_id':      j['user_id'],
        'topic':        j.get('topic') or '',
        'title':        j.get('title') or '',
        'narrations':   j.get('narrations') or [],
        'image_count':  len(j.get('image_paths') or []),
        'status':       j.get('status'),
        'video_url':    video_url,
        'duration_sec': j.get('duration_sec'),
        'file_size':    j.get('file_size'),
        'error':        j.get('error_msg') or None,
        'created_at':   str(j.get('created_at') or ''),
        'updated_at':   str(j.get('updated_at') or ''),
    }


@router.get('/users/{user_id}/video/jobs')
def list_jobs(user_id: str, request: Request, _=Depends(require_login)):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    rows = db.list_video_jobs(user_id, limit=100)
    return {'jobs': [_job_summary(j) for j in rows]}


@router.get('/users/{user_id}/video/jobs/{job_id}')
def get_job(user_id: str, job_id: int, request: Request, _=Depends(require_login)):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    j = db.get_video_job(job_id)
    if not j or j['user_id'] != user_id:
        raise HTTPException(404)
    return _job_summary(j)


# ── 提交（multipart：表单字段 + 0-N 张图片） ─────────────────────────────────

async def _save_upload(uf: UploadFile, dest: Path) -> None:
    """流式落盘。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, 'wb') as f:
        while True:
            chunk = await uf.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


_SAFE_FN_RE = re.compile(r'[^A-Za-z0-9._-]+')


def _safe_filename(name: str) -> str:
    """剥掉路径字符 / 中文 / 空格等不便 stage 的字符。保留 ext。"""
    name = name.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    name = _SAFE_FN_RE.sub('_', name)
    return name[:80] or 'upload'


# ── 智能分段：用户粘一大坨文案进来自动切成 TTS/字幕友好的短句 ──────────────────

_SENT_END   = r'。！？!?;；'
_SOFT_BREAK = r'，、,'
_LONG_LINE_THRESHOLD = 35   # 单行 > 这个字符数才触发拆分
_TARGET_MAX = 30            # 期望段长上限
_HARD_MAX   = 45            # 段长不能超的硬上限
_MIN_CHARS  = 10            # 段长不能少于这个（少了跟前一段合并）


def _smart_split_narration(text: str) -> list[str]:
    """把一段长文案切成短句。

    优先级：句末标点 (。！？；) > 软标点 (，、) > 硬切。
    保证每段在 [MIN_CHARS, HARD_MAX] 之间。

    例：
      入："兰实大学位于泰国曼谷北部，是泰国最大的私立大学之一。本科学费每年约 7-9 万。"
      出：["兰实大学位于泰国曼谷北部", "是泰国最大的私立大学之一。", "本科学费每年约 7-9 万。"]
    """
    if not text or not text.strip():
        return []

    # Step 1: 按句末标点切（保留标点在句末）
    parts = re.split(rf'(?<=[{_SENT_END}])\s*', text.strip())
    parts = [p.strip() for p in parts if p.strip()]

    # Step 2: 单段仍 > HARD_MAX → 按软标点继续切，聚合到 ≤ TARGET_MAX
    refined: list[str] = []
    for p in parts:
        if len(p) <= _HARD_MAX:
            refined.append(p)
            continue
        sub = re.split(rf'(?<=[{_SOFT_BREAK}])\s*', p)
        sub = [s.strip() for s in sub if s.strip()]
        buf = ''
        for s in sub:
            if len(buf) + len(s) <= _TARGET_MAX:
                buf += s
            else:
                if buf:
                    refined.append(buf)
                buf = s
        if buf:
            refined.append(buf)

    # Step 3: 太短的（< MIN_CHARS）跟前一段合并
    merged: list[str] = []
    for s in refined:
        if merged and len(merged[-1]) < _MIN_CHARS:
            merged[-1] = merged[-1] + s
        else:
            merged.append(s)

    # Step 4: 极端无标点 / 仍然超长 → 硬切
    final: list[str] = []
    for s in merged:
        if len(s) <= _HARD_MAX:
            final.append(s)
        else:
            for i in range(0, len(s), _TARGET_MAX):
                final.append(s[i:i + _TARGET_MAX])

    return final


def _normalize_narrations(raw: str) -> list[str]:
    """处理用户提交的 narrations textarea 内容：

    1. 按换行切
    2. 每行如果太长（> LONG_LINE_THRESHOLD）走 smart split；否则原样保留
    3. 去重去空
    """
    out: list[str] = []
    for line in (raw or '').splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= _LONG_LINE_THRESHOLD:
            out.append(line)
        else:
            pieces = _smart_split_narration(line)
            out.extend(pieces if pieces else [line])
    return out


@router.post('/users/{user_id}/video/generate', status_code=202)
async def generate_video(
    user_id: str,
    request: Request,
    topic:      str = Form(''),
    title:      str = Form(''),
    narrations: str = Form(''),   # 用户文案：每行一段；空 = 让 LLM 生
    n_scenes:   int = Form(3),
    voice:      str = Form(DEFAULT_VOICE_KEY),   # 'none' = 无配音
    rate:       str = Form('+5%'),
    bgm_mode:   str = Form('default'),           # default | upload | none
    images:     list[UploadFile] = File(default=[]),
    bgm:        UploadFile | None = File(default=None),
    _=Depends(require_editor),    # admin / editor 可提交；user 不行
):
    """提交一条视频生成任务。

    形式：
      - **完全自动**：只填 topic → LLM 生 narration + 默认 RSU 图
      - **半自动**：topic + 自传 narrations / images → 缺什么 LLM 或默认补
      - **完全手动**：narrations + images 都给 → 不调 LLM

    bgm_mode:
      - 'default' (默认) → 用 assets/video-defaults/bgm-default.mp3
      - 'upload'         → bgm 文件必传，保存到 job/bgm/uploaded.mp3
      - 'none'           → 无 BGM

    voice='none' → 静音模式，每段按字数生静音 mp3 控时长（BGM 还能正常播）

    返回：202 {job_id, status='pending'}，前端轮询 /jobs/{id} 看进度。
    """
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404, '用户不存在')

    # 解析 narrations：按换行切 + 长段智能拆句（用户粘一大坨也能用）
    raw_line_count = sum(1 for l in (narrations or '').splitlines() if l.strip())
    narration_lines = _normalize_narrations(narrations or '')
    if raw_line_count and len(narration_lines) != raw_line_count:
        log.info('[video] narration smart-split: %d 行 → %d 段', raw_line_count, len(narration_lines))
    # 都空也允许：pipeline 内部会从默认话题列表挑一个跑（前端提交前已弹确认）

    # 1. 先插 DB 拿 job_id（bgm_path 暂留空，下面上传完了再 update）
    job_id = db.create_video_job(
        user_id=user_id,
        topic=topic or '',
        title=title or '',
        narrations=narration_lines,
        n_scenes=n_scenes,
        voice=voice,
        rate=rate,
    )

    # 2. 用 job_id 建目录，落用户上传的图，更新 image_paths
    job_dir = default_video_jobs_dir() / str(job_id)
    image_dir = job_dir / 'images'
    user_image_paths: list[str] = []
    for i, uf in enumerate(images or [], 1):
        # 跳过空 upload（前端 input 即使为空也会发一个）
        if not uf or not uf.filename:
            continue
        # 校验：必须是图片
        ct = (uf.content_type or '').lower()
        if not ct.startswith('image/'):
            log.warning('[video] job=%d 跳过非图: %s (%s)', job_id, uf.filename, ct)
            continue
        fn = f'{i:02d}_{_safe_filename(uf.filename)}'
        dest = image_dir / fn
        await _save_upload(uf, dest)
        user_image_paths.append(str(dest.resolve()))
    if user_image_paths:
        db.update_video_job(job_id, image_paths=user_image_paths)

    # 2.5 处理 BGM
    from ...video_gen.pipeline import BGM_NONE_SENTINEL
    bgm_mode_norm = (bgm_mode or 'default').strip().lower()
    if bgm_mode_norm == 'none':
        db.update_video_job(job_id, bgm_path=BGM_NONE_SENTINEL)
        log.info('[video] job=%d 用户选择无 BGM', job_id)
    elif bgm_mode_norm == 'upload':
        if not bgm or not bgm.filename:
            raise HTTPException(400, 'bgm_mode=upload 但没上传 bgm 文件')
        ct = (bgm.content_type or '').lower()
        if not (ct.startswith('audio/') or ct == 'application/octet-stream'):
            raise HTTPException(400, f'bgm 文件类型不对: {ct}')
        bgm_dir = job_dir / 'bgm'
        bgm_dest = bgm_dir / f'uploaded_{_safe_filename(bgm.filename)}'
        await _save_upload(bgm, bgm_dest)
        db.update_video_job(job_id, bgm_path=str(bgm_dest.resolve()))
        log.info('[video] job=%d BGM 上传 → %s', job_id, bgm_dest.name)
    else:
        # 默认模式 - bgm_path 留空，pipeline 自动取 assets/video-defaults/bgm-default.mp3
        pass

    log.info(
        '[video] job=%d submitted by user=%s topic=%r narr=%d imgs=%d voice=%s bgm_mode=%s',
        job_id, user_id, topic[:40], len(narration_lines), len(user_image_paths),
        voice, bgm_mode_norm,
    )

    # 3. 丢后台线程跑（pipeline 内部会改 status processing → done / failed）
    def _run():
        try:
            pipeline = VideoGenPipeline(
                config=request.app.state.config,
                prompts=request.app.state.prompts,
            )
            pipeline.run_job(job_id, request.app.state.db)
        except VideoJobError as e:
            log.warning('[video] job=%d pipeline 失败: %s', job_id, e)
        except Exception:
            log.exception('[video] job=%d 未捕获异常', job_id)

    threading.Thread(target=_run, name=f'video-job-{job_id}', daemon=True).start()

    return {'ok': True, 'job_id': job_id, 'status': 'pending'}


# ── 删除（含磁盘清理；admin only） ──────────────────────────────────────────

@router.delete('/users/{user_id}/video/jobs/{job_id}', status_code=200)
def delete_job(
    user_id: str, job_id: int, request: Request, _=Depends(require_editor),
):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    j = db.get_video_job(job_id)
    if not j or j['user_id'] != user_id:
        raise HTTPException(404)
    if j.get('status') == 'processing':
        raise HTTPException(409, '任务还在运行，等完成再删')

    job_dir = default_video_jobs_dir() / str(job_id)
    dir_removed = False
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        dir_removed = True

    affected = db.delete_video_job(job_id)
    log.info('[video] delete job=%d dir_removed=%s db_rows=%d', job_id, dir_removed, affected)
    return {'ok': True, 'job_id': job_id, 'dir_removed': dir_removed}


# ── preview：跑 LLM 出 narration 不渲染，让用户先看再改 ─────────────────────

class PreviewBody(BaseModel):
    topic:      str = ''
    narrations: str = ''      # 多行文本，跟 generate 用同一格式（自动 smart-split）
    n_scenes:   int = DEFAULT_N_SCENES


@router.post('/users/{user_id}/video/preview')
def preview_narration(
    user_id: str,
    body: PreviewBody,
    request: Request,
    _=Depends(require_editor),
):
    """跑 LLM / smart-split / 挑默认图 —— 但不渲染视频。前端拿到结果让用户改。

    返回：
      {
        topic:      "（实际用的 topic；用户提供就原样，否则 LLM 种子）",
        title:      "（LLM 起的或用户给的）",
        narrations: [ "...", ... ],
        default_image_urls: [ "/api/video/default-image/RSU.jpg", ... ],
        source:     "user_narrations" | "user_topic" | "db_seed" | "fallback"
      }
    """
    config  = request.app.state.config
    prompts = request.app.state.prompts
    db      = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)

    topic = (body.topic or '').strip()
    narration_lines = _normalize_narrations(body.narrations or '')
    n_scenes = max(2, min(8, int(body.n_scenes or DEFAULT_N_SCENES)))

    # ── 拿 narrations ──
    title = ''
    source = ''
    if narration_lines:
        # 用户已经给文案了：直接用（仍然 smart-split 过的）
        narrations = narration_lines
        title = topic[:40]   # 没好标题就用 topic 截一段
        source = 'user_narrations'
    else:
        # 没有 narration：要么用户给了 topic，要么我们从 DB 拉
        if not topic:
            seed = seed_post_mod.fetch_seed_topic(
                user_id=user_id, user=get_user(config, user_id), db=db,
            )
            if seed:
                topic = seed
                source = 'db_seed'
            else:
                topic = DEFAULT_TOPIC_FALLBACK
                source = 'fallback'
        else:
            source = 'user_topic'

        # 跑 LLM
        try:
            result = narration_mod.generate(
                topic=topic, n_scenes=n_scenes,
                config={'llm': config.get('llm') or {}},
                prompts=prompts,
            )
        except Exception as e:
            log.warning('[preview] LLM 失败 user=%s topic=%r: %s', user_id, topic[:40], e)
            raise HTTPException(502, f'LLM 生成失败: {e}')
        narrations = result.narrations
        title = result.title or topic[:40]

    # ── 挑默认图（n_scenes 数量；URL 走 /api/video/default-image/<name>）──
    defaults_dir = default_assets_dir()
    candidates = sorted([
        p.name for p in defaults_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}
    ]) if defaults_dir.exists() else []
    picked: list[str] = []
    if candidates:
        random.shuffle(candidates)
        while len(picked) < len(narrations):
            picked.extend(candidates)
        picked = picked[:len(narrations)]
    image_urls = [f'/api/video/default-image/{n}' for n in picked]

    log.info(
        '[preview] user=%s source=%s narrations=%d title=%r imgs=%d',
        user_id, source, len(narrations), title[:40], len(image_urls),
    )
    return {
        'topic':              topic,
        'title':              title,
        'narrations':         narrations,
        'default_image_urls': image_urls,
        'source':             source,
    }


# ── 配置 / 选项端点（前端下拉用） ────────────────────────────────────────────

@router.get('/video/options')
def options(_=Depends(require_login)):
    """前端用：列出可选音色 + 默认 rate。"""
    return {
        'voices':         [{'key': k, 'code': v} for k, v in VOICE_PRESETS.items()],
        'default_voice':  DEFAULT_VOICE_KEY,
        'default_rate':   '+5%',
        # 'none' 不在 VOICE_PRESETS 里，前端单独加这个选项；后端 pipeline 见到就走静音
        'silent_voice':   'none',
    }


# ── 默认图 流式 ─────────────────────────────────────────────────────────────

_DEFAULT_IMAGE_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+\.(jpe?g|png|webp)$', re.IGNORECASE)


@router.get('/video/default-image/{name}')
def default_image(name: str):
    """流 assets/video-defaults/<name>。给 preview 的 <img src=...> 用，
    所以不能要求 Authorization header（<img> 不带 token）。

    硬限制文件名为白名单字符（防路径穿越）。默认图本身不敏感（公开校园照）。
    """
    if not _DEFAULT_IMAGE_NAME_RE.match(name):
        raise HTTPException(400, '非法文件名')
    path = default_assets_dir() / name
    if not path.exists() or not path.is_file():
        raise HTTPException(404, '默认图不存在')
    return FileResponse(str(path))
