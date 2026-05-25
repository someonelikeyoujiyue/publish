"""小红书草稿 JSON API。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ...config import get_user
from ...feishu import FeishuBot
from ...rewrite import RewriteEngine
from ...xhs import XhsPublisher
from ._helpers import parse_images
from .auth import require_editor

log = logging.getLogger('publisher_hub.api.xhs')
router = APIRouter()
PLATFORM = 'xhs'


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


@router.get('/users/{user_id}/xhs/drafts')
def list_drafts(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return {'drafts': [_draft_summary(d) for d in drafts]}


@router.get('/users/{user_id}/xhs/drafts/{draft_id}')
def get_draft(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    images = parse_images(draft.get('image_urls') or '')
    qr_url = ''
    if draft.get('status') == 'pushed' and draft.get('pushed_result'):
        try:
            qr_url = (json.loads(draft['pushed_result']) or {}).get('qr_url') or ''
        except Exception:
            pass
    return {
        'id':         draft['id'],
        'title':      draft.get('title') or '',
        'content':    draft.get('content') or '',
        'images':     images,
        'status':     draft.get('status'),
        'created_at': str(draft.get('created_at') or ''),
        'pushed_at':  str(draft.get('pushed_at') or '') if draft.get('pushed_at') else None,
        'qr_url':     qr_url,
        'error':      draft.get('error_msg') or None,
    }


@router.post('/users/{user_id}/xhs/refresh')
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
        log.error('[api/xhs] refresh %s 异常: %s', user_id, e, exc_info=True)
        return {'ok': False, 'error': str(e)}


@router.post('/users/{user_id}/xhs/drafts/{draft_id}/push')
def push(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    publisher = XhsPublisher(config)
    images    = [u.strip() for u in (draft.get('image_urls') or '').split(',') if u.strip()]
    result = publisher.push(
        title   = draft.get('title') or '',
        content = draft.get('content') or '',
        images  = images,
    )
    bot       = FeishuBot(config)
    user_name = user.get('name') or user_id
    title     = draft.get('title') or '(无标题)'

    if result['ok']:
        qr = result.get('qr_url') or ''
        db.mark_pushed(
            draft_id,
            pushed_result=json.dumps({'qr_url': qr}, ensure_ascii=False),
        )
        bot.push_success(user_name, PLATFORM, title,
                         f'扫码：{qr}' if qr else '已自动发布（无需扫码）')
        return {'ok': True, 'qr_url': qr}

    err = result.get('error', '未知错误')
    db.mark_failed(draft_id, error_msg=err)
    bot.push_failed(user_name, PLATFORM, title, err)
    return {'ok': False, 'error': err}
