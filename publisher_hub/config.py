"""加载 / 写入 config.yaml 和 prompts.yaml。

写文件用 ruamel.yaml 保留注释和格式，读用 PyYAML（更快）。
默认值在 `_default_user:` 段，新建/读取用户时自动合并。
"""
from __future__ import annotations

import copy
import logging
import re
from pathlib import Path
from typing import Optional

import yaml
from ruamel.yaml import YAML

log = logging.getLogger('publisher_hub.config')

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH  = _PROJECT_ROOT / 'config.yaml'
_PROMPTS_PATH = _PROJECT_ROOT / 'prompts.yaml'

_USER_ID_RE = re.compile(r'^[a-z0-9_-]{1,32}$')

# 写文件专用：保留注释、引号、缩进、非 ASCII 字符（中文不转义）
_ruamel = YAML()
_ruamel.preserve_quotes = True
_ruamel.indent(mapping=2, sequence=4, offset=2)
_ruamel.width = 4096
_ruamel.allow_unicode = True       # 中文/emoji 直接写，不转义为 \xXX


# ── 硬编码默认值（最低优先级）──────────────────────────────────────────────────
# 即使 config.yaml 完全没有 _default_user 段，新建用户也能拿到完整 sources/prompt。
# yaml 的 _default_user 段如有则覆盖此处；用户级配置最终覆盖前两者。
# 注意 wechat.proxy 故意留空，因为代理凭证是部署相关的，不能硬编码。

_HARDCODED_DEFAULTS = {
    'wechat': {
        'proxy':      '',
        'author':     '',
        'sources': {
            'platforms':  ['youtube', 'facebook', 'instagram'],
            'categories': ['学校官方'],
        },
        'prompt':            'wechat_article',
        'rewrite_mode':      'batch',
        'rewrite_batch':     10,
        'articles_per_batch': 2,
        # 定时任务：每天早上 8:00 触发  →  仿写 + 自动推送到草稿箱
        # （草稿箱不自动群发，运营进公众号后台手动点群发）
        'rewrite_cron':      '0 8 * * *',
    },
    'xhs': {
        'sources': {
            'platforms':  ['xiaohongshu', 'douyin'],
            'categories': [],          # 空=不限制 category，所有有内容的帖子都可仿写
        },
        'prompt':            'xhs_note',
        'rewrite_mode':      'per_post',
        'rewrite_batch':     2,
        'articles_per_batch': 0,
        # 定时任务：每天早上 8:00 触发  →  仿写 + 自动调 myaibot 生成二维码
        # （二维码 URL 写入 hub_drafts.pushed_result + 飞书通知运营扫码）
        'rewrite_cron':      '0 8 * * *',
    },
}


# ── 加载 ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f'找不到 {_CONFIG_PATH}，请先 `cp config.example.yaml config.yaml` 并填入真实值'
        )
    with open(_CONFIG_PATH, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_prompts() -> dict:
    if not _PROMPTS_PATH.exists():
        return {}
    with open(_PROMPTS_PATH, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data.get('templates', {})


# ── 用户读取（合并默认值）──────────────────────────────────────────────────────

def _deep_merge(base: dict, overlay: dict) -> dict:
    """把 overlay 合并到 base 上，base 不变，返回新 dict。dict 递归合并，其它直接覆盖。"""
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _materialize_user(raw: dict, defaults: dict) -> dict:
    """把 raw 用户配置 + defaults 合并成完整用户配置。"""
    if not raw or not raw.get('id'):
        return raw or {}
    merged = _deep_merge(defaults or {}, raw)
    # xhs.display_name 自动派生
    xhs = merged.setdefault('xhs', {})
    if not xhs.get('display_name'):
        xhs['display_name'] = f"{merged.get('name') or merged['id']} 的小红书"
    return merged


def _resolved_defaults(config: dict) -> dict:
    """硬编码默认值 ⊕ yaml._default_user 覆盖（深度合并）。"""
    return _deep_merge(_HARDCODED_DEFAULTS, config.get('_default_user') or {})


def get_user(config: dict, user_id: str) -> Optional[dict]:
    raw = next((u for u in (config.get('users') or []) if u.get('id') == user_id), None)
    if not raw:
        return None
    return _materialize_user(raw, _resolved_defaults(config))


def list_users(config: dict) -> list[dict]:
    defaults = _resolved_defaults(config)
    return [_materialize_user(u, defaults) for u in (config.get('users') or [])]


def get_user_raw(config: dict, user_id: str) -> Optional[dict]:
    """返回 yaml 里的原始用户记录（未合并默认值），用于编辑表单 prefill。"""
    return next((u for u in (config.get('users') or []) if u.get('id') == user_id), None)


# ── 用户写入（修改 yaml 文件并保留注释）──────────────────────────────────────

def _load_yaml_for_write():
    with open(_CONFIG_PATH, encoding='utf-8') as f:
        return _ruamel.load(f)


def _save_yaml(data) -> None:
    tmp = _CONFIG_PATH.with_suffix('.yaml.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        _ruamel.dump(data, f)
    tmp.replace(_CONFIG_PATH)


def validate_user_id(user_id: str, config: dict, allow_existing: bool = False) -> str:
    """校验 user_id 合法性。返回错误信息，空字符串=合法。"""
    if not user_id:
        return '用户 ID 不能为空'
    if not _USER_ID_RE.match(user_id):
        return '用户 ID 必须由小写字母/数字/下划线/连字符组成，长度 1-32'
    if user_id.startswith('_'):
        return '用户 ID 不能以下划线开头（保留给系统配置）'
    exists = any(u.get('id') == user_id for u in (config.get('users') or []))
    if exists and not allow_existing:
        return f'用户 ID 「{user_id}」已存在'
    if not exists and allow_existing:
        return f'用户 ID 「{user_id}」不存在'
    return ''


def add_user(user_id: str, name: str, wechat_app_id: str, wechat_app_secret: str) -> dict:
    """追加一个用户到 config.yaml。返回创建的 raw 记录。"""
    data = _load_yaml_for_write()
    users = data.setdefault('users', [])

    # 创建新记录（只填差异字段，其它继承 _default_user）
    new_user = {
        'id':   user_id,
        'name': name,
        'wechat': {
            'app_id':     wechat_app_id,
            'app_secret': wechat_app_secret,
        },
    }
    users.append(new_user)
    _save_yaml(data)
    log.info('[config] 新增用户 %s', user_id)
    return new_user


def update_user(user_id: str,
                name: Optional[str] = None,
                wechat_app_id: Optional[str] = None,
                wechat_app_secret: Optional[str] = None) -> None:
    """部分更新一个用户。空字符串 / None 表示保持原值。"""
    data = _load_yaml_for_write()
    for u in data.get('users') or []:
        if u.get('id') == user_id:
            if name:
                u['name'] = name
            wc = u.setdefault('wechat', {})
            if wechat_app_id:
                wc['app_id'] = wechat_app_id
            if wechat_app_secret:
                wc['app_secret'] = wechat_app_secret
            _save_yaml(data)
            log.info('[config] 更新用户 %s', user_id)
            return
    raise ValueError(f'用户 {user_id} 不存在')


def delete_user(user_id: str) -> None:
    data = _load_yaml_for_write()
    users = data.get('users') or []
    new_users = [u for u in users if u.get('id') != user_id]
    if len(new_users) == len(users):
        raise ValueError(f'用户 {user_id} 不存在')
    data['users'] = new_users
    _save_yaml(data)
    log.info('[config] 删除用户 %s', user_id)


# ── 热加载 ───────────────────────────────────────────────────────────────────

def reload_app_state(app) -> None:
    """重新读 yaml 并更新 FastAPI app.state（无需 restart 进程）。"""
    app.state.config  = load_config()
    app.state.prompts = load_prompts()
    log.info('[config] app.state 已 reload')
