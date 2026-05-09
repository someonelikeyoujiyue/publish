"""用户管理路由（新增 / 编辑 / 删除 / 表单 modal）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from .. import config as cfg

log = logging.getLogger('publisher_hub.routes.users')

router = APIRouter()
templates = Jinja2Templates(directory='templates')


# ── 表单（modal HTMX 加载）────────────────────────────────────────────────────

@router.get('/users/new', response_class=HTMLResponse)
def new_user_form(request: Request):
    return templates.TemplateResponse(request, '_user_form.html', {
        'mode': 'new',
        'user': None,
        'error': '',
    })


@router.get('/users/{user_id}/edit', response_class=HTMLResponse)
def edit_user_form(user_id: str, request: Request):
    config = request.app.state.config
    raw = cfg.get_user_raw(config, user_id)
    if not raw:
        raise HTTPException(404)
    return templates.TemplateResponse(request, '_user_form.html', {
        'mode': 'edit',
        'user': raw,
        'error': '',
    })


# ── 创建 ─────────────────────────────────────────────────────────────────────

@router.post('/users', response_class=HTMLResponse)
def create_user(
    request: Request,
    user_id:           str = Form(...),
    name:              str = Form(...),
    wechat_app_id:     str = Form(...),
    wechat_app_secret: str = Form(...),
):
    config = request.app.state.config
    err = cfg.validate_user_id(user_id.strip().lower(), config, allow_existing=False)
    if err:
        return _form_error('new', err, raw={'id': user_id, 'name': name,
                                            'wechat': {'app_id': wechat_app_id}}, request=request)
    if not name.strip():
        return _form_error('new', '显示名不能为空', raw={'id': user_id}, request=request)
    if not wechat_app_id.strip() or not wechat_app_secret.strip():
        return _form_error('new', 'app_id 和 app_secret 必填', raw={'id': user_id, 'name': name},
                           request=request)

    try:
        cfg.add_user(user_id.strip().lower(), name.strip(),
                     wechat_app_id.strip(), wechat_app_secret.strip())
    except Exception as e:
        log.error('[users] add_user 失败: %s', e, exc_info=True)
        return _form_error('new', f'写入失败：{e}',
                           raw={'id': user_id, 'name': name}, request=request)

    cfg.reload_app_state(request.app)
    # HTMX：返回 HX-Redirect 让浏览器跳到首页
    return Response(headers={'HX-Redirect': '/'}, status_code=200)


# ── 编辑 ─────────────────────────────────────────────────────────────────────

@router.post('/users/{user_id}', response_class=HTMLResponse)
def update_user_route(
    user_id: str,
    request: Request,
    name:              str = Form(''),
    wechat_app_id:     str = Form(''),
    wechat_app_secret: str = Form(''),
):
    config = request.app.state.config
    if not cfg.get_user_raw(config, user_id):
        raise HTTPException(404)

    try:
        cfg.update_user(
            user_id,
            name=name.strip() or None,
            wechat_app_id=wechat_app_id.strip() or None,
            wechat_app_secret=wechat_app_secret.strip() or None,
        )
    except Exception as e:
        log.error('[users] update_user 失败: %s', e, exc_info=True)
        raw = cfg.get_user_raw(config, user_id) or {'id': user_id}
        return _form_error('edit', f'保存失败：{e}', raw=raw, request=request)

    cfg.reload_app_state(request.app)
    return Response(headers={'HX-Redirect': '/'}, status_code=200)


# ── 删除 ─────────────────────────────────────────────────────────────────────

@router.post('/users/{user_id}/delete', response_class=HTMLResponse)
def delete_user_route(user_id: str, request: Request):
    try:
        cfg.delete_user(user_id)
    except Exception as e:
        log.error('[users] delete_user 失败: %s', e, exc_info=True)
        raise HTTPException(400, str(e))

    cfg.reload_app_state(request.app)
    return Response(headers={'HX-Redirect': '/'}, status_code=200)


# ── 工具 ─────────────────────────────────────────────────────────────────────

def _form_error(mode: str, error: str, raw: dict, request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, '_user_form.html', {
        'mode': mode,
        'user': raw,
        'error': error,
    })
