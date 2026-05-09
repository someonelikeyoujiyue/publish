"""管理端点（无登录，仅做基本防护）。

提供：
- POST /admin/run-now —— 立即触发一次 daily_run（仿写+推送+通知）
                          可选 user_id=xxx 只跑一个用户
- GET  /admin/scheduler-status —— 看下次 cron 触发时间
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from .. import scheduler as sch

log = logging.getLogger('publisher_hub.routes.admin')
router = APIRouter()


@router.post('/admin/run-now', response_class=HTMLResponse)
def run_now(request: Request, user_id: str = Form('')):
    """后台线程触发 daily_run（避免 HTTP 长连接超时）。"""
    target = user_id.strip() or None
    log.info('[admin] 手动触发 daily_run target_user=%s', target or '(全部)')
    threading.Thread(
        target=sch.daily_run,
        args=(request.app, target),
        daemon=True,
    ).start()
    return HTMLResponse(
        '<span class="badge badge-pushed" style="padding:8px 12px;">'
        f'✓ 已在后台触发{"（用户 " + target + "）" if target else ""}，'
        '约 1-3 分钟/条；进度看 journalctl -u publisher-hub -f</span>'
    )


@router.get('/admin/scheduler-status', response_class=PlainTextResponse)
def scheduler_status():
    s = sch._scheduler
    if not s or not s.running:
        return PlainTextResponse('scheduler 未启动', status_code=503)
    job = s.get_job('daily_run')
    return PlainTextResponse(
        f'scheduler running\n'
        f'job_id: {job.id}\n'
        f'cron:   {job.trigger}\n'
        f'next:   {job.next_run_time}\n',
    )
