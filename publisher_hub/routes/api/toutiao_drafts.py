"""头条号微头条草稿 JSON API。

只仿写文字、不生图；推送由 toutiao_publisher 走 CDP DOM 提交（Step 2 实现）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ... import config as cfg
from ...config import get_user
from ...rewrite import RewriteEngine
from ...toutiao_browser import get_browser
from ...toutiao_publisher import publish_weitoutiao
from ...feishu import FeishuBot
from ._helpers import parse_images
from .auth import require_editor

log = logging.getLogger('publisher_hub.api.toutiao_drafts')
router = APIRouter()
PLATFORM = 'toutiao'


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


@router.get('/users/{user_id}/toutiao/drafts')
def list_drafts(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return {'drafts': [_draft_summary(d) for d in drafts]}


@router.get('/users/{user_id}/toutiao/drafts/{draft_id}')
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


@router.post('/users/{user_id}/toutiao/refresh')
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
        log.error('[api/toutiao_drafts] refresh %s 异常: %s', user_id, e, exc_info=True)
        return {'ok': False, 'error': str(e)}


@router.post('/users/{user_id}/toutiao/drafts/{draft_id}/push')
async def push(user_id: str, draft_id: int, request: Request):
    """走 CDP 自动发布微头条。需要用户已扫码绑定头条号。

    body 可选 {"draft_only": true} 只存草稿不发布。
    """
    config = request.app.state.config
    db     = request.app.state.db
    user   = get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    port = ((cfg.get_user_raw(config, user_id) or {}).get('toutiao') or {}).get('cdp_port')
    if not port:
        return {'ok': False, 'error': '未绑定头条号，请先去「头条号」tab 扫码登录'}

    try:
        body = await request.json()
    except Exception:
        body = {}
    draft_only = bool(body.get('draft_only', False))

    title   = draft.get('title') or ''
    content = draft.get('content') or ''
    images  = [u.strip() for u in (draft.get('image_urls') or '').split(',') if u.strip()]

    browser = get_browser(user_id, int(port))
    result = await publish_weitoutiao(
        browser, title, content, images=images, save_draft_only=draft_only,
    )

    bot = FeishuBot(config)
    user_name = user.get('name') or user_id

    if result.get('ok'):
        mode = result.get('mode', 'publish')
        db.mark_pushed(draft_id, pushed_result=f'mode={mode} url={result.get("final_url","")}')
        bot.push_success(user_name, PLATFORM, title or '(无标题)',
                         f'已{("存草稿" if mode == "draft" else "发布")}：{result.get("final_url","")}')
        log.info('[api/toutiao_drafts] ✓ push user=%s draft=%s mode=%s',
                 user_id, draft_id, mode)
        return result

    err = result.get('error', '未知错误')
    db.mark_failed(draft_id, error_msg=err)
    bot.push_failed(user_name, PLATFORM, title or '(无标题)', err)
    log.warning('[api/toutiao_drafts] ✗ push user=%s draft=%s: %s', user_id, draft_id, err)
    return result
