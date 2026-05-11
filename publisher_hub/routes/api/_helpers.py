"""API 序列化辅助。"""
from __future__ import annotations

import re

# 把 http(s)://<host>:<port> 部分去掉，只留 path
# 用途：数据库里存的图片 URL 都是 http://47.236.168.208:8899/img/...
# HTTPS 站点上加载 HTTP 图片会被浏览器拦截 (Mixed Content)
# 改成相对路径 /img/... 让 nginx 同源反代到 127.0.0.1:8899
_EXTERNAL_HOST_RE = re.compile(r'^https?://[^/]+')


def to_relative(url: str) -> str:
    if not url:
        return url
    return _EXTERNAL_HOST_RE.sub('', url)


def parse_images(image_urls: str) -> list[str]:
    return [to_relative(u.strip()) for u in (image_urls or '').split(',') if u.strip()]
