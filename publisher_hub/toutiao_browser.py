"""今日头条号 Chrome 实例管理 + 扫码绑定 + 登录态检查（async_playwright 版）。

每个 publisher-hub 用户独享一个 Chrome 子进程：
  - CDP 端口 9230+ 自动分配，写入 user.toutiao.cdp_port
  - user_data_dir = /data/publisher-hub/chrome/toutiao-<user_id>/  持久化 cookie

用 async_playwright 而非 sync_playwright（后者在 FastAPI 进程里多次调用
会出 'PlaywrightContextManager has no _playwright' 状态污染错误）。
"""
from __future__ import annotations

import asyncio
import base64
import glob
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger('publisher_hub.toutiao_browser')

# stealth.js（伪装 headless 特征：navigator.webdriver / plugins / languages 等）
_STEALTH_JS = Path(__file__).parent / 'vendor' / 'stealth.min.js'


# ── Chrome 路径与数据目录 ────────────────────────────────────────────────────

def _find_chrome() -> str:
    candidates = [
        '/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome',
        '/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome',
        os.path.expanduser('~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/chrome'),
        os.path.expanduser('~/Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium'),
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ]
    for p in candidates:
        matches = sorted(glob.glob(p), reverse=True) if '*' in p else ([p] if os.path.exists(p) else [])
        for m in matches:
            if os.path.exists(m):
                return m
    raise FileNotFoundError(f'找不到 Chrome 可执行文件，已尝试: {candidates}')


def _data_root() -> Path:
    server = Path('/data/publisher-hub/chrome')
    if server.parent.parent.exists():
        server.mkdir(parents=True, exist_ok=True)
        return server
    fallback = Path('/tmp/publisher-hub-chrome')
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


# ── 单用户 Chrome 实例 ───────────────────────────────────────────────────────

class ToutiaoBrowser:
    def __init__(self, user_id: str, cdp_port: int):
        self.user_id        = user_id
        self.cdp_port       = int(cdp_port)
        self.user_data_dir  = _data_root() / f'toutiao-{user_id}'
        self.chrome_path    = _find_chrome()
        # 状态缓存：HTMX 频繁轮询时复用，避免重复 page.goto
        self._cache_result: Optional[dict] = None
        self._cache_at: float = 0.0
        self._cache_ttl: float = 30.0      # 30 秒内复用结果

    # ── 进程管理（同步即可，subprocess.Popen 不阻塞）────────────────────

    def is_running(self) -> bool:
        try:
            with socket.create_connection(('127.0.0.1', self.cdp_port), timeout=1):
                return True
        except OSError:
            return False

    def start(self) -> bool:
        if self.is_running():
            return True
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.chrome_path,
            f'--remote-debugging-port={self.cdp_port}',
            f'--user-data-dir={self.user_data_dir}',
            '--no-first-run', '--no-default-browser-check', '--disable-default-apps',
            '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--headless=new', '--noerrdialogs',
            '--ozone-platform=headless',
            '--ozone-override-screen-size=1280,800',
            '--use-angle=swiftshader-webgl',
        ]
        log.info('[toutiao] %s 启动 Chrome :%d  user_data=%s',
                 self.user_id, self.cdp_port, self.user_data_dir)
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(40):
            if self.is_running():
                return True
            time.sleep(0.5)
        log.error('[toutiao] %s Chrome 启动超时', self.user_id)
        return False

    def stop(self):
        if not self.is_running():
            return
        subprocess.run(
            ['pkill', '-f', f'--remote-debugging-port={self.cdp_port}'],
            check=False,
        )
        log.info('[toutiao] %s Chrome :%d 已停', self.user_id, self.cdp_port)

    def invalidate_cache(self):
        self._cache_result = None
        self._cache_at = 0.0

    # ── async playwright 辅助 ────────────────────────────────────────────

    async def _with_page_async(self, fn):
        """连 CDP → 新开 page（注入 stealth.js）→ await fn(page, ctx) → 关 page。"""
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f'http://127.0.0.1:{self.cdp_port}'
            )
            try:
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()

                # 注入 stealth（修改 navigator.webdriver 等，每次 new_page 都生效）
                if _STEALTH_JS.exists():
                    try:
                        await ctx.add_init_script(path=str(_STEALTH_JS))
                    except Exception as e:
                        log.debug('[toutiao] stealth init_script 注入失败: %s', e)

                # 始终新建 page（保证 stealth 在 navigation 前已注入）
                page = await ctx.new_page()
                try:
                    return await fn(page, ctx)
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass
            finally:
                await browser.close()

    # ── 登录态检查 ────────────────────────────────────────────────────────

    async def check_login(self, force: bool = False) -> dict:
        if not force and self._cache_result and (time.time() - self._cache_at) < self._cache_ttl:
            return self._cache_result

        if not self.start():
            result = {'status': 'error', 'error': 'Chrome 启动失败'}
            self._cache_result, self._cache_at = result, time.time()
            return result

        async def _do(page, ctx):
            try:
                await page.goto(
                    'https://mp.toutiao.com', wait_until='commit', timeout=30_000,
                )
            except Exception as e:
                log.warning('[toutiao] %s page.goto 超时/异常: %s', self.user_id, e)
                url = ''
                try:
                    url = page.url
                except Exception:
                    pass
                return {'status': 'logged_out', 'url': url, 'reason': 'goto_timeout'}

            # 等 url 稳定（SPA redirect 可能需要 3-5 秒）
            last_url = ''
            for _ in range(10):
                await asyncio.sleep(1)
                cur = page.url
                if cur and cur == last_url:
                    break
                last_url = cur
            url = last_url

            if any(k in url for k in ('sso.', 'passport.', '/login', 'auth/')):
                return {'status': 'logged_out', 'url': url}

            # 正向判定：已登录必有 session 类 cookie，否则即便 url 看起来 OK 也是未登录
            cookies = await ctx.cookies()
            cookie_names = {c.get('name', '').lower() for c in cookies}
            session_cookies = {'sessionid', 'sid_tt', 'sid_guard', 'uid_tt',
                               'uid_tt_ss', 'sid_ucp_v1', 'ssid_ucp_v1'}
            if not (cookie_names & session_cookies):
                # body 文本兜底确认（防止误杀）
                try:
                    body_text = await page.evaluate(
                        'document.body && document.body.innerText.slice(0, 500) || ""'
                    )
                except Exception:
                    body_text = ''
                if any(k in body_text for k in ('扫码登录', '获取验证码', '验证码登录', '注册')):
                    return {'status': 'logged_out', 'url': url,
                            'reason': 'no_session_cookie+login_body'}
                # 没 session cookie 但也没登录页文本 → 仍判 logged_out（保守）
                return {'status': 'logged_out', 'url': url,
                        'reason': 'no_session_cookie'}

            # 提取用户名：DOM 多 selector 尝试 + cookie 兜底
            name = ''
            for sel in [
                '[class*="user-name"]', '[class*="username"]',
                '[class*="UserName"]', '[class*="nickname"]',
                '[class*="NickName"]', '[class*="user_name"]',
                '[class*="userInfo"] [class*="name"]',
                '.byte-avatar + *',
                'header [class*="name"]',
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = (await el.inner_text() or '').strip()
                        if text and 1 <= len(text) <= 30:
                            name = text
                            break
                except Exception:
                    pass

            # 从 cookie 兜底拿 name（头条号常存 nickname 类 cookie）
            if not name:
                for c in cookies:
                    cn = c.get('name', '').lower()
                    if cn in ('nickname', 'user_name', 'screen_name', 'login_name'):
                        cv = (c.get('value') or '').strip()
                        try:
                            from urllib.parse import unquote
                            cv = unquote(cv)
                        except Exception:
                            pass
                        if cv and len(cv) <= 30:
                            name = cv
                            break

            exp_days = None
            for c in cookies:
                n = c.get('name', '').lower()
                if 'session' in n or 'sid' == n or 'sso' in n:
                    exp = c.get('expires', -1)
                    if exp > 0:
                        exp_days = max(0, int((exp - time.time()) / 86400))
                        break

            return {
                'status': 'logged_in',
                'url': url,
                'name': name,                  # 空字符串而不是 '(未识别)'
                'cookie_expires_days': exp_days,
            }

        try:
            result = await self._with_page_async(_do)
        except Exception as e:
            log.warning('[toutiao] %s check_login 整体异常: %s', self.user_id, e)
            result = {'status': 'logged_out', 'reason': f'check_exception: {e}'}

        self._cache_result, self._cache_at = result, time.time()
        return result

    # ── 截二维码 ──────────────────────────────────────────────────────────

    _REAL_UA = (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
    )

    # 特殊返回值：表示已登录，没二维码可截
    ALREADY_LOGGED_IN = 'ALREADY_LOGGED_IN'

    async def capture_login_page(self) -> Optional[str]:
        """截二维码登录页。返回值：
            - data:image/png;base64,...  正常截到二维码
            - 'ALREADY_LOGGED_IN'        已登录，无需扫码
            - None                       异常 / 截图失败
        """
        if not self.start():
            log.warning('[toutiao] %s capture: Chrome 启动失败', self.user_id)
            return None

        async def _do(page, ctx):
            try:
                await ctx.set_extra_http_headers({'User-Agent': self._REAL_UA})
            except Exception:
                pass
            await page.set_viewport_size({'width': 1280, 'height': 900})

            try:
                resp = await page.goto(
                    'https://mp.toutiao.com', wait_until='commit', timeout=30_000,
                )
                log.info('[toutiao] %s goto OK status=%s url=%s',
                         self.user_id, resp.status if resp else 'no_resp', page.url)
            except Exception as e:
                log.warning('[toutiao] %s goto 失败: %s', self.user_id, e)
                return None

            # 等 url 稳定（最多 8s），同时为 cookie 写入留时间
            last_url = ''
            for _ in range(8):
                await asyncio.sleep(1)
                cur = page.url
                if cur and cur == last_url:
                    break
                last_url = cur
            url_now = last_url
            log.info('[toutiao] %s after settle url=%s', self.user_id, url_now)

            # 已登录判定：必须有 session 类 cookie，否则即便 url 显示 dashboard 也按未登录处理
            cookies = await ctx.cookies()
            cookie_names = {c.get('name', '').lower() for c in cookies}
            session_cookies = {'sessionid', 'sid_tt', 'sid_guard', 'uid_tt',
                               'uid_tt_ss', 'sid_ucp_v1', 'ssid_ucp_v1'}
            if (cookie_names & session_cookies) and not any(
                k in url_now for k in ('sso.', 'passport.', '/login', 'auth/')
            ):
                log.info('[toutiao] %s 已登录 (cookie ok, url=%s)，跳过截图',
                         self.user_id, url_now)
                return self.ALREADY_LOGGED_IN

            # 等二维码元素
            try:
                await page.wait_for_selector(
                    'canvas, img[src*="qr"], [class*="qrcode"], [class*="QRCode"], [class*="QrCode"]',
                    timeout=8_000,
                )
                log.info('[toutiao] %s 二维码元素加载完成', self.user_id)
            except Exception:
                log.info('[toutiao] %s 未找到二维码元素（可能反爬）', self.user_id)

            await asyncio.sleep(2)
            png = await page.screenshot(full_page=False, type='png')
            log.info('[toutiao] %s screenshot %d bytes', self.user_id, len(png))
            return f'data:image/png;base64,{base64.b64encode(png).decode()}'

        try:
            return await self._with_page_async(_do)
        except Exception as e:
            log.warning('[toutiao] %s capture 整体异常: %s', self.user_id, e)
            return None

    # ── 解绑（清登录态） ──────────────────────────────────────────────────

    def unbind(self):
        self.stop()
        time.sleep(1)
        if self.user_data_dir.exists():
            shutil.rmtree(self.user_data_dir)
            log.info('[toutiao] %s user_data_dir 已删', self.user_id)
        self.invalidate_cache()


# ── 全局缓存 + 端口分配 ─────────────────────────────────────────────────────

_browsers: dict[str, ToutiaoBrowser] = {}
_PORT_BASE = 9230


def get_browser(user_id: str, cdp_port: int) -> ToutiaoBrowser:
    cached = _browsers.get(user_id)
    if cached and cached.cdp_port == int(cdp_port):
        return cached
    if cached:
        cached.stop()
    b = ToutiaoBrowser(user_id, cdp_port)
    _browsers[user_id] = b
    return b


def allocate_port(config: dict, exclude_user_id: Optional[str] = None) -> int:
    """找下一个未被任何用户占用的 CDP 端口。"""
    used = set()
    for u in config.get('users') or []:
        if u.get('id') == exclude_user_id:
            continue
        t = u.get('toutiao') or {}
        if t.get('cdp_port'):
            used.add(int(t['cdp_port']))
    port = _PORT_BASE
    while port in used:
        port += 1
    return port
