"""用户 CRUD JSON API。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ... import config as cfg
from .auth import require_admin

log = logging.getLogger('publisher_hub.api.users')
router = APIRouter()


class UserCreateBody(BaseModel):
    id:                str = Field(..., min_length=1, max_length=32)
    name:              str = Field(..., min_length=1)
    # wechat key 现在可选；用户首次「推送到草稿箱」时按需绑定
    wechat_app_id:     str = ''
    wechat_app_secret: str = ''
    # 启用哪些平台的每日仿写（不传 = 全平台）
    enabled_platforms: list[str] | None = None


class UserUpdateBody(BaseModel):
    name:              str | None = None
    wechat_app_id:     str | None = None
    wechat_app_secret: str | None = None
    enabled_platforms: list[str] | None = None


def _serialize_user(u: dict, counts: dict[tuple[str, str], int]) -> dict:
    """白名单序列化：只返回前端需要的字段。

    counts: 由 db.count_all_drafts() 提供的 (user_id, platform) → 计数
    敏感字段（app_secret / proxy 凭证 / cookie 等）一律不出口。
    """
    wc = u.get('wechat') or {}
    tt = u.get('toutiao') or {}
    xhs = u.get('xhs') or {}
    uid = u['id']
    wechat_bound = bool((wc.get('app_id') or '').strip()) and bool((wc.get('app_secret') or '').strip())
    return {
        'id':      uid,
        'name':    u.get('name') or uid,
        'wechat': {
            'app_id': wc.get('app_id') or '',
            'author': wc.get('author') or '',
            'bound':  wechat_bound,         # 前端按这个决定推送前是否弹绑定 modal
        },
        'xhs': {
            'display_name': xhs.get('display_name') or '',
        },
        'toutiao': {
            'cdp_port': tt.get('cdp_port'),
        },
        # 哪些平台启用每日仿写（缺省 = 全开，由 _materialize_user 兜底）
        'enabled_platforms': list(u.get('enabled_platforms') or []),
        'wechat_count':  counts.get((uid, 'wechat'),  0),
        'xhs_count':     counts.get((uid, 'xhs'),     0),
        'toutiao_count': counts.get((uid, 'toutiao'), 0),
        'douyin_count':  counts.get((uid, 'douyin'),  0),
        'youtube_count': counts.get((uid, 'youtube'), 0),
    }


@router.get('/users')
def list_users(request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    counts = db.count_all_drafts()
    return {'users': [_serialize_user(u, counts) for u in cfg.list_users(config)]}


@router.get('/users/{user_id}')
def get_user(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = cfg.get_user(config, user_id)
    if not user:
        raise HTTPException(404, f'用户 {user_id} 不存在')
    return _serialize_user(user, db.count_all_drafts())


@router.post('/users', status_code=201)
def create_user(body: UserCreateBody, request: Request, _=Depends(require_admin)):
    config = request.app.state.config
    uid = body.id.strip().lower()
    err = cfg.validate_user_id(uid, config, allow_existing=False)
    if err:
        raise HTTPException(400, err)
    try:
        cfg.add_user(
            uid, body.name.strip(),
            wechat_app_id=(body.wechat_app_id or '').strip(),
            wechat_app_secret=(body.wechat_app_secret or '').strip(),
            enabled_platforms=body.enabled_platforms,
        )
    except Exception as e:
        log.error('[api/users] add_user 失败: %s', e, exc_info=True)
        raise HTTPException(500, str(e))
    cfg.reload_app_state(request.app)
    return _serialize_user(
        cfg.get_user(request.app.state.config, uid),
        request.app.state.db.count_all_drafts(),
    )


@router.put('/users/{user_id}')
def update_user(user_id: str, body: UserUpdateBody, request: Request, _=Depends(require_admin)):
    config = request.app.state.config
    if not cfg.get_user_raw(config, user_id):
        raise HTTPException(404)
    try:
        cfg.update_user(
            user_id,
            name=(body.name or '').strip() or None,
            wechat_app_id=(body.wechat_app_id or '').strip() or None,
            wechat_app_secret=(body.wechat_app_secret or '').strip() or None,
            enabled_platforms=body.enabled_platforms,
        )
    except Exception as e:
        log.error('[api/users] update_user 失败: %s', e, exc_info=True)
        raise HTTPException(500, str(e))
    cfg.reload_app_state(request.app)
    return _serialize_user(
        cfg.get_user(request.app.state.config, user_id),
        request.app.state.db.count_all_drafts(),
    )


@router.delete('/users/{user_id}', status_code=204)
def delete_user(user_id: str, request: Request, _=Depends(require_admin)):
    try:
        cfg.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        log.error('[api/users] delete_user 失败: %s', e, exc_info=True)
        raise HTTPException(500, str(e))
    cfg.reload_app_state(request.app)
    return None
