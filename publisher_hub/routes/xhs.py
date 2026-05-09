"""小红书列表 / 详情 / 推送 / 手动仿写。"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..config import get_user
from ..feishu import FeishuBot
from ..rewrite import RewriteEngine
from ..xhs import XhsPublisher

log = logging.getLogger('publisher_hub.routes.xhs')

router = APIRouter()
templates = Jinja2Templates(directory='templates')

PLATFORM = 'xhs'


@router.get('/{user_id}/xhs', response_class=HTMLResponse)
def xhs_list(user_id: str, request: Request):
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


@router.get('/{user_id}/xhs/{draft_id}', response_class=HTMLResponse)
def xhs_detail(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    images = [u.strip() for u in (draft.get('image_urls') or '').split(',') if u.strip()]

    # 已推送：从 pushed_result JSON 提取 qr_url，让用户随时回来扫码
    qr_url = ''
    if draft.get('status') == 'pushed' and draft.get('pushed_result'):
        try:
            data = json.loads(draft['pushed_result'])
            qr_url = (data or {}).get('qr_url') or ''
        except Exception as e:
            log.debug('[xhs] pushed_result 解析失败 id=%s: %s', draft_id, e)

    return templates.TemplateResponse(request, 'detail.html', {
        'user':         user,
        'draft':        draft,
        'content_html': '',          # 小红书是纯文本，模板用 content 字段直接渲染
        'images':       images,
        'platform':     PLATFORM,
        'qr_url':       qr_url,      # pushed 状态时模板用来展示二维码
    })


@router.post('/{user_id}/xhs/refresh', response_class=HTMLResponse)
def xhs_refresh(user_id: str, request: Request):
    config  = request.app.state.config
    prompts = request.app.state.prompts
    db      = request.app.state.db
    user    = get_user(config, user_id)
    if not user:
        raise HTTPException(404)

    try:
        engine = RewriteEngine(config, prompts)
        n = engine.run_user(user_id, PLATFORM, db)
        flash = f'仿写完成，新增 {n} 条' if n else '仿写完成，无新增（无可仿写素材）'
    except Exception as e:
        log.error('[refresh] %s/xhs 异常: %s', user_id, e, exc_info=True)
        flash = f'仿写失败：{e}'

    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return templates.TemplateResponse(request, '_list_partial.html', {
        'user':     user,
        'drafts':   drafts,
        'platform': PLATFORM,
        'flash':    flash,
    })


@router.post('/{user_id}/xhs/{draft_id}/push', response_class=HTMLResponse)
def xhs_push(user_id: str, draft_id: int, request: Request):
    """调 myaibot 生成发布二维码。"""
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
        # 只存 qr_url（base64 二维码可达 30KB，不存完整 raw 避免 TEXT 列溢出）
        db.mark_pushed(
            draft_id,
            pushed_result=json.dumps({'qr_url': qr}, ensure_ascii=False),
        )
        bot.push_success(user_name, PLATFORM, title,
                         f'扫码：{qr}' if qr else '已自动发布（无需扫码）')
        log.info('[xhs] ✓ 推送成功 user=%s draft_id=%s qr=%s',
                 user_id, draft_id, qr[:60] if qr else '(none)')
        return templates.TemplateResponse(request, '_qr_modal.html', {
            'qr_url': qr,
            'title':  title,
        })

    err = result.get('error', '未知错误')
    db.mark_failed(draft_id, error_msg=err)
    bot.push_failed(user_name, PLATFORM, title, err)
    log.warning('[xhs] ✗ 推送失败 user=%s draft_id=%s: %s', user_id, draft_id, err)
    return templates.TemplateResponse(request, '_push_failed.html', {
        'error':         err,
        'retry_url':     f'/{user_id}/xhs/{draft_id}/push',
        'confirm_msg':   '重新生成小红书发布二维码？',
        'btn_label':     '重试生成',
        'loading_label': '生成中',
    })
