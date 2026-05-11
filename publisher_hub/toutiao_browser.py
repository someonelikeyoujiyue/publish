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
        """连 CDP → await fn(page, ctx) → 自动 close。"""
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f'http://127.0.0.1:{self.cdp_port}'
            )
            try:
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                return await fn(page, ctx)
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

            await asyncio.sleep(1)
            url = page.url

            if any(k in url for k in ('sso.', 'passport.', '/login', 'auth/')):
                return {'status': 'logged_out', 'url': url}

            name = ''
            for sel in [
                '[class*="user-name"]', '[class*="username"]',
                '[class*="UserName"]', '.avatar img',
                'img[alt]',
            ]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        alt = (await el.get_attribute('alt')) if not text else ''
                        candidate = (text or alt or '').strip()
                        if candidate and len(candidate) <= 30:
                            name = candidate
                            break
                except Exception:
                    pass

            cookies = await ctx.cookies()
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
                'name': name or '(未识别)',
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

    async def capture_login_page(self) -> Optional[str]:
        if not self.start():
            return None

        async def _do(page, ctx):
            await page.set_viewport_size({'width': 1280, 'height': 800})
            try:
                await page.goto(
                    'https://mp.toutiao.com', wait_until='commit', timeout=30_000,
                )
            except Exception as e:
                log.warning('[toutiao] capture goto 异常: %s', e)
            await asyncio.sleep(3)
            png = await page.screenshot(full_page=False, type='png')
            return f'data:image/png;base64,{base64.b64encode(png).decode()}'

        try:
            return await self._with_page_async(_do)
        except Exception as e:
            log.warning('[toutiao] %s capture 异常: %s', self.user_id, e)
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
