"""管理端 JSON API。"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ... import scheduler as sch

log = logging.getLogger('publisher_hub.api.admin')
router = APIRouter()


class RunNowBody(BaseModel):
    user_id: str | None = None


@router.post('/admin/run-now')
def run_now(body: RunNowBody, request: Request):
    target = (body.user_id or '').strip() or None
    log.info('[api/admin] 手动触发 daily_run target_user=%s', target or '(全部)')
    threading.Thread(
        target=sch.daily_run,
        args=(request.app, target),
        daemon=True,
    ).start()
    return {'ok': True, 'target': target}


@router.get('/admin/scheduler-status')
def scheduler_status():
    s = sch._scheduler
    if not s or not s.running:
        return {'running': False}
    job = s.get_job('daily_run')
    return {
        'running': True,
        'job_id':  job.id,
        'cron':    str(job.trigger),
        'next':    str(job.next_run_time) if job.next_run_time else None,
    }
