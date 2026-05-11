"""头条号微头条 (weitoutiao) 自动发布。

通过 CDP 连到用户已绑定的 Chrome → 进发布页 → 把 title+content 写到 ProseMirror
编辑器 → 点发布按钮。

跟 wechat / xhs publisher 不一样：
- wechat 走官方 API（HTTPS POST）
- xhs 之前走 myaibot 拿二维码
- toutiao 走 DOM 操作（因为没公开 API），需要用户先扫码绑定
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .toutiao_browser import ToutiaoBrowser

log = logging.getLogger('publisher_hub.toutiao_publisher')

PUBLISH_URL = 'https://mp.toutiao.com/profile_v4/weitoutiao/publish'


async def publish_weitoutiao(
    browser: ToutiaoBrowser,
    title: str,
    content: str,
    save_draft_only: bool = False,
) -> dict:
    """发一条微头条。

    Args:
        browser: 用户的 ToutiaoBrowser 实例（必须已扫码登录）
        title:   仿写出来的标题，会拼到正文最前面
        content: 仿写正文
        save_draft_only: True 只点"存草稿"，False 点"发布"

    Returns:
        {'ok': True, 'url': ...}
        {'ok': False, 'error': '...'}
    """
    if not browser.start():
        return {'ok': False, 'error': 'Chrome 启动失败'}

    # 微头条没有独立标题字段，把 title 拼到正文开头
    full_text = f'{title}\n\n{content}'.strip() if title else content

    async def _do(page, ctx):
        try:
            await page.goto(PUBLISH_URL, wait_until='commit', timeout=30_000)
        except Exception as e:
            return {'ok': False, 'error': f'goto 失败: {e}'}

        # 等 dashboard SPA + 编辑器加载
        await asyncio.sleep(3)
        url = page.url
        if any(k in url for k in ('sso.', 'passport.', '/login', 'auth/')):
            return {'ok': False, 'error': '未登录或登录已失效，请先去头条号 tab 重新扫码'}

        try:
            await page.wait_for_selector('.ProseMirror', timeout=15_000)
        except Exception:
            return {'ok': False, 'error': 'ProseMirror 编辑器未加载（可能页面变了或被反爬挡了）'}

        # 聚焦编辑器 + 输入文本
        await page.click('.ProseMirror')
        await asyncio.sleep(0.3)
        # delay=5 ms/char，900 字约 4.5 秒；太快 ProseMirror 可能漏字
        try:
            await page.keyboard.type(full_text, delay=5)
        except Exception as e:
            return {'ok': False, 'error': f'输入文本失败: {e}'}

        # 等编辑器把内容渲染完
        await asyncio.sleep(1.5)

        # 校验编辑器内容长度
        try:
            editor_text = await page.evaluate(
                'document.querySelector(".ProseMirror")?.innerText || ""'
            )
            if len(editor_text.strip()) < min(20, len(full_text) * 0.5):
                return {'ok': False, 'error': f'内容写入异常 (编辑器只剩 {len(editor_text)} 字)'}
        except Exception:
            pass

        # 点发布 / 存草稿
        btn_sel = '.save-draft' if save_draft_only else '.publish-co'
        try:
            btn = await page.wait_for_selector(f'{btn_sel}:not([disabled])', timeout=8_000)
            if not btn:
                return {'ok': False, 'error': f'找不到按钮 {btn_sel}'}
            await btn.click()
        except Exception as e:
            return {'ok': False, 'error': f'点击按钮失败: {e}'}

        # 等发布完成。成功的信号可能是：
        #   - URL 跳转回 dashboard
        #   - 出现"发布成功"toast
        #   - 出现成功 modal
        await asyncio.sleep(3)
        final_url = page.url
        # 简单判断：URL 不再是 publish 页 = 发布成功；否则可能是失败 + 错误提示
        if 'weitoutiao/publish' not in final_url:
            log.info('[toutiao_publisher] ✓ %s url=%s', browser.user_id, final_url)
            return {'ok': True, 'final_url': final_url, 'mode': 'draft' if save_draft_only else 'publish'}

        # 还在发布页 → 看下页面有没有错误 toast
        try:
            err_text = await page.evaluate("""() => {
              const t = document.querySelector('[class*="message"], [class*="toast"], [class*="notice"]');
              return t ? (t.innerText || '').trim().slice(0, 200) : '';
            }""")
        except Exception:
            err_text = ''
        log.warning('[toutiao_publisher] %s 仍在 publish 页 url=%s err_text=%s',
                    browser.user_id, final_url, err_text)
        return {'ok': False, 'error': err_text or '点击发布后页面没跳转，可能未真正发出（看日志）'}

    try:
        return await browser._with_page_async(_do)
    except Exception as e:
        log.exception('[toutiao_publisher] %s 整体异常', browser.user_id)
        return {'ok': False, 'error': str(e)}


# CLI 入口（手动测试用）
async def _cli(user_id: str, title: str, content: str, draft: bool):
    from .config import load_config
    cfg = load_config()
    user_raw = next((u for u in cfg.get('users') or [] if u.get('id') == user_id), None)
    if not user_raw:
        print(f'用户 {user_id} 不存在'); return
    port = (user_raw.get('toutiao') or {}).get('cdp_port')
    if not port:
        print(f'用户 {user_id} 未绑定头条号'); return
    b = ToutiaoBrowser(user_id, int(port))
    print(await publish_weitoutiao(b, title, content, save_draft_only=draft))


if __name__ == '__main__':
    import argparse, sys
    p = argparse.ArgumentParser(prog='python -m publisher_hub.toutiao_publisher')
    p.add_argument('user_id')
    p.add_argument('title')
    p.add_argument('content')
    p.add_argument('--draft', action='store_true', help='只存草稿不发布')
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    asyncio.run(_cli(args.user_id, args.title, args.content, args.draft))
