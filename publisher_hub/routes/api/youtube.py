"""YouTube 视频处理 JSON API。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...config import get_user
from ...youtube import processor
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
