"""用户什么都不填时，从 newmedia.posts 拉一条原帖当 LLM 种子（类似 cron 仿写 1 条）。

返回：topic 字符串（没拿到就空串，调用方自己兜底）。

**只取文字，不取原帖封面**——原帖图来自抖音/小红书，CDN 链接不稳定（防盗链、过期、签名）
而且跟"兰实大学留学"主题相关性不强。默认场景的图统一从 publisher-hub 自带的
assets/video-defaults/（RSU 真实校园照片）出，质量稳定。

source 选择：优先 user.video.sources；没定义则 fallback user.xhs.sources。
对应 posts 平台默认 [xiaohongshu, douyin] —— 图文密度高、文案贴近视频体裁。

post 去重：复用 db.get_posts_for_user 的 LEFT JOIN，platform 参数传 'video'。
hub_drafts.platform ENUM 里没有 'video'，JOIN 永远不命中、不排除任何帖。
video 任务自带 pick_strategy='random'，重复点也能拿到不同 post。
"""
from __future__ import annotations

import logging

log = logging.getLogger('publisher_hub.video_gen.seed_post')


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


def fetch_seed_topic(user_id: str, user: dict, db) -> str:
    """从 DB 拉 1 条原帖当种子，返回 topic 文本（空 = 没拿到，调用方兜底）。"""
    # 优先 user.video.sources；没定义 fallback user.xhs.sources
    video_cfg = user.get('video') or {}
    sources   = video_cfg.get('sources') or (user.get('xhs') or {}).get('sources') or {}
    if not sources.get('platforms'):
        log.info('[seed] user=%s 没配 video/xhs sources，跳过', user_id)
        return ''

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
        return ''

    if not posts:
        log.info('[seed] user=%s 池里没有可用帖子', user_id)
        return ''

    post = posts[0]
    log.info('[seed] user=%s 取到 post_id=%s  platform=%s  title=%r',
             user_id, post.get('id'), post.get('platform'),
             (post.get('translated_title') or post.get('title') or '')[:40])

    return build_topic_from_post(post)
