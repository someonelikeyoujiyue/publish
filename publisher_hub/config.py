"""加载 config.yaml 和 prompts.yaml。"""
from __future__ import annotations

from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH  = _PROJECT_ROOT / 'config.yaml'
_PROMPTS_PATH = _PROJECT_ROOT / 'prompts.yaml'


def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f'找不到 {_CONFIG_PATH}，请先 `cp config.example.yaml config.yaml` 并填入真实值'
        )
    with open(_CONFIG_PATH, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_prompts() -> dict:
    """返回 prompts.yaml 里的 templates 段（按名取模板）。"""
    if not _PROMPTS_PATH.exists():
        return {}
    with open(_PROMPTS_PATH, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data.get('templates', {})


def get_user(config: dict, user_id: str) -> dict | None:
    for u in config.get('users') or []:
        if u.get('id') == user_id:
            return u
    return None


def list_users(config: dict) -> list[dict]:
    return config.get('users') or []
