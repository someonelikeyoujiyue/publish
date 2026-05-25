"""抖音图文草稿 JSON API（无 push 端点 —— 用户手动复制 + 跳转抖音发布）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config import get_user
from ...rewrite import RewriteEngine
from ._helpers import parse_images
from .auth import require_editor

log = logging.getLogger('publisher_hub.api.douyin_drafts')
router = APIRouter()
PLATFORM = 'douyin'


def _draft_summary(d: dict) -> dict:
    images = parse_images(d.get('image_urls') or '')
    return {
        'id':         d['id'],
        'title':      d.get('title') or '',
        'status':     d.get('status'),
        'created_at': str(d.get('created_at') or ''),
        'pushed_at':  str(d.get('pushed_at') or '') if d.get('pushed_at') else None,
        'error':      d.get('error_msg') or None,
        'cover':      images[0] if images else '',
        'image_count': len(images),
    }


@router.get('/users/{user_id}/douyin/drafts')
def list_drafts(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return {'drafts': [_draft_summary(d) for d in drafts]}


@router.get('/users/{user_id}/douyin/drafts/{draft_id}')
def get_draft(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    images = parse_images(draft.get('image_urls') or '')
    return {
        'id':         draft['id'],
        'title':      draft.get('title') or '',
        'content':    draft.get('content') or '',
        'images':     images,
        'status':     draft.get('status'),
        'created_at': str(draft.get('created_at') or ''),
        'pushed_at':  str(draft.get('pushed_at') or '') if draft.get('pushed_at') else None,
        'pushed_result': draft.get('pushed_result') or '',
        'error':      draft.get('error_msg') or None,
    }


@router.post('/users/{user_id}/douyin/refresh')
def refresh(user_id: str, request: Request, _=Depends(require_editor)):
    config  = request.app.state.config
    prompts = request.app.state.prompts
    db      = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    try:
        engine = RewriteEngine(config, prompts)
        n = engine.run_user(user_id, PLATFORM, db)
        return {'ok': True, 'new_count': n}
    except Exception as e:
        log.error('[api/douyin_drafts] refresh %s 异常: %s', user_id, e, exc_info=True)
        return {'ok': False, 'error': str(e)}
