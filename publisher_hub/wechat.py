"""微信公众号草稿箱推送（独立精简版）。

每个用户对应一个 (app_id, app_secret)，推送时按 user 实例化。
access_token 按 app_id 在模块级缓存（7200s 过期，提前 60s 续期）。

API 出口必须走 SOCKS5 代理（IP 白名单），代理 URL 在 user.wechat.proxy。
"""
from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from io import BytesIO
from typing import Optional

import httpx
from PIL import Image

log = logging.getLogger('publisher_hub.wechat')

_API = 'https://api.weixin.qq.com/cgi-bin'

# 模块级 token 缓存：app_id -> (token, expires_at)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


# ── 简洁版 Markdown → 微信内联 HTML ────────────────────────────────────────────

_C = {
    'primary':  '#0f766e',   # teal-700
    'accent':   '#0d9488',   # teal-600
    'gold':     '#d97706',   # amber-600
    'text':     '#1c3f3d',
    'text_mid': '#4a7575',
    'bg_main':  '#f0fdfa',
    'bg_quote': '#fffbeb',
    'border':   '#99f6e4',
}


def _inline(text: str) -> str:
    """内联 Markdown 标记 → HTML。"""
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(
        r'\*\*(.+?)\*\*',
        lambda m: f'<strong style="font-weight:700;color:{_C["primary"]};">{m.group(1)}</strong>',
        text,
    )
    text = re.sub(r'~~(.+?)~~', lambda m: f'<s>{m.group(1)}</s>', text)
    text = re.sub(
        r'\*(.+?)\*',
        lambda m: f'<em style="color:{_C["text_mid"]};font-style:italic;">{m.group(1)}</em>',
        text,
    )
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return text.strip()


def md_to_wechat_html(md: str) -> str:
    """简洁版 markdown → 微信内联样式 HTML。

    支持：## ### / > 引用 / **bold** / *italic* / 段落 / 分割线 / 无序列表（转段落）。
    不做花哨排版（Header/Footer/品牌横幅一律不加）。
    """
    lines = md.split('\n')
    out: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        # H1（少见，仿写 prompt 不让生成）
        if line.startswith('# ') and not line.startswith('## '):
            out.append(
                f'<section style="text-align:center;margin:24px 0 16px;">'
                f'<p style="margin:0;font-size:22px;font-weight:700;color:{_C["primary"]};'
                f'letter-spacing:2px;line-height:1.5;">{_inline(line[2:])}</p>'
                f'</section>'
            )
            i += 1
            continue

        # H2 — 左边线 + 浅青背景
        if line.startswith('## '):
            out.append(
                f'<section style="margin:28px 0 12px;padding:12px 18px;'
                f'background:{_C["bg_main"]};border-left:4px solid {_C["primary"]};'
                f'border-radius:0 6px 6px 0;">'
                f'<p style="margin:0;font-size:17px;font-weight:700;color:{_C["primary"]};'
                f'letter-spacing:1px;line-height:1.5;">{_inline(line[3:])}</p>'
                f'</section>'
            )
            i += 1
            continue

        # H3
        if line.startswith('### '):
            out.append(
                f'<p style="margin:20px 0 10px;font-size:16px;font-weight:700;'
                f'color:{_C["accent"]};line-height:1.6;">{_inline(line[4:])}</p>'
            )
            i += 1
            continue

        # 引用（多行连续）
        if line.startswith('> '):
            block = []
            while i < len(lines) and lines[i].rstrip().startswith('> '):
                block.append(lines[i].rstrip()[2:])
                i += 1
            inner = ''.join(
                f'<p style="margin:0 0 6px;font-size:15px;line-height:1.85;'
                f'color:{_C["text_mid"]};">{_inline(b)}</p>'
                for b in block if b.strip()
            )
            out.append(
                f'<section style="background:{_C["bg_quote"]};'
                f'border-left:4px solid {_C["gold"]};padding:14px 18px;'
                f'margin:16px 0;border-radius:0 6px 6px 0;">{inner}</section>'
            )
            continue

        # 分割线
        if re.match(r'^---+$', line.strip()):
            out.append(
                f'<p style="text-align:center;margin:20px 0;color:{_C["border"]};'
                f'letter-spacing:8px;font-size:14px;">· · ·</p>'
            )
            i += 1
            continue

        # 无序列表 → 多段
        if re.match(r'^[-*•] ', line):
            while i < len(lines) and re.match(r'^[-*•] ', lines[i].rstrip()):
                item = lines[i].rstrip()[2:]
                out.append(
                    f'<p style="margin:8px 0 8px 16px;font-size:16px;line-height:1.85;'
                    f'color:{_C["text"]};">· {_inline(item)}</p>'
                )
                i += 1
            continue

        # 普通段落
        out.append(
            f'<p style="margin:12px 0;font-size:16px;line-height:1.95;'
            f'color:{_C["text"]};letter-spacing:0.3px;">{_inline(line)}</p>'
        )
        i += 1

    return (
        f'<section style="font-family:-apple-system,BlinkMacSystemFont,'
        f'PingFang SC,Helvetica Neue,sans-serif;'
        f'color:{_C["text"]};padding:0 4px;">'
        + '\n'.join(out)
        + '</section>'
    )


def _insert_images(html: str, urls: list[str]) -> str:
    """每隔 2 个章节插一张图（最后一节前结束）。"""
    if not urls:
        return html
    sections = re.split(r'(?=<section style="margin:28px)', html)
    if len(sections) <= 1:
        return html

    def img(u: str) -> str:
        return (
            f'<section style="text-align:center;margin:20px 0;">'
            f'<img src="{u}" style="max-width:100%;border-radius:6px;'
            f'display:block;margin:0 auto;"/>'
            f'</section>'
        )

    out = [sections[0]]
    idx = 0
    last = len(sections) - 1
    for i, sec in enumerate(sections[1:], start=1):
        out.append(sec)
        if i % 2 == 0 and idx < len(urls) and i < last:
            out.append(img(urls[idx]))
            idx += 1
    return ''.join(out)


def _extract_digest(content: str, max_len: int = 54) -> str:
    """微信 digest 上限 54 汉字（errcode 45004）。"""
    plain = re.sub(r'#{1,3}\s+', '', content or '')
    plain = re.sub(r'\*\*(.+?)\*\*', r'\1', plain)
    plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', plain)
    plain = re.sub(r'\s+', ' ', plain).strip()
    return plain[:max_len] + '…' if len(plain) > max_len else plain


# ── 推送器 ────────────────────────────────────────────────────────────────────

class WeChatPublisher:
    def __init__(self, user_wechat_cfg: dict):
        self.app_id     = user_wechat_cfg.get('app_id', '').strip()
        self.app_secret = user_wechat_cfg.get('app_secret', '').strip()
        self.proxy      = user_wechat_cfg.get('proxy') or None   # socks5h://...
        self.author     = user_wechat_cfg.get('author', '') or ''
        self.enabled    = bool(self.app_id and self.app_secret)

    # ── HTTP 客户端 ──────────────────────────────────────────────────

    @contextmanager
    def _api(self, **kw):
        """微信 API 客户端走代理（IP 白名单）。"""
        with httpx.Client(proxy=self.proxy, **kw) as c:
            yield c

    @contextmanager
    def _dl(self, **kw):
        """图片下载客户端不走代理。"""
        with httpx.Client(**kw) as c:
            yield c

    # ── access_token 缓存 ───────────────────────────────────────────

    def get_token(self) -> str:
        cached = _TOKEN_CACHE.get(self.app_id)
        if cached and time.time() < cached[1] - 60:
            return cached[0]
        with self._api(timeout=15) as c:
            r = c.get(f'{_API}/token', params={
                'grant_type': 'client_credential',
                'appid':      self.app_id,
                'secret':     self.app_secret,
            })
        data = r.json()
        if data.get('errcode'):
            raise RuntimeError(f'access_token 获取失败: {data}')
        token = data['access_token']
        _TOKEN_CACHE[self.app_id] = (token, time.time() + data.get('expires_in', 7200))
        log.info('[wechat] %s access_token 已刷新', self.app_id)
        return token

    # ── 图片处理 ────────────────────────────────────────────────────

    @staticmethod
    def _compress(img_bytes: bytes, max_w: int = 1080, max_kb: int = 900) -> tuple[bytes, str]:
        """压到 1080px 宽 + 900KB 以内（防 errcode 40009）。"""
        img = Image.open(BytesIO(img_bytes)).convert('RGB')
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
        for q in (85, 75, 65, 50, 40):
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=q, optimize=True)
            if buf.tell() <= max_kb * 1024:
                break
        return buf.getvalue(), 'image/jpeg'

    def _download_image(self, url: str) -> tuple[bytes, str]:
        with self._dl(timeout=20, follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            raise RuntimeError(f'图片下载 HTTP {r.status_code}: {url[:80]}')
        ct = r.headers.get('content-type', 'image/jpeg').split(';')[0]
        return r.content, ct

    def upload_cover(self, url: str) -> str:
        """上传永久素材作为封面，返回 thumb_media_id。"""
        img, _ = self._download_image(url)
        img, ct = self._compress(img)
        token = self.get_token()
        with self._api(timeout=30) as c:
            r = c.post(
                f'{_API}/material/add_material',
                params={'access_token': token, 'type': 'image'},
                files={'media': ('cover.jpg', img, ct)},
            )
        data = r.json()
        if data.get('errcode'):
            raise RuntimeError(f'封面上传失败: {data}')
        log.info('[wechat] 封面 media_id=%s', data['media_id'])
        return data['media_id']

    def upload_inline(self, url: str) -> Optional[str]:
        """上传内嵌图，返回微信 CDN URL。失败返回 None（让正文继续，缺图无碍）。"""
        try:
            img, _ = self._download_image(url)
            img, ct = self._compress(img)
            token = self.get_token()
            with self._api(timeout=30) as c:
                r = c.post(
                    f'{_API}/media/uploadimg',
                    params={'access_token': token},
                    files={'media': ('inline.jpg', img, ct)},
                )
            data = r.json()
            if data.get('errcode'):
                log.warning('[wechat] 内嵌图上传失败: %s', data)
                return None
            return data.get('url') or None
        except Exception as e:
            log.warning('[wechat] 内嵌图异常 %s: %s', url[:60], e)
            return None

    # ── 草稿创建 ────────────────────────────────────────────────────

    def create_draft(self, title: str, content_html: str,
                     thumb_media_id: str, digest: str = '') -> str:
        token = self.get_token()
        article = {
            'title':                  title[:64],          # 微信标题上限 64 字
            'author':                 self.author,
            'content':                content_html,
            'thumb_media_id':         thumb_media_id,
            'digest':                 digest,
            'need_open_comment':      0,
            'only_fans_can_comment':  0,
        }
        with self._api(timeout=30) as c:
            r = c.post(
                f'{_API}/draft/add',
                params={'access_token': token},
                json={'articles': [article]},
            )
        data = r.json()
        if data.get('errcode'):
            raise RuntimeError(f'创建草稿失败: {data}')
        log.info('[wechat] 草稿 media_id=%s', data['media_id'])
        return data['media_id']

    # ── 主入口 ──────────────────────────────────────────────────────

    def push(self, draft: dict) -> dict:
        """推送一条草稿。

        draft 字段：title / content（markdown） / image_urls（逗号分隔本地 HTTP URL）

        返回 dict：
          成功：{'ok': True,  'media_id': '...'}
          失败：{'ok': False, 'error': '...'}
        """
        if not self.enabled:
            return {'ok': False, 'error': 'app_id / app_secret 未配置'}

        title   = (draft.get('title')   or '').strip() or '(无标题)'
        content = (draft.get('content') or '').strip()
        image_urls = [u.strip() for u in (draft.get('image_urls') or '').split(',') if u.strip()]

        try:
            # 上传内嵌图（每章节插一张），最多 3 张避免请求过多
            inline_urls: list[str] = []
            for u in image_urls[:4]:
                wx_url = self.upload_inline(u)
                if wx_url:
                    inline_urls.append(wx_url)

            content_html = md_to_wechat_html(content)
            content_html = _insert_images(content_html, inline_urls)
            digest       = _extract_digest(content)

            cover_url = image_urls[0] if image_urls else ''
            if not cover_url:
                return {'ok': False, 'error': '无可用封面图'}
            thumb_media_id = self.upload_cover(cover_url)

            media_id = self.create_draft(title, content_html, thumb_media_id, digest)
            return {'ok': True, 'media_id': media_id}

        except Exception as e:
            log.warning('[wechat] push 失败: %s', e)
            return {'ok': False, 'error': str(e)}
