"""头条号扫码绑定 JSON API。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ... import config as cfg
from ...toutiao_browser import allocate_port, get_browser, ToutiaoBrowser

log = logging.getLogger('publisher_hub.api.toutiao')
router = APIRouter()


@router.get('/users/{user_id}/toutiao/status')
async def status(user_id: str, request: Request):
    config = request.app.state.config
    user = cfg.get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    port = (user.get('toutiao') or {}).get('cdp_port')
    if not port:
        return {'status': 'unbound'}
    try:
        browser = get_browser(user_id, int(port))
        return await browser.check_login()
    except Exception as e:
        log.exception('[api/toutiao] %s check_login 异常', user_id)
        return {'status': 'error', 'error': str(e)}


@router.post('/users/{user_id}/toutiao/bind')
async def bind(user_id: str, request: Request):
    config = request.app.state.config
    user = cfg.get_user_raw(config, user_id)
    if not user:
        raise HTTPException(404)

    port = (user.get('toutiao') or {}).get('cdp_port')
    if not port:
        port = allocate_port(config, exclude_user_id=user_id)
        try:
            cfg.set_user_toutiao_port(user_id, port)
            cfg.reload_app_state(request.app)
        except Exception as e:
            log.exception('[api/toutiao] 写 yaml 失败')
            raise HTTPException(500, f'端口分配失败：{e}')

    try:
        browser = get_browser(user_id, int(port))
        img_data = await browser.capture_login_page()
        browser.invalidate_cache()
    except Exception as e:
        log.exception('[api/toutiao] capture 异常')
        raise HTTPException(500, f'启 Chrome 异常：{e}')

    if img_data == ToutiaoBrowser.ALREADY_LOGGED_IN:
        return {'ok': True, 'already_logged_in': True, 'port': port}
    if not img_data:
        raise HTTPException(500, '截图失败（可能 page.goto 超时或被反爬挡）')
    return {'ok': True, 'qr_image': img_data, 'port': port}


@router.post('/users/{user_id}/toutiao/unbind')
async def unbind(user_id: str, request: Request):
    config = request.app.state.config
    user = cfg.get_user_raw(config, user_id)
    if not user:
        raise HTTPException(404)
    port = (user.get('toutiao') or {}).get('cdp_port')
    if port:
        try:
            browser = get_browser(user_id, int(port))
            browser.unbind()
        except Exception as e:
            log.warning('[api/toutiao] unbind 浏览器侧异常: %s', e)
    cfg.unset_user_toutiao(user_id)
    cfg.reload_app_state(request.app)
    return {'ok': True}
