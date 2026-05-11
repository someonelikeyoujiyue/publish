"""微信草稿 JSON API。"""
from __future__ import annotations

import logging

import markdown2
from fastapi import APIRouter, HTTPException, Request

from ...config import get_user
from ...feishu import FeishuBot
from ...rewrite import RewriteEngine
from ...wechat import WeChatPublisher

log = logging.getLogger('publisher_hub.api.wechat')
router = APIRouter()
PLATFORM = 'wechat'


def _draft_summary(d: dict) -> dict:
    return {
        'id':         d['id'],
        'title':      d.get('title') or '',
        'status':     d.get('status'),
        'created_at': str(d.get('created_at') or ''),
        'pushed_at':  str(d.get('pushed_at') or '') if d.get('pushed_at') else None,
        'error':      d.get('error_msg') or None,
    }


@router.get('/users/{user_id}/wechat/drafts')
def list_drafts(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404, f'用户 {user_id} 不存在')
    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return {'drafts': [_draft_summary(d) for d in drafts]}


@router.get('/users/{user_id}/wechat/drafts/{draft_id}')
def get_draft(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    content_html = markdown2.markdown(
        draft.get('content') or '',
        extras=['fenced-code-blocks', 'tables', 'break-on-newline', 'cuddled-lists'],
    )
    images = [u.strip() for u in (draft.get('image_urls') or '').split(',') if u.strip()]
    return {
        'id':           draft['id'],
        'title':        draft.get('title') or '',
        'content':      draft.get('content') or '',
        'content_html': content_html,
        'images':       images,
        'status':       draft.get('status'),
        'created_at':   str(draft.get('created_at') or ''),
        'pushed_at':    str(draft.get('pushed_at') or '') if draft.get('pushed_at') else None,
        'pushed_result': draft.get('pushed_result') or '',
        'error':        draft.get('error_msg') or None,
    }


@router.post('/users/{user_id}/wechat/refresh')
def refresh(user_id: str, request: Request):
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
        log.error('[api/wechat] refresh %s 异常: %s', user_id, e, exc_info=True)
        return {'ok': False, 'error': str(e)}


@router.post('/users/{user_id}/wechat/drafts/{draft_id}/push')
def push(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    publisher = WeChatPublisher(user.get('wechat') or {})
    result = publisher.push(draft)
    bot = FeishuBot(config)
    user_name = user.get('name') or user_id
    title     = draft.get('title') or '(无标题)'

    if result['ok']:
        media_id = result.get('media_id', '')
        db.mark_pushed(draft_id, pushed_result=f'media_id={media_id}')
        bot.push_success(user_name, PLATFORM, title, f'media_id: `{media_id}`')
        return {'ok': True, 'media_id': media_id}

    err = result.get('error', '未知错误')
    db.mark_failed(draft_id, error_msg=err)
    bot.push_failed(user_name, PLATFORM, title, err)
    return {'ok': False, 'error': err}
