"""简单两级权限（admin / user）。

凭证写在 config.yaml.auth 里；token 存在内存（重启清空，所有人要重新登录，OK）。
Token 用 secrets.token_urlsafe 生成，24h 后过期。
"""
from __future__ import annotations

import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

log = logging.getLogger('publisher_hub.api.auth')
router = APIRouter()
_bearer = HTTPBearer(auto_error=False)

# token → {role, expires_at}
_tokens: dict[str, dict] = {}
_TTL_SECONDS = 24 * 3600


def _passwords(config: dict) -> dict[str, str]:
    """从 config 拿 {role: password}，默认 admin/admin user/user。"""
    auth = (config or {}).get('auth') or {}
    pw = auth.get('passwords') or {}
    return {
        'admin': str(pw.get('admin', 'admin')),
        'user':  str(pw.get('user',  'user')),
    }


class LoginBody(BaseModel):
    username: str
    password: str


@router.post('/login')
def login(body: LoginBody, request: Request):
    pw = _passwords(request.app.state.config)
    role = body.username.strip().lower()
    if role not in pw:
        raise HTTPException(401, '账号不存在')
    if body.password != pw[role]:
        raise HTTPException(401, '密码错误')
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + _TTL_SECONDS
    _tokens[token] = {'role': role, 'expires_at': expires_at}
    log.info('[auth] %s 登录', role)
    return {'token': token, 'role': role, 'expires_at': int(expires_at)}


@router.post('/logout')
def logout(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    if creds and creds.credentials in _tokens:
        _tokens.pop(creds.credentials, None)
    return {'ok': True}


@router.get('/me')
def me(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    if not creds:
        raise HTTPException(401, 'no token')
    rec = _tokens.get(creds.credentials)
    if not rec or rec['expires_at'] < time.time():
        _tokens.pop(creds.credentials, None)
        raise HTTPException(401, 'expired')
    return {'role': rec['role'], 'expires_at': int(rec['expires_at'])}


# ── 依赖函数：在 endpoint 上 Depends(require_admin) ─────────────────────────

def _resolve_role(creds: HTTPAuthorizationCredentials | None) -> str | None:
    """从 Bearer token 解析 role，返回 None 表示未登录或 expired。"""
    if not creds:
        return None
    rec = _tokens.get(creds.credentials)
    if not rec:
        return None
    if rec['expires_at'] < time.time():
        _tokens.pop(creds.credentials, None)
        return None
    return rec['role']


def require_admin(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    role = _resolve_role(creds)
    if role != 'admin':
        raise HTTPException(403, 'admin only')
    return role


def require_login(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    role = _resolve_role(creds)
    if role is None:
        raise HTTPException(401, 'login required')
    return role
