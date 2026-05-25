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

# 用户级别支持的平台。新用户默认全开；admin 可在「编辑用户」勾选关掉。
ALL_PLATFORMS = ('wechat', 'xhs', 'toutiao', 'douyin')

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
        # 减小服务器压力：每次只综合写 1 篇；素材池从 50 拿 5 条够用
        'rewrite_batch':      5,
        'articles_per_batch': 1,
        'pick_strategy':     'recent_random',
        'recent_pool':       50,
        'rewrite_cron':      '0 8 * * *',
    },
    'xhs': {
        'sources': {
            'platforms':  ['xiaohongshu', 'douyin'],
            'categories': [],          # 空=不限制 category，所有有内容的帖子都可仿写
        },
        'prompt':            'xhs_note',
        'rewrite_mode':      'per_post',
        # 每次只生成 1 条；候选池 3 个让 LLM 挑最优
        'rewrite_batch':     1,
        'articles_per_batch': 0,
        'pick_strategy':     'random',
        'candidate_pool':    3,
        'rewrite_cron':      '0 8 * * *',
    },
    'toutiao': {
        # 微头条体裁：跟小红书同源（xiaohongshu/douyin），但只写文字、不生图
        'sources': {
            'platforms':  ['xiaohongshu', 'douyin'],
            'categories': [],
        },
        'prompt':            'toutiao_weitt',
        'rewrite_mode':      'per_post',
        'rewrite_batch':     1,
        'articles_per_batch': 0,
        'pick_strategy':     'random',
        'candidate_pool':    0,
        'rewrite_cron':      '0 8 * * *',
    },
    'douyin': {
        # 抖音图文：复用 toutiao_weitt prompt（信息密度风，跟头条/公众号一致）
        # 用户在前端复制后跳转 creator.douyin.com 自己粘贴发布；不做扫码绑定/CDP
        'sources': {
            'platforms':  ['xiaohongshu', 'douyin'],
            'categories': [],
        },
        'prompt':            'toutiao_weitt',
        'rewrite_mode':      'per_post',
        'rewrite_batch':     1,
        'articles_per_batch': 0,
        'pick_strategy':     'random',
        'candidate_pool':    0,
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
    # enabled_platforms 缺省 = 全开。yaml 里写了空列表就当全关。
    if 'enabled_platforms' not in merged:
        merged['enabled_platforms'] = list(ALL_PLATFORMS)
    return merged


def is_platform_enabled(user: dict, platform: str) -> bool:
    """user 上是否启用了某平台的每日仿写。"""
    if not user:
        return False
    enabled = user.get('enabled_platforms')
    if enabled is None:
        return platform in ALL_PLATFORMS
    return platform in enabled


def has_wechat_binding(user: dict) -> bool:
    """user 是否已绑定公众号（app_id + app_secret 都非空）。"""
    wc = (user or {}).get('wechat') or {}
    return bool((wc.get('app_id') or '').strip()) and bool((wc.get('app_secret') or '').strip())


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


def add_user(user_id: str, name: str,
             wechat_app_id: str = '', wechat_app_secret: str = '',
             enabled_platforms: Optional[list[str]] = None) -> dict:
    """追加一个用户到 config.yaml。返回创建的 raw 记录。

    wechat key 现在可选；新建时不强制，用户首次「推送到草稿箱」时再补绑。
    enabled_platforms 留空 = 全平台启用。
    """
    data = _load_yaml_for_write()
    users = data.setdefault('users', [])

    # 创建新记录（只填差异字段，其它继承 _default_user）
    new_user: dict = {
        'id':   user_id,
        'name': name,
    }
    if wechat_app_id or wechat_app_secret:
        new_user['wechat'] = {
            'app_id':     wechat_app_id or '',
            'app_secret': wechat_app_secret or '',
        }
    if enabled_platforms is not None:
        # 只接受白名单内的，且按固定顺序写回（yaml 可读性）
        clean = [p for p in ALL_PLATFORMS if p in set(enabled_platforms)]
        new_user['enabled_platforms'] = clean
    users.append(new_user)
    _save_yaml(data)
    log.info('[config] 新增用户 %s enabled=%s', user_id, new_user.get('enabled_platforms'))
    return new_user


def update_user(user_id: str,
                name: Optional[str] = None,
                wechat_app_id: Optional[str] = None,
                wechat_app_secret: Optional[str] = None,
                enabled_platforms: Optional[list[str]] = None) -> None:
    """部分更新一个用户。空字符串 / None 表示保持原值。

    enabled_platforms 传 list 才会写；传 None 不动；传空列表 = 全关。
    """
    data = _load_yaml_for_write()
    for u in data.get('users') or []:
        if u.get('id') == user_id:
            if name:
                u['name'] = name
            if wechat_app_id is not None or wechat_app_secret is not None:
                wc = u.setdefault('wechat', {})
                if wechat_app_id:
                    wc['app_id'] = wechat_app_id
                if wechat_app_secret:
                    wc['app_secret'] = wechat_app_secret
            if enabled_platforms is not None:
                clean = [p for p in ALL_PLATFORMS if p in set(enabled_platforms)]
                u['enabled_platforms'] = clean
            _save_yaml(data)
            log.info('[config] 更新用户 %s', user_id)
            return
    raise ValueError(f'用户 {user_id} 不存在')


def set_user_toutiao_port(user_id: str, port: int) -> None:
    """写 user.toutiao.cdp_port（首次绑头条号时分配）。"""
    data = _load_yaml_for_write()
    for u in data.get('users') or []:
        if u.get('id') == user_id:
            tt = u.setdefault('toutiao', {})
            tt['cdp_port'] = int(port)
            _save_yaml(data)
            log.info('[config] %s toutiao.cdp_port=%d', user_id, port)
            return
    raise ValueError(f'用户 {user_id} 不存在')


def unset_user_toutiao(user_id: str) -> None:
    """解绑头条号（清 user.toutiao 段）。"""
    data = _load_yaml_for_write()
    for u in data.get('users') or []:
        if u.get('id') == user_id and 'toutiao' in u:
            u.pop('toutiao', None)
            _save_yaml(data)
            log.info('[config] %s toutiao 段已清', user_id)
            return


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
