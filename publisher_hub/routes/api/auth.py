"""三级权限（admin / editor / user）。

- **admin**：全功能（仿写 / 推送 / 用户增删改 / 删除草稿）
- **editor**：仿写 + 推送 + 头条绑定（**不能**改用户、不能删草稿）
- **user**：只读 + 推送（**不能**仿写、不能改用户）

凭证写在 config.yaml.auth.passwords 里，按"登录用户名→角色"映射（一个角色
可以有多个登录用户名）：

  auth:
    passwords:        # username → password
      admin:  fxj18383465677
      user:   123456
      lanshi: lanshi123456
    roles:            # 可选；username → role；省略=同名 role；lanshi 默认 editor
      lanshi: editor

token 存在内存（重启清空，所有人要重新登录，OK）。
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

# token → {username, role, expires_at}
_tokens: dict[str, dict] = {}
_TTL_SECONDS = 24 * 3600

# 内置「用户名 → 角色」默认映射（yaml.auth.roles 可覆盖）。
# username 是登录用的字符串；role 是权限等级。
_DEFAULT_USER_TO_ROLE: dict[str, str] = {
    'admin':  'admin',
    'user':   'user',
    'lanshi': 'editor',
}

VALID_ROLES = {'admin', 'editor', 'user'}


def _passwords(config: dict) -> dict[str, str]:
    """从 config 拿 {username: password}。

    没在 yaml 写就用 dev 默认值（admin/admin、user/user、lanshi/lanshi）。生产
    config.yaml 里写真实密码即可。
    """
    auth = (config or {}).get('auth') or {}
    pw = auth.get('passwords') or {}
    return {
        'admin':  str(pw.get('admin',  'admin')),
        'user':   str(pw.get('user',   'user')),
        'lanshi': str(pw.get('lanshi', 'lanshi')),
    }


def _user_to_role(config: dict) -> dict[str, str]:
    """登录用户名 → 角色映射，yaml.auth.roles 覆盖默认。"""
    out = dict(_DEFAULT_USER_TO_ROLE)
    auth = (config or {}).get('auth') or {}
    for k, v in (auth.get('roles') or {}).items():
        r = str(v).strip().lower()
        if r in VALID_ROLES:
            out[str(k).strip().lower()] = r
    return out


class LoginBody(BaseModel):
    username: str
    password: str


@router.post('/login')
def login(body: LoginBody, request: Request):
    config = request.app.state.config
    pw = _passwords(config)
    username = body.username.strip().lower()
    if username not in pw:
        raise HTTPException(401, '账号不存在')
    if body.password != pw[username]:
        raise HTTPException(401, '密码错误')
    role = _user_to_role(config).get(username, 'user')
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + _TTL_SECONDS
    _tokens[token] = {'username': username, 'role': role, 'expires_at': expires_at}
    log.info('[auth] %s 登录 (role=%s)', username, role)
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
    if role is None:
        # 未登录 / token 过期 / 服务重启清掉 token —— 401 让前端清缓存跳 /login
        raise HTTPException(401, 'login required')
    if role != 'admin':
        # 真正的权限不足（editor/user 访问 admin 端点）
        raise HTTPException(403, 'admin only')
    return role


def require_editor(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    """admin 或 editor 都通过；user 拒绝。用于「立即仿写」「头条绑定」等写操作。"""
    role = _resolve_role(creds)
    if role is None:
        raise HTTPException(401, 'login required')
    if role not in ('admin', 'editor'):
        raise HTTPException(403, 'editor or admin only')
    return role


def require_login(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    role = _resolve_role(creds)
    if role is None:
        raise HTTPException(401, 'login required')
    return role
