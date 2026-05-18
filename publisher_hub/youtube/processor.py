"""publisher-hub 集成入口：把 pipeline.process_youtube 包成后台任务 + DB 更新。"""
from __future__ import annotations

import logging
import threading
import traceback
import zlib
from pathlib import Path

from ..db import Database
from .pipeline import extract_video_id, process_youtube

log = logging.getLogger('publisher_hub.youtube.processor')


def _hash_video_id(video_id: str) -> int:
    """把 YouTube 11 字符 video_id 映射成 int31（hub_drafts.source_post_id NOT NULL）。

    CRC32 截 31 位防溢出。同一视频 hash 稳定（重复提交会覆盖原草稿）。
    """
    return zlib.crc32(video_id.encode()) & 0x7FFFFFFF


def submit(
    db: Database,
    user_id: str,
    url: str,
    *,
    strip_hardsub: bool = True,
    blur_qr: bool = False,
) -> int:
    """把 youtube URL 入队为 hub_drafts 草稿（status=processing），异步触发处理。

    Returns: draft_id
    """
    video_id = extract_video_id(url)
    draft_id = db.save_draft(
        user_id=user_id,
        platform='youtube',
        source_post_id=_hash_video_id(video_id),
        title=f'(处理中) {video_id}',  # 处理完后 title 改为视频实际标题
        content='',
        source_url=url,
        status='processing',
    )
    log.info('[youtube] submit user=%s url=%s draft_id=%d', user_id, url, draft_id)

    # 后台线程跑（30s-10min），不阻塞 HTTP
    threading.Thread(
        target=_run_process,
        args=(db, draft_id, user_id, video_id, url, strip_hardsub, blur_qr),
        daemon=True,
    ).start()
    return draft_id


def _run_process(
    db: Database,
    draft_id: int,
    user_id: str,
    video_id: str,
    url: str,
    strip_hardsub: bool,
    blur_qr: bool,
):
    try:
        result = process_youtube(url, strip_hardsub=strip_hardsub, blur_qr=blur_qr)
    except Exception as e:
        log.exception('[youtube] draft=%d 处理失败', draft_id)
        db.set_draft_status(draft_id, 'failed', error_msg=str(e)[:500])
        return

    # 把 final_video 路径转成 nginx /videos/ 可访问的相对 URL
    # data/youtube/<video_id>/video_final.mp4 → /videos/<video_id>/video_final.mp4
    final_path = Path(result['outputs']['final_video'])
    # 找到 data/youtube/ 之后的相对部分
    parts = final_path.parts
    if 'youtube' in parts:
        idx = parts.index('youtube')
        rel = '/'.join(parts[idx + 1:])  # <video_id>/video_final.mp4
    else:
        rel = final_path.name
    video_url = f'/videos/{rel}'

    # title 拿 YouTube 视频原标题（这里没拿，用 source_language + segments 凑数；
    # 后续可以让 pipeline 也返回 title，暂时用 video_id 占位）
    new_title = f'YouTube · {video_id}'

    # 把 content 设为双语 SRT 内容（前端可展示文字+下载链接）
    bilingual = result['outputs'].get('bilingual_srt')
    content_text = ''
    if bilingual and Path(bilingual).exists():
        content_text = Path(bilingual).read_text(encoding='utf-8', errors='replace')

    # 走 save_draft 的 ON DUPLICATE KEY 更新（同 source_post_id 复用同行）
    db.save_draft(
        user_id=user_id,
        platform='youtube',
        source_post_id=_hash_video_id(video_id),
        title=new_title,
        content=content_text,
        source_url=url,
        status='pushed',
    )
    # video_url 写到 pushed_result（复用现有字段，TEXT 可放短 URL）
    db.mark_pushed(draft_id, pushed_result=video_url)
    log.info('[youtube] ✓ draft=%d → %s', draft_id, video_url)
