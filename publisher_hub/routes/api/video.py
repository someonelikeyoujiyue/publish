"""短视频生成 JSON API。

POST   /api/users/{user_id}/video/generate   提交一条视频任务（multipart）
GET    /api/users/{user_id}/video/jobs       列出所有任务
GET    /api/users/{user_id}/video/jobs/{id}  查单条
DELETE /api/users/{user_id}/video/jobs/{id}  删（admin only，清磁盘 + DB）
"""
from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
)

from ...config import get_user
from ...video_gen import VideoGenPipeline, VideoJobError
from ...video_gen.pipeline import default_video_jobs_dir
from ...video_gen.tts import VOICE_PRESETS, DEFAULT_VOICE_KEY
from .auth import require_admin, require_editor, require_login

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


@router.post('/users/{user_id}/video/generate', status_code=202)
async def generate_video(
    user_id: str,
    request: Request,
    topic:      str = Form(''),
    title:      str = Form(''),
    narrations: str = Form(''),   # 用户文案：每行一段；空 = 让 LLM 生
    n_scenes:   int = Form(3),
    voice:      str = Form(DEFAULT_VOICE_KEY),
    rate:       str = Form('+5%'),
    images:     list[UploadFile] = File(default=[]),
    _=Depends(require_editor),    # admin / editor 可提交；user 不行
):
    """提交一条视频生成任务。

    形式：
      - **完全自动**：只填 topic → LLM 生 narration + 默认 RSU 图
      - **半自动**：topic + 自传 narrations / images → 缺什么 LLM 或默认补
      - **完全手动**：narrations + images 都给 → 不调 LLM

    返回：202 {job_id, status='pending'}，前端轮询 /jobs/{id} 看进度。
    """
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404, '用户不存在')

    # 解析 narrations：按换行切，去空行
    narration_lines = [l.strip() for l in (narrations or '').splitlines() if l.strip()]
    # 都空也允许：pipeline 内部会从默认话题列表挑一个跑（前端提交前已弹确认）

    # 1. 先插 DB 拿 job_id
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

    log.info(
        '[video] job=%d submitted by user=%s topic=%r narr=%d imgs=%d',
        job_id, user_id, topic[:40], len(narration_lines), len(user_image_paths),
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
    user_id: str, job_id: int, request: Request, _=Depends(require_admin),
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


# ── 配置 / 选项端点（前端下拉用） ────────────────────────────────────────────

@router.get('/video/options')
def options(_=Depends(require_login)):
    """前端用：列出可选音色 + 默认 rate。"""
    return {
        'voices':         [{'key': k, 'code': v} for k, v in VOICE_PRESETS.items()],
        'default_voice':  DEFAULT_VOICE_KEY,
        'default_rate':   '+5%',
    }
