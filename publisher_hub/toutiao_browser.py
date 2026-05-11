"""今日头条号 Chrome 实例管理 + 扫码绑定 + 登录态检查。

每个 publisher-hub 用户独享一个 Chrome 子进程：
  - CDP 端口 9230+ 自动分配，写入 user.toutiao.cdp_port
  - user_data_dir = /data/publisher-hub/chrome/toutiao-<user_id>/  持久化 cookie

扫码流程：
  1. POST /{uid}/toutiao/bind → 启 Chrome → playwright connect_over_cdp
  2. 打开 https://mp.toutiao.com → 等二维码 canvas 加载
  3. 整页 screenshot → base64 PNG → 前端 modal 展示
  4. 前端轮询 /{uid}/toutiao/status → page.url 跳到 dashboard = 登录成功
"""
from __future__ import annotations

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
        # 服务器：playwright bundled chromium（newmedia 验证过的版本）
        '/root/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome',
        '/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome',
        # 本地 macOS
        os.path.expanduser('~/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/chrome'),
        os.path.expanduser('~/Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium'),
        # System Chrome
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
    # 服务器优先 /data/publisher-hub/chrome（持久），本地用 /tmp
    server = Path('/data/publisher-hub/chrome')
    if server.parent.parent.exists():        # /data 存在 → 服务器侧
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

    # ── 进程管理 ──────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """CDP 端口可达 = Chrome 在跑。"""
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

    # ── Playwright 辅助 ───────────────────────────────────────────────────

    def _with_page(self, fn):
        """连 CDP → 调 fn(page, ctx) → 自动 close。"""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f'http://127.0.0.1:{self.cdp_port}')
            try:
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                return fn(page, ctx)
            finally:
                browser.close()

    # ── 登录态检查 ────────────────────────────────────────────────────────

    def check_login(self) -> dict:
        """返回 {'status': 'logged_in'/'logged_out'/'error', ...}。"""
        if not self.start():
            return {'status': 'error', 'error': 'Chrome 启动失败'}

        def _do(page, ctx):
            try:
                page.goto('https://mp.toutiao.com', wait_until='domcontentloaded', timeout=15_000)
            except Exception as e:
                return {'status': 'error', 'error': f'page.goto: {e}'}
            time.sleep(2)
            url = page.url

            # 未登录通常 redirect 到 sso.snssdk.com / passport / login
            if any(k in url for k in ('sso.', 'passport.', '/login', 'auth/')):
                return {'status': 'logged_out', 'url': url}

            # 已登录：抽取用户名（多个 selector 尝试）
            name = ''
            for sel in [
                '[class*="user-name"]', '[class*="username"]',
                '[class*="UserName"]', '.avatar img',
                'img[alt]',
            ]:
                try:
                    el = page.query_selector(sel)
                    if el:
                        name = (el.inner_text() or el.get_attribute('alt') or '').strip()
                        if name and len(name) <= 30:
                            break
                except Exception:
                    pass

            # cookie 过期检查
            cookies = ctx.cookies()
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
            return self._with_page(_do)
        except Exception as e:
            log.warning('[toutiao] %s check_login 异常: %s', self.user_id, e)
            return {'status': 'error', 'error': str(e)}

    # ── 截二维码（整页 screenshot） ───────────────────────────────────────

    def capture_login_page(self) -> Optional[str]:
        """返回整页 base64 PNG（前端 <img src=...> 直接展示，含二维码）。"""
        if not self.start():
            return None

        def _do(page, ctx):
            page.set_viewport_size({'width': 1280, 'height': 800})
            try:
                page.goto('https://mp.toutiao.com', wait_until='domcontentloaded', timeout=15_000)
            except Exception as e:
                log.warning('[toutiao] capture goto 异常: %s', e)
            time.sleep(3)              # 等二维码 canvas 加载
            png = page.screenshot(full_page=False, type='png')
            return f'data:image/png;base64,{base64.b64encode(png).decode()}'

        try:
            return self._with_page(_do)
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
