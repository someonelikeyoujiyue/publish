"""YouTube 视频处理 JSON API。"""
from __future__ import annotations

import logging
import re
import shutil
import time
import zlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ...config import get_user
from ...youtube import processor
from ...youtube.pipeline import DEFAULT_OUTPUT_ROOT, extract_video_id
from .auth import require_admin, require_login

log = logging.getLogger('publisher_hub.api.youtube')
router = APIRouter()
PLATFORM = 'youtube'


def _draft_summary(d: dict) -> dict:
    return {
        'id':         d['id'],
        'title':      d.get('title') or '',
        'status':     d.get('status'),
        'created_at': str(d.get('created_at') or ''),
        'pushed_at':  str(d.get('pushed_at') or '') if d.get('pushed_at') else None,
        'error':      d.get('error_msg') or None,
        'source_url': d.get('source_url') or '',
        'video_url':  d.get('pushed_result') or '',
    }


@router.get('/users/{user_id}/youtube/drafts')
def list_drafts(user_id: str, request: Request, _=Depends(require_login)):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return {'drafts': [_draft_summary(d) for d in drafts]}


@router.get('/users/{user_id}/youtube/drafts/{draft_id}')
def get_draft(user_id: str, draft_id: int, request: Request, _=Depends(require_login)):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    return {
        'id':         draft['id'],
        'title':      draft.get('title') or '',
        'content':    draft.get('content') or '',     # bilingual SRT 内容
        'source_url': draft.get('source_url') or '',
        'video_url':  draft.get('pushed_result') or '',
        'status':     draft.get('status'),
        'created_at': str(draft.get('created_at') or ''),
        'pushed_at':  str(draft.get('pushed_at') or '') if draft.get('pushed_at') else None,
        'error':      draft.get('error_msg') or None,
    }


class SubmitBody(BaseModel):
    url:          str  = Field(..., min_length=10)
    strip_hardsub: bool = True
    blur_qr:       bool = False


@router.post('/users/{user_id}/youtube/submit', status_code=202)
def submit(
    user_id: str, body: SubmitBody, request: Request, _=Depends(require_admin),
):
    """提交 YouTube URL，后台异步处理（5-10 分钟）。

    返回 draft_id，前端轮询 /drafts/{id} 看 status：
      processing → pushed (含 video_url) → 完成
                 → failed (含 error)     → 失败
    """
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    try:
        draft_id = processor.submit(
            db, user_id, body.url.strip(),
            strip_hardsub=body.strip_hardsub,
            blur_qr=body.blur_qr,
        )
    except ValueError as e:
        # extract_video_id 抛的：URL 格式不对
        raise HTTPException(400, str(e))
    return {'ok': True, 'draft_id': draft_id, 'status': 'processing'}


# ─── 上传已处理好的视频（用户在 mac / 本地 rangsit 跑完，文件上传过来）───────

_SAFE_SLUG_RE = re.compile(r'[^A-Za-z0-9_-]')


def _derive_slug(source_url: str) -> str:
    """优先用 YouTube video_id（11 字符），否则用时间戳。"""
    if source_url:
        try:
            return extract_video_id(source_url)
        except ValueError:
            pass
    return f'upload_{int(time.time())}'


async def _save_upload(uf: UploadFile, dest: Path):
    """流式落盘，避免大视频内存爆掉。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, 'wb') as f:
        while True:
            chunk = await uf.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


@router.post('/users/{user_id}/youtube/upload', status_code=201)
async def upload(
    user_id: str,
    request: Request,
    video: UploadFile = File(..., description='final mp4 文件'),
    bilingual_srt: UploadFile | None = File(None),
    en_srt:        UploadFile | None = File(None),
    zh_srt:        UploadFile | None = File(None),
    source_url:    str = Form(''),
    title:         str = Form(''),
    _=Depends(require_admin),
):
    """用户在本地处理好视频后上传到服务器（绕过服务器 yt-dlp 反爬问题）。

    必传：video（mp4）
    可选：3 个 srt 文件、source_url（YouTube 链接）、title
    """
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)

    slug = _SAFE_SLUG_RE.sub('_', _derive_slug(source_url.strip()))
    out_dir = DEFAULT_OUTPUT_ROOT / slug
    log.info('[youtube] upload user=%s slug=%s', user_id, slug)

    # 落地视频（必传） + 各 srt（可选）
    final_path = out_dir / 'video_final.mp4'
    await _save_upload(video, final_path)
    if bilingual_srt:
        await _save_upload(bilingual_srt, out_dir / 'subs.bilingual.srt')
    if en_srt:
        await _save_upload(en_srt, out_dir / 'subs.en.srt')
    if zh_srt:
        await _save_upload(zh_srt, out_dir / 'subs.zh-Hans.srt')

    # bilingual srt 内容塞进 content 字段（前端详情页可预览）
    bi_path = out_dir / 'subs.bilingual.srt'
    content_text = bi_path.read_text(encoding='utf-8', errors='replace') if bi_path.exists() else ''

    # 入 hub_drafts
    source_post_id = zlib.crc32(slug.encode()) & 0x7FFFFFFF
    final_title = title.strip() or f'YouTube · {slug}'
    video_url = f'/videos/{slug}/video_final.mp4'

    draft_id = db.save_draft(
        user_id=user_id,
        platform=PLATFORM,
        source_post_id=source_post_id,
        title=final_title,
        content=content_text,
        source_url=source_url.strip(),
        status='pushed',
    )
    db.mark_pushed(draft_id, pushed_result=video_url)

    log.info('[youtube] ✓ upload user=%s draft=%d → %s (%d KB)',
             user_id, draft_id, video_url, final_path.stat().st_size // 1024)

    return {
        'ok':        True,
        'draft_id':  draft_id,
        'video_url': video_url,
        'slug':      slug,
    }
