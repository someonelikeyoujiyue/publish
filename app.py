"""FastAPI 入口。

启动：
    uv run uvicorn app:app --host 0.0.0.0 --port 8900 --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from publisher_hub.config import load_config, load_prompts
from publisher_hub.db import Database
from publisher_hub.routes import home, users, wechat, xhs

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('publisher_hub.app')


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info('=== Publisher Hub 启动 ===')
    config = load_config()
    prompts = load_prompts()
    db = Database(config)
    db.connect()

    app.state.config  = config
    app.state.prompts = prompts
    app.state.db      = db
    log.info('config / prompts / db 已就绪')

    yield

    db.close()
    log.info('=== Publisher Hub 已停止 ===')


app = FastAPI(title='Publisher Hub', lifespan=lifespan)

app.include_router(users.router)        # 必须先于 home.router（/{user_id} 通配会吞 /users）
app.include_router(home.router)
app.include_router(wechat.router)
app.include_router(xhs.router)


@app.get('/healthz', response_class=HTMLResponse)
def healthz():
    return 'ok'
