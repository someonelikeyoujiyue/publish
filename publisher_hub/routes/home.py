"""首页 + 用户 ID 重定向。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import get_user, list_users

router = APIRouter()
templates = Jinja2Templates(directory='templates')


@router.get('/', response_class=HTMLResponse)
def home(request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    users  = list_users(config)

    # 给每个用户算一下 ready 草稿数
    user_stats = []
    for u in users:
        wechat_count = len(db.list_drafts(u['id'], platform='wechat', status='ready', limit=999))
        xhs_count    = len(db.list_drafts(u['id'], platform='xhs',    status='ready', limit=999))
        user_stats.append({
            **u,
            'wechat_count': wechat_count,
            'xhs_count':    xhs_count,
        })

    return templates.TemplateResponse(request, 'home.html', {
        'users': user_stats,
    })


@router.get('/{user_id}')
def user_index(user_id: str, request: Request):
    """访问 /{user_id} 直接跳到 wechat 列表。"""
    if not get_user(request.app.state.config, user_id):
        return RedirectResponse('/', status_code=302)
    return RedirectResponse(f'/{user_id}/wechat', status_code=302)
