"""小红书发布 — 通过 myaibot.vip 生成扫码确认二维码。

myaibot token 全用户共享一个；每次调用返回一个一次性二维码 URL，
绑定的小红书 App 扫码后真实发布。

API 文档：参考 /Users/fengxiaoji/code/xhs-publisher/xhs_publish.py
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

log = logging.getLogger('publisher_hub.xhs')


class XhsPublisher:
    def __init__(self, config: dict):
        cfg = config.get('myaibot') or {}
        self.token   = cfg.get('token', '').strip()
        self.api_url = cfg.get('api_url') or 'https://www.myaibot.vip/api/rednote/publish-with-upload'
        self.enabled = bool(self.token)

    def push(self, title: str, content: str, images: list[str] | None = None) -> dict:
        """发起发布请求。返回 dict：

        成功:  {'ok': True,  'qr_url': '...',  'raw': {...}}
        失败:  {'ok': False, 'error':  '...',  'raw': {...}}
        """
        if not self.enabled:
            return {'ok': False, 'error': 'myaibot.token 未配置', 'raw': {}}

        # 长度合规化（myaibot 算法：中文 1 字、ASCII 0.5 字、emoji 多字符；title ≤ 20 字）
        title   = self._truncate_xhs_title((title or '').strip(), limit=20.0)
        content = (content or '').strip()
        if len(content) > 1000:
            content = content[:997] + '…'

        images = [u for u in (images or []) if u]
        if not images:
            return {'ok': False, 'error': '小红书图文必须有至少 1 张图片（API 要求）', 'raw': {}}

        # myaibot 当前 API：api_key 在 body 而不是 Header；type 必填
        payload = {
            'api_key': self.token,
            'type':    'normal',         # 'normal' 图文 / 'video' 视频
            'title':   title,
            'content': content,
            'images':  images,
        }
        headers = {'Content-Type': 'application/json'}

        try:
            with httpx.Client(timeout=30) as c:
                r = c.post(self.api_url, json=payload, headers=headers)
            try:
                data = r.json()
            except Exception:
                data = {'raw_text': r.text[:500]}

            if r.status_code >= 400:
                log.warning('[xhs] HTTP %d: %s', r.status_code, data)
                return {'ok': False, 'error': f'HTTP {r.status_code}: {data}', 'raw': data}

            qr = self._extract_qr(data)
            if qr:
                log.info('[xhs] 二维码生成成功: %s', qr[:80])
                return {'ok': True, 'qr_url': qr, 'raw': data}

            # 部分情况 myaibot 直接返回成功无二维码（自动发布）
            if data.get('code') in (0, 200) or data.get('success'):
                resp_dump = json.dumps(data, ensure_ascii=False)
                log.info('[xhs] 200 OK 但无二维码  完整响应=%s', resp_dump[:600])
                return {'ok': True, 'qr_url': '', 'raw': data}

            log.warning('[xhs] 未知响应: %s', json.dumps(data, ensure_ascii=False)[:600])
            return {'ok': False, 'error': '未提取到二维码', 'raw': data}

        except Exception as e:
            log.warning('[xhs] 调用异常: %s', e)
            return {'ok': False, 'error': str(e), 'raw': {}}

    @staticmethod
    def _truncate_xhs_title(s: str, limit: float = 20.0) -> str:
        """按 myaibot 字符计数规则截断。

        规则（myaibot 错误信息推断）：
          - 中文/标点：1 字
          - ASCII 字母数字：0.5 字
          - emoji（含 ZWJ 序列、国旗）：每个 codepoint 算 2 字（保守估计）

        逐字符累加直到接近 limit；保留 0.5 字余量。
        """
        if not s:
            return ''
        budget = limit - 0.5
        used = 0.0
        out = []
        for c in s:
            cp = ord(c)
            if cp < 128:
                w = 0.5
            elif 0x1F000 <= cp <= 0x1FFFF or 0x2600 <= cp <= 0x27BF:
                # emoji / pictograph
                w = 2.0
            else:
                w = 1.0
            if used + w > budget:
                break
            out.append(c)
            used += w
        return ''.join(out)

    @staticmethod
    def _extract_qr(data: dict) -> Optional[str]:
        """从 myaibot 响应里找二维码（base64 data URI 或 URL）。

        myaibot 官方格式（2026-05）：
            data.qrcode = 'data:image/png;base64,iVBOR...'   ← 真正的二维码图，直接 <img src=...>
            data.url    = 'https://www.myaibot.vip/rednote/publish/<uuid>'  ← 发布页 URL（备用）

        优先级：data.qrcode > data.url > 旧字段
        """
        if not isinstance(data, dict):
            return None
        sub = data.get('data') if isinstance(data.get('data'), dict) else {}
        candidates = [
            sub.get('qrcode'),            # 优先：base64 data URI
            sub.get('qr_code'),
            sub.get('qrcode_url'),
            sub.get('qr_url'),
            sub.get('qrUrl'),
            sub.get('url'),               # 兜底：myaibot 发布页 URL
            data.get('qrcode'),
            data.get('qrcode_url'),
            data.get('qr_url'),
            data.get('qrUrl'),
        ]
        for u in candidates:
            if u and isinstance(u, str) and (u.startswith('data:image') or u.startswith('http')):
                return u
        return None
