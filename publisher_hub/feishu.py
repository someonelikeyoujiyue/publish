"""飞书 webhook 通知。

推送/仿写完成后发卡片到群里。webhook URL 在 config.yaml.feishu.webhook，
留占位（含 'xxxxxxxx'）则跳过通知不报错。
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger('publisher_hub.feishu')


class FeishuBot:
    def __init__(self, config: dict):
        webhook = (config.get('feishu') or {}).get('webhook') or ''
        # 含占位字符则视为未配置
        self.webhook = webhook if webhook and 'xxxxxxxx' not in webhook else ''
        self.enabled = bool(self.webhook)

    def _post(self, payload: dict) -> bool:
        if not self.enabled:
            log.debug('[feishu] webhook 未配置，跳过')
            return False
        try:
            with httpx.Client(timeout=10) as c:
                r = c.post(self.webhook, json=payload)
            data = r.json() if r.content else {}
            if data.get('code') == 0 or data.get('StatusCode') == 0:
                return True
            log.warning('[feishu] webhook 响应异常: %s', data)
            return False
        except Exception as e:
            log.warning('[feishu] webhook 调用失败: %s', e)
            return False

    def send_card(self, title: str, template: str, lines: list[str]) -> bool:
        return self._post({
            'msg_type': 'interactive',
            'card': {
                'header': {
                    'title': {'tag': 'plain_text', 'content': title},
                    'template': template,
                },
                'elements': [
                    {'tag': 'markdown', 'content': '\n'.join(lines)},
                ],
            },
        })

    def push_success(self, user_name: str, platform: str, draft_title: str,
                     extra: str = '') -> bool:
        platform_label = '公众号草稿箱' if platform == 'wechat' else '小红书'
        emoji          = '📰' if platform == 'wechat' else '🌹'
        lines = [
            f'**用户**：{user_name}',
            f'**平台**：{platform_label}',
            f'**标题**：{draft_title[:60]}',
        ]
        if extra:
            lines.append(extra)
        return self.send_card(
            f'{emoji} 推送成功',
            template='green',
            lines=lines,
        )

    def push_failed(self, user_name: str, platform: str, draft_title: str,
                    error: str) -> bool:
        platform_label = '公众号' if platform == 'wechat' else '小红书'
        lines = [
            f'**用户**：{user_name}',
            f'**平台**：{platform_label}',
            f'**标题**：{draft_title[:60]}',
            f'**错误**：{error[:300]}',
        ]
        return self.send_card(
            '❌ 推送失败',
            template='red',
            lines=lines,
        )
