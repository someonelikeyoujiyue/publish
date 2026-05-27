"""用户什么都不填时，从 newmedia.posts 拉一条作种子（类似 cron 仿写 1 条的逻辑）。

返回值：
  (topic_text, downloaded_image_path | None)

source 选择：优先 user.video.sources（用户自定义），否则 user.xhs.sources（兜底）；
对应 posts 平台默认 [xiaohongshu, douyin] —— 这两个平台帖子图文丰富、适合做视频素材。

post 去重：复用 db.get_posts_for_user 的 LEFT JOIN，但 platform 参数传 'video'。
hub_drafts 表 platform ENUM 里没有 'video'，所以 LEFT JOIN 永远不命中、永远不排除
任何已仿写过的 hub_drafts 行——这是有意的。video 任务自带 randomness（pick_strategy=random），
让用户重复点也能拿到不同 post。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger('publisher_hub.video_gen.seed_post')


def _local_to_http(local_path: str, mysql_cfg: dict) -> str:
    """post.cover_local_path → /img/cover/... HTTP URL。同 rewrite._local_to_http。"""
    if not local_path:
        return ''
    server     = (mysql_cfg.get('image_server_url') or '').rstrip('/')
    cover_dir  = (mysql_cfg.get('cover_dir')        or '/data/newmedia/covers').rstrip('/')
    attach_dir = (mysql_cfg.get('attachment_dir')   or '/data/newmedia/attachments').rstrip('/')
    if not server:
        return ''
    if local_path.startswith(cover_dir + '/'):
        rel = local_path[len(cover_dir) + 1:]
        return f'{server}/img/cover/{rel}'
    if local_path.startswith(attach_dir + '/'):
        rel = local_path[len(attach_dir) + 1:]
        return f'{server}/img/attach/{rel}'
    return ''


def _download(url: str, dest: Path, timeout: float = 30) -> bool:
    """下载到 dest，成功返回 True。"""
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(url)
            if r.status_code != 200:
                log.warning('[seed] 下载 %s 失败: HTTP %d', url, r.status_code)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return True
    except Exception as e:
        log.warning('[seed] 下载 %s 失败: %s', url, e)
        return False


def build_topic_from_post(post: dict, max_chars: int = 500) -> str:
    """把 post 的 title + content 拼成 LLM 能用的 topic。

    优先用 translated_*（外部平台帖子已经翻译成中文）；其次原文。
    """
    title   = (post.get('translated_title')   or post.get('title')   or '').strip()
    content = (post.get('translated_content') or post.get('content') or '').strip()
    img_desc = (post.get('cover_image_desc') or '').strip()
    parts: list[str] = []
    if title:
        parts.append(title)
    if img_desc:
        parts.append(f'（配图：{img_desc}）')
    if content:
        parts.append(content[:max_chars])
    return '\n'.join(parts) or '泰国留学相关信息'


def fetch_seed_post(
    user_id: str,
    user: dict,
    db,
    config: dict,
    image_dir: Path,
) -> tuple[str, Optional[Path]]:
    """从 DB 拉 1 条原帖当种子，返回 (topic 文本, 已下载图片路径或 None)。

    没找到帖 / 帖没图 都不致命：调用方自己用 DEFAULT_TOPICS 兜底 + 默认 RSU 图。
    """
    # 优先 user.video.sources；没定义 fallback user.xhs.sources
    video_cfg = user.get('video') or {}
    sources   = video_cfg.get('sources') or (user.get('xhs') or {}).get('sources') or {}
    if not sources.get('platforms'):
        log.info('[seed] user=%s 没配 video/xhs sources，跳过', user_id)
        return '', None

    try:
        posts = db.get_posts_for_user(
            user_id=user_id,
            platform='video',           # hub_drafts.platform ENUM 里没有 'video'，LEFT JOIN 不会排除任何 post
            sources=sources,
            limit=1,
            pick_strategy='random',
        )
    except Exception as e:
        log.warning('[seed] db.get_posts_for_user 失败: %s', e)
        return '', None

    if not posts:
        log.info('[seed] user=%s 池里没有可用帖子', user_id)
        return '', None

    post = posts[0]
    log.info('[seed] user=%s 取到 post_id=%s  platform=%s  title=%r',
             user_id, post.get('id'), post.get('platform'),
             (post.get('translated_title') or post.get('title') or '')[:40])

    topic = build_topic_from_post(post)

    # 下载封面图（如果有）—— Remotion 跑在 publisher-hub 这台机，需要本地能读
    cover_path: Optional[Path] = None
    local = post.get('cover_local_path') or post.get('attachment_local_path')
    if local:
        url = _local_to_http(local, config.get('mysql') or {})
        if url:
            # 用 post.id 命名避免冲突
            dest = image_dir / f'seed_post_{post.get("id")}.jpg'
            if _download(url, dest):
                cover_path = dest
                log.info('[seed] 封面已下载 → %s', dest.name)

    return topic, cover_path
