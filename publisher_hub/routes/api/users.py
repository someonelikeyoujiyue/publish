"""用户 CRUD JSON API。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ... import config as cfg

log = logging.getLogger('publisher_hub.api.users')
router = APIRouter()


class UserCreateBody(BaseModel):
    id:                str = Field(..., min_length=1, max_length=32)
    name:              str = Field(..., min_length=1)
    wechat_app_id:     str = Field(..., min_length=1)
    wechat_app_secret: str = Field(..., min_length=1)


class UserUpdateBody(BaseModel):
    name:              str | None = None
    wechat_app_id:     str | None = None
    wechat_app_secret: str | None = None


def _serialize_user(u: dict, db) -> dict:
    """裁掉敏感字段（app_secret），加 ready 草稿数。"""
    wc = dict(u.get('wechat') or {})
    wc.pop('app_secret', None)
    return {
        'id':           u['id'],
        'name':         u.get('name') or u['id'],
        'wechat':       wc,
        'xhs':          u.get('xhs') or {},
        'toutiao':      u.get('toutiao') or {},
        'wechat_count': len(db.list_drafts(u['id'], platform='wechat', status='ready', limit=999)),
        'xhs_count':    len(db.list_drafts(u['id'], platform='xhs',    status='ready', limit=999)),
    }


@router.get('/users')
def list_users(request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    return {'users': [_serialize_user(u, db) for u in cfg.list_users(config)]}


@router.get('/users/{user_id}')
def get_user(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = cfg.get_user(config, user_id)
    if not user:
        raise HTTPException(404, f'用户 {user_id} 不存在')
    return _serialize_user(user, db)


@router.post('/users', status_code=201)
def create_user(body: UserCreateBody, request: Request):
    config = request.app.state.config
    uid = body.id.strip().lower()
    err = cfg.validate_user_id(uid, config, allow_existing=False)
    if err:
        raise HTTPException(400, err)
    try:
        cfg.add_user(uid, body.name.strip(),
                     body.wechat_app_id.strip(), body.wechat_app_secret.strip())
    except Exception as e:
        log.error('[api/users] add_user 失败: %s', e, exc_info=True)
        raise HTTPException(500, str(e))
    cfg.reload_app_state(request.app)
    return _serialize_user(cfg.get_user(request.app.state.config, uid), request.app.state.db)


@router.put('/users/{user_id}')
def update_user(user_id: str, body: UserUpdateBody, request: Request):
    config = request.app.state.config
    if not cfg.get_user_raw(config, user_id):
        raise HTTPException(404)
    try:
        cfg.update_user(
            user_id,
            name=(body.name or '').strip() or None,
            wechat_app_id=(body.wechat_app_id or '').strip() or None,
            wechat_app_secret=(body.wechat_app_secret or '').strip() or None,
        )
    except Exception as e:
        log.error('[api/users] update_user 失败: %s', e, exc_info=True)
        raise HTTPException(500, str(e))
    cfg.reload_app_state(request.app)
    return _serialize_user(cfg.get_user(request.app.state.config, user_id), request.app.state.db)


@router.delete('/users/{user_id}', status_code=204)
def delete_user(user_id: str, request: Request):
    try:
        cfg.delete_user(user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        log.error('[api/users] delete_user 失败: %s', e, exc_info=True)
        raise HTTPException(500, str(e))
    cfg.reload_app_state(request.app)
    return None
