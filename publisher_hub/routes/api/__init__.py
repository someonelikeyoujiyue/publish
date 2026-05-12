"""JSON API 路由（/api/*）—— React 前端调用。

老的 HTML 路由（publisher_hub.routes.{home,users,wechat,xhs,toutiao,admin}）
保留不动，作为过渡 + 兜底降级路径。
"""
from fastapi import APIRouter

from . import auth, users, wechat, xhs, toutiao, toutiao_drafts, douyin_drafts, admin

api_router = APIRouter(prefix='/api')
api_router.include_router(auth.router,           tags=['auth'])
api_router.include_router(users.router,          tags=['users'])
api_router.include_router(wechat.router,         tags=['wechat'])
api_router.include_router(xhs.router,            tags=['xhs'])
api_router.include_router(toutiao.router,        tags=['toutiao'])
api_router.include_router(toutiao_drafts.router, tags=['toutiao-drafts'])
api_router.include_router(douyin_drafts.router,  tags=['douyin-drafts'])
api_router.include_router(admin.router,          tags=['admin'])
