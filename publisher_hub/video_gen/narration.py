"""文案生成：复用 publisher_hub.rewrite 的 LLM 流式调用，套 prompts.yaml.video_narration。

LLM 严格输出 JSON {"title": "...", "narrations": ["...","...",...]}。
parse 时容错：剥 markdown ``` 块、剥 BOM、尝试摘第一个 {} 段。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

log = logging.getLogger('publisher_hub.video_gen.narration')


@dataclass
class NarrationResult:
    title: str
    narrations: list[str]


def _strip_to_json(raw: str) -> str:
    """剥 LLM 输出里常见的装饰：``` 围栏、BOM、前后说明文字。返回纯 JSON 字符串。"""
    s = raw.strip().lstrip('﻿')
    # 剥 ```json ... ``` / ``` ... ```
    if s.startswith('```'):
        # 移到第一个换行后开始；尾部 ``` 砍掉
        s = re.sub(r'^```[a-zA-Z]*\s*\n?', '', s)
        if s.endswith('```'):
            s = s[:-3]
        s = s.strip()
    # 找第一个 { 和最后一个 }，截这段
    i = s.find('{')
    j = s.rfind('}')
    if i >= 0 and j > i:
        s = s[i:j + 1]
    return s


def _stream_llm(prompt: str, llm_cfg: dict) -> str:
    """流式 chat completion。复刻 rewrite._call_llm 的核心逻辑（避免循环 import）。

    必须 stream=True：valueclue 代理对非流式 ~16s 强制断连，reasoning 类完不成。
    """
    api_key  = llm_cfg['api_key']
    base_url = llm_cfg['base_url'].rstrip('/')
    model    = llm_cfg['model']
    max_tokens = int(llm_cfg.get('max_tokens', 8000))   # narration 不需要太大
    timeout    = float(llm_cfg.get('timeout_seconds', 180))

    body: dict = {
        'model':      model,
        'messages':   [{'role': 'user', 'content': prompt}],
        'max_tokens': max_tokens,
        'stream':     True,
    }
    if 'reasoner' not in model:
        body['temperature'] = 0.7  # narration 要稳定，比正文仿写稍降

    content_parts: list[str] = []
    with httpx.Client(timeout=timeout) as c:
        with c.stream(
            'POST', f'{base_url}/chat/completions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json=body,
        ) as resp:
            if resp.status_code >= 400:
                body_text = b''.join(resp.iter_bytes()).decode('utf-8', errors='replace')
                raise RuntimeError(f'LLM HTTP {resp.status_code}: {body_text[:300]}')
            for line in resp.iter_lines():
                if not line:
                    continue
                line = line.strip()
                if line.startswith('data:'):
                    line = line[5:].strip()
                if line == '[DONE]':
                    break
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get('choices') or [{}])[0].get('delta', {}) or {}
                piece = delta.get('content')
                if piece:
                    content_parts.append(piece)

    return ''.join(content_parts).strip()


def generate(
    topic: str,
    n_scenes: int,
    config: dict,
    prompts: dict,
) -> NarrationResult:
    """主入口。

    Args:
        topic:     话题（必填，给 LLM 引出旁白）
        n_scenes:  期望 narration 段数（3-5 推荐）
        config:    publisher-hub 全局 config（取 llm 段）
        prompts:   load_prompts() 结果（取 video_narration 段）

    Returns:
        NarrationResult(title, narrations)

    Raises:
        RuntimeError: LLM 调用失败或解析失败
    """
    tmpl = (prompts or {}).get('video_narration')
    if not tmpl:
        raise RuntimeError('prompts.yaml 缺 video_narration 模板')

    prompt = tmpl.format(topic=topic.strip(), n_scenes=n_scenes)

    log.info('[narration] LLM 生成中  n_scenes=%d  topic=%r', n_scenes, topic[:40])
    raw = _stream_llm(prompt, config['llm'])
    if not raw:
        raise RuntimeError('LLM 返回空')

    log.debug('[narration] 原始输出: %s', raw[:300])
    cleaned = _strip_to_json(raw)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.error('[narration] JSON 解析失败: %s\n原始(剥后): %s', e, cleaned[:500])
        raise RuntimeError(f'LLM 输出非合法 JSON: {e}')

    title = (obj.get('title') or '').strip()
    narrations = obj.get('narrations') or []
    if not isinstance(narrations, list) or not narrations:
        raise RuntimeError(f'LLM 返回 narrations 为空或不是 list: {obj!r}')

    # 各段做基本清理：strip + 去掉残留的 emoji / # 标签
    cleaned_narr: list[str] = []
    for n in narrations:
        if not isinstance(n, str):
            continue
        s = n.strip()
        # 撕 # 标签
        s = re.sub(r'#\S+', '', s)
        s = re.sub(r'\s+', '', s) if not re.search(r'[a-zA-Z]', s) else re.sub(r'\s+', ' ', s).strip()
        if s:
            cleaned_narr.append(s)

    if not cleaned_narr:
        raise RuntimeError('narrations 清洗后全空')

    log.info('[narration] ✓ title=%r  narrations=%d 段', title, len(cleaned_narr))
    return NarrationResult(title=title, narrations=cleaned_narr)
