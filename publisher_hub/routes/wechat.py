"""微信草稿列表 / 详情 / 推送 / 手动仿写。"""
from __future__ import annotations

import logging

import markdown2
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import get_user
from ..feishu import FeishuBot
from ..rewrite import RewriteEngine
from ..wechat import WeChatPublisher

log = logging.getLogger('publisher_hub.routes.wechat')

router = APIRouter()
templates = Jinja2Templates(directory='templates')

PLATFORM = 'wechat'


@router.get('/{user_id}/wechat', response_class=HTMLResponse)
def wechat_list(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = get_user(config, user_id)
    if not user:
        raise HTTPException(404, f'用户 {user_id} 不存在')

    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return templates.TemplateResponse(request, 'list.html', {
        'user':     user,
        'drafts':   drafts,
        'platform': PLATFORM,
    })


@router.get('/{user_id}/wechat/{draft_id}', response_class=HTMLResponse)
def wechat_detail(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    content_html = markdown2.markdown(
        draft.get('content') or '',
        extras=['fenced-code-blocks', 'tables', 'break-on-newline', 'cuddled-lists'],
    )
    images = [u.strip() for u in (draft.get('image_urls') or '').split(',') if u.strip()]

    return templates.TemplateResponse(request, 'detail.html', {
        'user':         user,
        'draft':        draft,
        'content_html': content_html,
        'images':       images,
        'platform':     PLATFORM,
    })


@router.post('/{user_id}/wechat/refresh', response_class=HTMLResponse)
def wechat_refresh(user_id: str, request: Request):
    """手动触发一次仿写，返回更新后的列表片段（HTMX 局部刷新）。"""
    config  = request.app.state.config
    prompts = request.app.state.prompts
    db      = request.app.state.db
    user    = get_user(config, user_id)
    if not user:
        raise HTTPException(404)

    try:
        engine = RewriteEngine(config, prompts)
        n = engine.run_user(user_id, PLATFORM, db)
        flash = f'仿写完成，新增 {n} 条草稿' if n else '仿写完成，无新增（无可仿写素材）'
    except Exception as e:
        log.error('[refresh] %s/wechat 异常: %s', user_id, e, exc_info=True)
        flash = f'仿写失败：{e}'

    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return templates.TemplateResponse(request, '_list_partial.html', {
        'user':     user,
        'drafts':   drafts,
        'platform': PLATFORM,
        'flash':    flash,
    })


@router.post('/{user_id}/wechat/{draft_id}/push', response_class=HTMLResponse)
def wechat_push(user_id: str, draft_id: int, request: Request):
    """真实推送到微信公众号草稿箱。"""
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
        log.info('[wechat] ✓ 推送成功 user=%s draft_id=%s media_id=%s',
                 user_id, draft_id, media_id)
        return HTMLResponse(
            f'<span class="badge badge-pushed" style="padding:8px 12px;">'
            f'✓ 已推送，去 <a href="https://mp.weixin.qq.com" target="_blank" '
            f'style="color:#1e40af;">公众号后台</a> 草稿箱查看</span>',
        )

    err = result.get('error', '未知错误')
    db.mark_failed(draft_id, error_msg=err)
    bot.push_failed(user_name, PLATFORM, title, err)
    log.warning('[wechat] ✗ 推送失败 user=%s draft_id=%s: %s', user_id, draft_id, err)
    return templates.TemplateResponse(request, '_push_failed.html', {
        'error':         err,
        'retry_url':     f'/{user_id}/wechat/{draft_id}/push',
        'confirm_msg':   '重新推送到微信公众号草稿箱？',
        'btn_label':     '重试推送',
        'loading_label': '推送中',
    })
