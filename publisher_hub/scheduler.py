"""定时调度。

策略（用户拍板）：
- 全部用户公用一个 cron `0 8 * * *`（每天 8:00 触发）
- 触发时遍历每个用户：
    1. wechat 仿写 N 条（rewrite_batch / articles_per_batch）
    2. wechat 把所有 status='ready' 草稿推到公众号草稿箱（运营进后台手动群发）
    3. xhs 仿写 N 条
    4. xhs 把所有 status='ready' 草稿调 myaibot 生成二维码（飞书通知扫码）
    5. 推送结果统一通过飞书 webhook 通知

用 APScheduler BackgroundScheduler 后台线程跑，不阻塞 FastAPI 主 event loop。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_user, list_users
from .feishu import FeishuBot
from .rewrite import RewriteEngine
from .wechat import WeChatPublisher
from .xhs import XhsPublisher

log = logging.getLogger('publisher_hub.scheduler')

# 防重入：daily_run 时间长（10-30 分钟），避免上一轮还在跑下一轮就开始
_run_lock = threading.Lock()


# ── 单用户单平台的完整流程（仿写 → 推送 → 通知）─────────────────────────────

def _process_user_wechat(user: dict, app):
    config  = app.state.config
    prompts = app.state.prompts
    db      = app.state.db
    uid     = user['id']
    name    = user.get('name') or uid

    engine = RewriteEngine(config, prompts)
    bot    = FeishuBot(config)

    log.info('[cron] ▶ %s/wechat 仿写...', uid)
    try:
        n = engine.run_user(uid, 'wechat', db)
    except Exception as e:
        log.exception('[cron] %s/wechat 仿写异常: %s', uid, e)
        bot.push_failed(name, 'wechat', '(仿写阶段)', str(e))
        return

    log.info('[cron] %s/wechat 新增草稿 %d 条', uid, n)

    pcfg = user.get('wechat') or {}
    if not pcfg.get('app_id') or not pcfg.get('app_secret'):
        log.warning('[cron] %s/wechat app_id/secret 未配置，跳过推送', uid)
        return

    publisher = WeChatPublisher(pcfg)
    drafts = db.list_drafts(uid, platform='wechat', status='ready', limit=20)
    log.info('[cron] %s/wechat 待推送 %d 条', uid, len(drafts))

    pushed_ok = 0
    for d in drafts:
        try:
            result = publisher.push(d)
        except Exception as e:
            log.exception('[cron] %s/wechat draft=%s push 异常', uid, d['id'])
            db.mark_failed(d['id'], error_msg=str(e))
            bot.push_failed(name, 'wechat', d.get('title') or '', str(e))
            continue

        if result.get('ok'):
            mid = result.get('media_id', '')
            db.mark_pushed(d['id'], pushed_result=f'media_id={mid}')
            bot.push_success(name, 'wechat', d.get('title') or '',
                             f'media_id: `{mid}`  → 进 mp.weixin.qq.com 草稿箱手动群发')
            pushed_ok += 1
        else:
            err = result.get('error', '未知错误')
            db.mark_failed(d['id'], error_msg=err)
            bot.push_failed(name, 'wechat', d.get('title') or '', err)

    log.info('[cron] ✓ %s/wechat 推送成功 %d/%d', uid, pushed_ok, len(drafts))


def _process_user_xhs(user: dict, app):
    config  = app.state.config
    prompts = app.state.prompts
    db      = app.state.db
    uid     = user['id']
    name    = user.get('name') or uid

    engine = RewriteEngine(config, prompts)
    bot    = FeishuBot(config)

    log.info('[cron] ▶ %s/xhs 仿写...', uid)
    try:
        n = engine.run_user(uid, 'xhs', db)
    except Exception as e:
        log.exception('[cron] %s/xhs 仿写异常: %s', uid, e)
        bot.push_failed(name, 'xhs', '(仿写阶段)', str(e))
        return

    log.info('[cron] %s/xhs 新增草稿 %d 条', uid, n)

    publisher = XhsPublisher(config)
    drafts = db.list_drafts(uid, platform='xhs', status='ready', limit=20)
    log.info('[cron] %s/xhs 待生成二维码 %d 条', uid, len(drafts))

    pushed_ok = 0
    for d in drafts:
        images = [u.strip() for u in (d.get('image_urls') or '').split(',') if u.strip()]
        try:
            result = publisher.push(
                title=d.get('title') or '',
                content=d.get('content') or '',
                images=images,
            )
        except Exception as e:
            log.exception('[cron] %s/xhs draft=%s push 异常', uid, d['id'])
            db.mark_failed(d['id'], error_msg=str(e))
            bot.push_failed(name, 'xhs', d.get('title') or '', str(e))
            continue

        if result.get('ok'):
            qr = result.get('qr_url') or ''
            db.mark_pushed(
                d['id'],
                pushed_result=json.dumps({'qr_url': qr}, ensure_ascii=False),
            )
            extra = f'扫码：{qr[:80]}…' if qr else '已自动发布（无二维码）'
            bot.push_success(name, 'xhs', d.get('title') or '', extra)
            pushed_ok += 1
        else:
            err = result.get('error', '未知错误')
            db.mark_failed(d['id'], error_msg=err)
            bot.push_failed(name, 'xhs', d.get('title') or '', err)

    log.info('[cron] ✓ %s/xhs 推送成功 %d/%d', uid, pushed_ok, len(drafts))


# ── 主入口：每天 8:00 跑一次 ──────────────────────────────────────────────

def daily_run(app, target_user_id: Optional[str] = None):
    """每天 8:00 触发的完整流程。

    Args:
        target_user_id: 仅跑某用户（用于 admin "立即触发" 按钮）；
                        默认 None 跑所有用户。
    """
    if not _run_lock.acquire(blocking=False):
        log.warning('[cron] 上一轮还在跑，跳过本次触发')
        return
    try:
        config = app.state.config
        if target_user_id:
            user = get_user(config, target_user_id)
            users = [user] if user else []
        else:
            users = list_users(config)

        log.info('[cron] ===== daily_run 开始 (%d 个用户) =====', len(users))
        for user in users:
            uid = user['id']
            try:
                _process_user_wechat(user, app)
            except Exception as e:
                log.exception('[cron] %s wechat 流程整体异常: %s', uid, e)

            try:
                _process_user_xhs(user, app)
            except Exception as e:
                log.exception('[cron] %s xhs 流程整体异常: %s', uid, e)

        log.info('[cron] ===== daily_run 结束 =====')
    finally:
        _run_lock.release()


# ── 启停 ────────────────────────────────────────────────────────────────

_scheduler: Optional[BackgroundScheduler] = None


def start_scheduler(app, cron_expr: str = '0 8 * * *') -> BackgroundScheduler:
    """启动 BackgroundScheduler，注册 daily_run cron。"""
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
    _scheduler.add_job(
        daily_run,
        trigger=CronTrigger.from_crontab(cron_expr, timezone='Asia/Shanghai'),
        args=[app],
        id='daily_run',
        replace_existing=True,
        misfire_grace_time=3600,         # 服务重启时若错过几小时内的触发仍补跑一次
    )
    _scheduler.start()
    log.info('[scheduler] 已启动，cron=%s（Asia/Shanghai）下次触发: %s',
             cron_expr, _scheduler.get_job('daily_run').next_run_time)
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info('[scheduler] 已停止')
        _scheduler = None
