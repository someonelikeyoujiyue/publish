"""今日头条号绑定页 + 扫码 + 登录态轮询 + 解绑。

端点：
  GET  /{user_id}/toutiao            主面板（含 tab + 状态卡 + 扫码按钮）
  GET  /{user_id}/toutiao/status     登录态片段（HTMX 每 5s 轮询）
  POST /{user_id}/toutiao/bind       启 Chrome + 截二维码（HTMX 替换 action 区）
  POST /{user_id}/toutiao/unbind     清 user_data_dir + 清 yaml.toutiao 段
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import config as cfg
from ..toutiao_browser import allocate_port, get_browser

log = logging.getLogger('publisher_hub.routes.toutiao')
router = APIRouter()
templates = Jinja2Templates(directory='templates')


@router.get('/{user_id}/toutiao', response_class=HTMLResponse)
def panel(user_id: str, request: Request):
    user = cfg.get_user(request.app.state.config, user_id)
    if not user:
        raise HTTPException(404, f'用户 {user_id} 不存在')
    return templates.TemplateResponse(request, 'toutiao_panel.html', {
        'user':     user,
        'platform': 'toutiao',
    })


@router.get('/{user_id}/toutiao/status', response_class=HTMLResponse)
async def status(user_id: str, request: Request):
    config = request.app.state.config
    user = cfg.get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    tt = user.get('toutiao') or {}
    port = tt.get('cdp_port')

    if not port:
        state = {'status': 'unbound'}
    else:
        try:
            browser = get_browser(user_id, int(port))
            state = await browser.check_login()
        except Exception as e:
            log.exception('[toutiao] %s check_login 路由层异常', user_id)
            state = {'status': 'logged_out', 'reason': str(e)}

    return templates.TemplateResponse(request, '_toutiao_status.html', {
        'user':  user,
        'state': state,
    })


@router.post('/{user_id}/toutiao/bind', response_class=HTMLResponse)
async def bind(user_id: str, request: Request):
    config = request.app.state.config
    user = cfg.get_user_raw(config, user_id)
    if not user:
        raise HTTPException(404)

    # 分配端口（如果还没分配）
    port = ((user.get('toutiao') or {}).get('cdp_port'))
    if not port:
        port = allocate_port(config, exclude_user_id=user_id)
        try:
            cfg.set_user_toutiao_port(user_id, port)
            cfg.reload_app_state(request.app)
        except Exception as e:
            log.exception('[toutiao] 写 yaml 失败')
            return HTMLResponse(
                f'<div class="badge badge-failed" style="padding:8px 12px;">'
                f'✗ 端口分配失败：{e}</div>',
            )

    # 启 Chrome + 截二维码
    try:
        browser = get_browser(user_id, int(port))
        img_data = await browser.capture_login_page()
        browser.invalidate_cache()      # 让下次 status 检测立刻跑（用户扫码后能尽快变绿）
    except Exception as e:
        log.exception('[toutiao] capture 路由层异常')
        return HTMLResponse(
            f'<div class="badge badge-failed" style="padding:8px 12px;">'
            f'✗ 启 Chrome 异常：{e}</div>',
        )

    # 已登录用户点扫码 → 友好提示
    from ..toutiao_browser import ToutiaoBrowser
    if img_data == ToutiaoBrowser.ALREADY_LOGGED_IN:
        return HTMLResponse(
            '<div class="badge badge-pushed" style="padding:10px 14px;">'
            '✓ 该 Chrome 实例已经登录头条号了，无需重新扫码。<br>'
            '<span style="font-weight:normal;font-size:12px;">'
            '如果要换头条号，先点 🗑 解绑 清除登录态再重新扫。</span></div>',
        )

    if not img_data:
        return HTMLResponse(
            '<div class="badge badge-failed" style="padding:8px 12px;">'
            '✗ 截图失败（可能 page.goto 超时或被反爬挡）。'
            '看日志: <code>journalctl -u publisher-hub -n 50</code></div>',
        )

    return templates.TemplateResponse(request, '_toutiao_qr.html', {
        'user':     user,
        'img_data': img_data,
        'port':     port,
    })


@router.post('/{user_id}/toutiao/unbind', response_class=HTMLResponse)
async def unbind(user_id: str, request: Request):
    config = request.app.state.config
    user = cfg.get_user_raw(config, user_id)
    if not user:
        raise HTTPException(404)

    port = ((user.get('toutiao') or {}).get('cdp_port'))
    if port:
        try:
            browser = get_browser(user_id, int(port))
            browser.unbind()
        except Exception as e:
            log.warning('[toutiao] unbind 浏览器侧异常: %s', e)
    cfg.unset_user_toutiao(user_id)
    cfg.reload_app_state(request.app)

    return HTMLResponse(
        '<div class="badge badge-pushed" style="padding:8px 12px;">'
        '✓ 已解绑（已删 user_data_dir + 清 yaml）。刷新页面重新绑。</div>',
    )
