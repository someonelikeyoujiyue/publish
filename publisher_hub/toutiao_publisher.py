"""头条号微头条 (weitoutiao) 自动发布。

通过 CDP 连到用户已绑定的 Chrome → 进发布页 → 关掉"发布助手"抽屉 →
把 title+content 写到 ProseMirror 编辑器 → 上传图片 → 点发布/存草稿。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import urllib.request
from typing import Optional

from .toutiao_browser import ToutiaoBrowser

log = logging.getLogger('publisher_hub.toutiao_publisher')

PUBLISH_URL = 'https://mp.toutiao.com/profile_v4/weitoutiao/publish'

# publisher-hub 现在跨机部署：图片服务在 47.236.168.208:8899（旧机），
# publisher-hub 本身在 5.189.184.60。直接走公网 IP，不做 host 改写。
# （早期版本同机部署时会改写成 127.0.0.1 走内网，跨机后改不掉就用原 URL。）


def _download_images(urls: list[str], max_count: int = 9) -> list[str]:
    """同步下载远程图片到 /tmp，压缩后返回本地路径列表。

    RSU 兜底图原图可达 19MB（4K+ 像素）—— set_input_files 等待上传 ack
    会超时（30s 仍未完成）。这里下载后用 PIL thumbnail 到 2000px + JPEG q85，
    单张通常压到 < 500KB。微头条最多 9 张图。
    """
    from PIL import Image

    paths: list[str] = []
    tmpdir = tempfile.mkdtemp(prefix='toutiao_upload_')
    for i, raw in enumerate(urls[:max_count]):
        url = raw.strip()
        if not url:
            continue
        try:
            raw_path = os.path.join(tmpdir, f'raw{i:02d}')
            urllib.request.urlretrieve(url, raw_path)
            raw_size = os.path.getsize(raw_path) / 1024
            img = Image.open(raw_path)
            img.thumbnail((2000, 2000), Image.LANCZOS)
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            out = os.path.join(tmpdir, f'img{i:02d}.jpg')
            img.save(out, 'JPEG', quality=85, optimize=True)
            os.unlink(raw_path)
            out_size = os.path.getsize(out) / 1024
            log.info('[toutiao_publisher] 图片 %d: %.0fKB → %.0fKB', i, raw_size, out_size)
            paths.append(out)
        except Exception as e:
            log.warning('[toutiao_publisher] 下载/压缩图片失败 %s: %s', url, e)
    return paths


def _cleanup(paths: list[str]):
    if not paths:
        return
    try:
        d = os.path.dirname(paths[0])
        for p in paths:
            try: os.unlink(p)
            except Exception: pass
        try: os.rmdir(d)
        except Exception: pass
    except Exception:
        pass


async def publish_weitoutiao(
    browser: ToutiaoBrowser,
    title: str,
    content: str,
    images: Optional[list[str]] = None,
    save_draft_only: bool = False,
) -> dict:
    """发一条微头条。

    Args:
        browser: 用户的 ToutiaoBrowser 实例（必须已扫码登录）
        title:   仿写出来的标题，会拼到正文最前面
        content: 仿写正文
        images:  图片 URL 列表（可空），自动下载到 /tmp 后通过 file input 上传
        save_draft_only: True 只点"存草稿"，False 点"发布"

    Returns:
        {'ok': True, 'mode': 'publish'|'draft', 'final_url'?: str, 'toast'?: str}
        {'ok': False, 'error': '...'}
    """
    if not browser.start():
        return {'ok': False, 'error': 'Chrome 启动失败'}

    # 微头条没有独立标题字段，把 title 拼到正文开头
    full_text = f'{title}\n\n{content}'.strip() if title else content

    # 下载图片（同步、阻塞）
    image_paths: list[str] = []
    if images:
        image_paths = _download_images(images)
        log.info('[toutiao_publisher] %s 下载了 %d/%d 张图', browser.user_id, len(image_paths), len(images))

    async def _do(page, ctx):
        try:
            await page.goto(PUBLISH_URL, wait_until='commit', timeout=30_000)
        except Exception as e:
            return {'ok': False, 'error': f'goto 失败: {e}'}

        await asyncio.sleep(3)
        url = page.url
        if any(k in url for k in ('sso.', 'passport.', '/login', 'auth/')):
            return {'ok': False, 'error': '未登录或登录已失效，请先去头条号 tab 重新扫码'}

        try:
            await page.wait_for_selector('.ProseMirror', timeout=15_000)
        except Exception:
            return {'ok': False, 'error': 'ProseMirror 编辑器未加载'}

        # 关掉"发布助手"侧拉抽屉
        try:
            removed = await page.evaluate("""() => {
              const ms = document.querySelectorAll(
                '.byte-drawer-mask, .publish-assistant-old-drawer, .byte-drawer-wrapper'
              );
              ms.forEach(el => el.remove());
              return ms.length;
            }""")
            if removed:
                log.info('[toutiao_publisher] %s 关掉了 %d 个 drawer 元素', browser.user_id, removed)
                await asyncio.sleep(0.3)
        except Exception:
            pass

        # 写正文
        await page.click('.ProseMirror')
        await asyncio.sleep(0.3)
        try:
            await page.keyboard.type(full_text, delay=5)
        except Exception as e:
            return {'ok': False, 'error': f'输入文本失败: {e}'}
        await asyncio.sleep(1.5)

        # 上传图片：点"图片"按钮 → 动态生成 input[type=file] → setInputFiles
        if image_paths:
            try:
                # 触发图片 toolbar 按钮（不能用 page.click 因为按钮文本"图片"重复出现在别处）
                await page.evaluate("""() => {
                  const btns = Array.from(document.querySelectorAll('.syl-toolbar-button'));
                  const img = btns.find(b => (b.innerText||'').includes('图片'));
                  if (img) img.click();
                }""")
                await asyncio.sleep(0.5)
                # 用 page.set_input_files（比 ElementHandle 更鲁棒，内部会重试 selector）
                try:
                    await page.set_input_files(
                        'input[type=file][accept^="image"]',
                        image_paths,
                        timeout=10_000,
                    )
                    log.info('[toutiao_publisher] %s 已 setInputFiles %d 张', browser.user_id, len(image_paths))
                except Exception as e:
                    log.warning('[toutiao_publisher] %s setInputFiles 失败: %s', browser.user_id, e)
                    raise

                # 等头条号"上传图片"抽屉里"已上传 N 张图片"计数到位（每张 1-5s）
                n_files = len(image_paths)
                try:
                    await page.wait_for_function(
                        f"""() => {{
                          const el = document.querySelector('.upload-image-wrapper');
                          if (!el) return false;
                          const m = (el.innerText || '').match(/已上传\\s*(\\d+)\\s*张/);
                          return m && parseInt(m[1]) >= {n_files};
                        }}""",
                        timeout=min(n_files * 8_000 + 3_000, 60_000),
                    )
                    log.info('[toutiao_publisher] %s ✓ 已上传 %d 张到面板', browser.user_id, n_files)
                except Exception:
                    log.warning('[toutiao_publisher] %s 等待"已上传 %d 张"超时（继续点确定）',
                                browser.user_id, n_files)
                await asyncio.sleep(0.8)

                # 点抽屉里的"确定"按钮（必须点才会把图片插入正文 ProseMirror）
                try:
                    clicked = await page.evaluate("""() => {
                      const drawer = document.querySelector('.byte-drawer-wrapper');
                      if (!drawer) return 'no-drawer';
                      const btns = Array.from(drawer.querySelectorAll('button'));
                      const ok = btns.find(b => (b.innerText||'').trim() === '确定');
                      if (!ok) return 'no-ok-btn';
                      if (ok.disabled) return 'ok-disabled';
                      ok.click();
                      return 'clicked';
                    }""")
                    log.info('[toutiao_publisher] %s 点确定结果: %s', browser.user_id, clicked)
                    # 等 drawer 关 + 图片插入 ProseMirror
                    await asyncio.sleep(2.5)
                except Exception as e:
                    log.warning('[toutiao_publisher] %s 点确定失败: %s', browser.user_id, e)
            except Exception as e:
                log.warning('[toutiao_publisher] %s 图片上传异常（继续发布）: %s', browser.user_id, e)

            # 兜底：如果还有 drawer 残留（极少数情况），清掉，避免拦 .save-draft / .publish-co
            try:
                removed2 = await page.evaluate("""() => {
                  const ms = document.querySelectorAll('.byte-drawer-mask, .byte-drawer-wrapper');
                  ms.forEach(el => el.remove());
                  return ms.length;
                }""")
                if removed2:
                    log.info('[toutiao_publisher] %s 兜底清掉 %d 个残留 drawer',
                             browser.user_id, removed2)
                    await asyncio.sleep(0.3)
            except Exception:
                pass

        # 点发布 / 存草稿
        mode = 'draft' if save_draft_only else 'publish'
        btn_sel = '.save-draft' if save_draft_only else '.publish-co'
        try:
            btn = await page.wait_for_selector(f'{btn_sel}:not([disabled])', timeout=8_000)
            if not btn:
                return {'ok': False, 'error': f'找不到按钮 {btn_sel}'}
            # force=True 跳过 actionability check，防 drawer 残留 mask 拦截
            await btn.click(force=True, timeout=10_000)
        except Exception as e:
            return {'ok': False, 'error': f'点击按钮失败: {e}'}

        # ── 判定成功 / 失败 ──────────────────────────────────────
        # 等服务端响应
        await asyncio.sleep(3.5)

        # 信号 1：URL 跳走（发布成功通常跳到 dashboard/列表；存草稿不跳）
        final_url = page.url
        if 'weitoutiao/publish' not in final_url:
            log.info('[toutiao_publisher] ✓ %s URL 跳转 → %s', browser.user_id, final_url)
            return {'ok': True, 'mode': mode, 'final_url': final_url}

        # 信号 2：byte-design 真 toast（不抓侧栏"消息中心"那种红点徽章）
        toast = await page.evaluate("""() => {
          const sels = [
            '.byte-message-content', '.byte-message__content',
            '.byte-notification-content', '.byte-notification__content',
            '.byte-toast', '[class*="byte-message_"]', '[class*="byte-toast_"]',
          ];
          for (const s of sels) {
            const el = document.querySelector(s);
            if (el) {
              const t = (el.innerText || '').trim();
              if (t) return t;
            }
          }
          return '';
        }""")
        if toast:
            log.info('[toutiao_publisher] %s toast=%r', browser.user_id, toast)
            if any(k in toast for k in ('成功', '已保存', '已发布', '保存', '发布')):
                return {'ok': True, 'mode': mode, 'toast': toast, 'final_url': final_url}
            if any(k in toast for k in ('失败', '错误', '出错', '请稍后', '重试', '不能为空')):
                return {'ok': False, 'error': toast}

        # 信号 3：编辑器是否被清空（发布会清空）
        editor_text = ''
        try:
            editor_text = await page.evaluate(
                'document.querySelector(".ProseMirror")?.innerText.trim() || ""'
            )
        except Exception:
            pass

        if not editor_text and mode == 'publish':
            return {'ok': True, 'mode': mode, 'note': 'editor_cleared', 'final_url': final_url}

        # 存草稿模式：编辑器不会清空。再等 2 秒看 toast
        if mode == 'draft':
            await asyncio.sleep(2)
            toast2 = await page.evaluate("""() => {
              const el = document.querySelector('.byte-message-content, .byte-message__content, [class*="byte-message_"]');
              return el ? (el.innerText||'').trim() : '';
            }""")
            if toast2:
                log.info('[toutiao_publisher] %s 二次 toast=%r', browser.user_id, toast2)
                if any(k in toast2 for k in ('成功', '已保存', '保存')):
                    return {'ok': True, 'mode': mode, 'toast': toast2}
                if any(k in toast2 for k in ('失败', '错误', '出错')):
                    return {'ok': False, 'error': toast2}
            # 还看不到明确反馈 → 保守认为成功（点了按钮 + 无 error toast）
            return {'ok': True, 'mode': mode, 'note': 'no_clear_signal'}

        log.warning('[toutiao_publisher] %s 仍在 publish 页且无 toast，editor_text=%d chars',
                    browser.user_id, len(editor_text))
        return {'ok': False, 'error': '点击发布后页面没跳转、无成功提示（看日志）'}

    try:
        return await browser._with_page_async(_do)
    except Exception as e:
        log.exception('[toutiao_publisher] %s 整体异常', browser.user_id)
        return {'ok': False, 'error': str(e)}
    finally:
        _cleanup(image_paths)


# CLI
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
    import argparse
    p = argparse.ArgumentParser(prog='python -m publisher_hub.toutiao_publisher')
    p.add_argument('user_id')
    p.add_argument('title')
    p.add_argument('content')
    p.add_argument('--draft', action='store_true', help='只存草稿不发布')
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    asyncio.run(_cli(args.user_id, args.title, args.content, args.draft))
