"""小红书草稿 JSON API。"""
from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from pathlib import Path

from fastapi import (
    APIRouter, Depends, File, HTTPException, Request, UploadFile,
)
from pydantic import BaseModel

from ...config import get_user
from ...feishu import FeishuBot
from ...image_gen import ImageGenerator
from ...rewrite import RewriteEngine
from ...xhs import XhsPublisher
from ._helpers import parse_images
from .auth import require_admin, require_editor

log = logging.getLogger('publisher_hub.api.xhs')
router = APIRouter()
PLATFORM = 'xhs'


def _draft_summary(d: dict) -> dict:
    images = parse_images(d.get('image_urls') or '')
    return {
        'id':         d['id'],
        'title':      d.get('title') or '',
        'status':     d.get('status'),
        'created_at': str(d.get('created_at') or ''),
        'pushed_at':  str(d.get('pushed_at') or '') if d.get('pushed_at') else None,
        'error':      d.get('error_msg') or None,
        'cover':      images[0] if images else '',
        'image_count': len(images),
    }


@router.get('/users/{user_id}/xhs/drafts')
def list_drafts(user_id: str, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    drafts = db.list_drafts(user_id, platform=PLATFORM, status=None, limit=100)
    return {'drafts': [_draft_summary(d) for d in drafts]}


@router.get('/users/{user_id}/xhs/drafts/{draft_id}')
def get_draft(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    images = parse_images(draft.get('image_urls') or '')
    qr_url = ''
    if draft.get('status') == 'pushed' and draft.get('pushed_result'):
        try:
            qr_url = (json.loads(draft['pushed_result']) or {}).get('qr_url') or ''
        except Exception:
            pass
    return {
        'id':         draft['id'],
        'title':      draft.get('title') or '',
        'content':    draft.get('content') or '',
        'images':     images,
        'status':     draft.get('status'),
        'created_at': str(draft.get('created_at') or ''),
        'pushed_at':  str(draft.get('pushed_at') or '') if draft.get('pushed_at') else None,
        'qr_url':     qr_url,
        'error':      draft.get('error_msg') or None,
    }


@router.post('/users/{user_id}/xhs/refresh')
def refresh(user_id: str, request: Request, _=Depends(require_editor)):
    config  = request.app.state.config
    prompts = request.app.state.prompts
    db      = request.app.state.db
    if not get_user(config, user_id):
        raise HTTPException(404)
    try:
        engine = RewriteEngine(config, prompts)
        n = engine.run_user(user_id, PLATFORM, db)
        return {'ok': True, 'new_count': n}
    except Exception as e:
        log.error('[api/xhs] refresh %s 异常: %s', user_id, e, exc_info=True)
        return {'ok': False, 'error': str(e)}


@router.post('/users/{user_id}/xhs/drafts/{draft_id}/push')
def push(user_id: str, draft_id: int, request: Request):
    config = request.app.state.config
    db     = request.app.state.db
    user   = get_user(config, user_id)
    if not user:
        raise HTTPException(404)
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    publisher = XhsPublisher(config)
    images    = [u.strip() for u in (draft.get('image_urls') or '').split(',') if u.strip()]
    result = publisher.push(
        title   = draft.get('title') or '',
        content = draft.get('content') or '',
        images  = images,
    )
    bot       = FeishuBot(config)
    user_name = user.get('name') or user_id
    title     = draft.get('title') or '(无标题)'

    if result['ok']:
        qr = result.get('qr_url') or ''
        db.mark_pushed(
            draft_id,
            pushed_result=json.dumps({'qr_url': qr}, ensure_ascii=False),
        )
        bot.push_success(user_name, PLATFORM, title,
                         f'扫码：{qr}' if qr else '已自动发布（无需扫码）')
        return {'ok': True, 'qr_url': qr}

    err = result.get('error', '未知错误')
    db.mark_failed(draft_id, error_msg=err)
    bot.push_failed(user_name, PLATFORM, title, err)
    return {'ok': False, 'error': err}


# ── 编辑 / 删除 / 重生 ────────────────────────────────────────────────────────

class _UpdateBody(BaseModel):
    title:   str | None = None
    content: str | None = None


@router.patch('/users/{user_id}/xhs/drafts/{draft_id}')
def update_draft(
    user_id: str, draft_id: int, body: _UpdateBody, request: Request,
    _=Depends(require_editor),
):
    """编辑 xhs 草稿的标题 / 正文。"""
    db = request.app.state.db
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    fields: dict = {}
    if body.title is not None:
        fields['title'] = body.title.strip()
    if body.content is not None:
        fields['content'] = body.content
    if not fields:
        return {'ok': True, 'no_change': True}
    db.update_draft_fields(draft_id, **fields)
    log.info('[xhs] draft=%d edited by user=%s fields=%s', draft_id, user_id, list(fields))
    return {'ok': True}


@router.delete('/users/{user_id}/xhs/drafts/{draft_id}', status_code=200)
def delete_draft(
    user_id: str, draft_id: int, request: Request, _=Depends(require_admin),
):
    """删除 xhs 草稿（admin only）+ 清理 draft-uploads/<id>/ 目录。"""
    db = request.app.state.db
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    # 清理用户上传过的图（如果有）
    upload_dir = _draft_upload_dir(draft_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)

    rows = db.delete_draft(draft_id)
    log.info('[xhs] draft=%d deleted (rows=%d)', draft_id, rows)
    return {'ok': True, 'draft_id': draft_id, 'rows_deleted': rows}


# ── 重生 narration（用原 source post + xhs_note 模板再跑一遍 LLM）─────────────

@router.post('/users/{user_id}/xhs/drafts/{draft_id}/regenerate-narration')
def regenerate_narration(
    user_id: str, draft_id: int, request: Request, _=Depends(require_editor),
):
    """拿原帖 + xhs_note 模板再跑一遍 LLM，覆盖 title / content。"""
    config  = request.app.state.config
    prompts = request.app.state.prompts
    db      = request.app.state.db

    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    src_post_id = draft.get('source_post_id')
    post = db.get_post(int(src_post_id)) if src_post_id else None
    if not post:
        raise HTTPException(400, '找不到原帖，无法重生 narration')

    tmpl = (prompts or {}).get('xhs_note')
    if not tmpl:
        raise HTTPException(500, 'prompts.yaml 缺 xhs_note 模板')

    title   = (post.get('translated_title')   or post.get('title')   or '').strip()
    content = (post.get('translated_content') or post.get('content') or '').strip()
    try:
        prompt = tmpl.format(title=title, content=content[:4000])
    except KeyError as e:
        raise HTTPException(500, f'xhs_note 模板缺占位符: {e}')

    engine = RewriteEngine(config, prompts)
    text = engine._call_llm(prompt)
    if not text:
        raise HTTPException(502, 'LLM 返回空')

    new_title, new_content = RewriteEngine._parse_response(text, 'xhs')
    if not new_content:
        raise HTTPException(502, 'LLM 输出解析失败')

    db.update_draft_fields(
        draft_id,
        title=new_title or draft.get('title') or '',
        content=new_content,
    )
    log.info('[xhs] draft=%d narration 重生完成 → %r', draft_id, (new_title or '')[:30])
    return {'ok': True, 'title': new_title, 'content': new_content}


# ── 图片：删 / 上传 / 重生 ──────────────────────────────────────────────────

def _draft_upload_dir(draft_id: int) -> Path:
    """publisher-hub/data/draft-uploads/<draft_id>/"""
    return Path(__file__).resolve().parents[3] / 'data' / 'draft-uploads' / str(draft_id)


_SAFE_FN_RE = re.compile(r'[^A-Za-z0-9._-]+')

def _safe_filename(name: str) -> str:
    name = name.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    return _SAFE_FN_RE.sub('_', name)[:80] or 'upload'


def _split_images(s: str) -> list[str]:
    return [u.strip() for u in (s or '').split(',') if u.strip()]


def _join_images(items: list[str]) -> str:
    return ','.join(items)


@router.delete('/users/{user_id}/xhs/drafts/{draft_id}/images/{index}')
def delete_image(
    user_id: str, draft_id: int, index: int, request: Request,
    _=Depends(require_editor),
):
    """从 image_urls 切出第 index 张图。

    如果是用户上传的图（URL 在 /draft-uploads/ 路径下），同时把磁盘文件删了。
    """
    db = request.app.state.db
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    imgs = _split_images(draft.get('image_urls') or '')
    if index < 0 or index >= len(imgs):
        raise HTTPException(400, f'index 越界 (有 {len(imgs)} 张)')
    removed_url = imgs.pop(index)
    db.update_draft_fields(draft_id, image_urls=_join_images(imgs))

    # 如果是 /draft-uploads/<id>/<file> 的本地文件，物理删
    m = re.search(r'/draft-uploads/(\d+)/([^?#]+)$', removed_url)
    if m and int(m.group(1)) == draft_id:
        fp = _draft_upload_dir(draft_id) / m.group(2)
        if fp.exists():
            try: fp.unlink()
            except Exception as e: log.warning('[xhs] 删上传图失败 %s: %s', fp, e)

    log.info('[xhs] draft=%d removed image[%d]=%s', draft_id, index, removed_url[:60])
    return {'ok': True, 'remaining': len(imgs), 'removed_url': removed_url}


@router.post('/users/{user_id}/xhs/drafts/{draft_id}/images')
async def upload_images(
    user_id: str, draft_id: int, request: Request,
    images: list[UploadFile] = File(...),
    _=Depends(require_editor),
):
    """上传一张或多张图，追加到 image_urls 末尾。"""
    db = request.app.state.db
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)

    save_dir = _draft_upload_dir(draft_id)
    save_dir.mkdir(parents=True, exist_ok=True)

    new_urls: list[str] = []
    for uf in images:
        if not uf or not uf.filename:
            continue
        ct = (uf.content_type or '').lower()
        if not ct.startswith('image/'):
            log.warning('[xhs] 跳过非图: %s (%s)', uf.filename, ct)
            continue
        # 用 uuid 前缀避免重名冲突
        fn = f'{uuid.uuid4().hex[:8]}_{_safe_filename(uf.filename)}'
        dest = save_dir / fn
        with open(dest, 'wb') as f:
            while True:
                chunk = await uf.read(1024 * 1024)
                if not chunk: break
                f.write(chunk)
        new_urls.append(f'/draft-uploads/{draft_id}/{fn}')

    if not new_urls:
        raise HTTPException(400, '没有有效图片')

    existing = _split_images(draft.get('image_urls') or '')
    updated = existing + new_urls
    db.update_draft_fields(draft_id, image_urls=_join_images(updated))
    log.info('[xhs] draft=%d 上传 %d 张 → 总共 %d 张', draft_id, len(new_urls), len(updated))
    return {'ok': True, 'added': new_urls, 'total': len(updated)}


class _RegenImgBody(BaseModel):
    prompt: str | None = None


@router.post('/users/{user_id}/xhs/drafts/{draft_id}/images/{index}/regenerate')
def regenerate_image(
    user_id: str, draft_id: int, index: int, body: _RegenImgBody,
    request: Request, _=Depends(require_editor),
):
    """调 wan2.7 生新图，替换第 index 张。

    prompt 没传就从 draft.title + 第一段正文派生。
    """
    config = request.app.state.config
    db     = request.app.state.db
    draft = db.get_draft(draft_id)
    if not draft or draft['user_id'] != user_id or draft['platform'] != PLATFORM:
        raise HTTPException(404)
    imgs = _split_images(draft.get('image_urls') or '')
    if index < 0 or index >= len(imgs):
        raise HTTPException(400, f'index 越界 (有 {len(imgs)} 张)')

    # 派生 prompt
    prompt = (body.prompt or '').strip()
    if not prompt:
        title = (draft.get('title') or '').strip()
        content = (draft.get('content') or '').strip()
        # 取前 80 字内容做提示词种子
        snippet = content.split('\n')[0][:80] if content else ''
        prompt = (title + ' ' + snippet).strip() or '泰国兰实大学校园场景，真实摄影，自然光'

    ig = ImageGenerator(config)
    if not ig.enabled:
        raise HTTPException(503, 'image_gen 未启用 / api_key 未配置')

    url = ig.generate_processed(prompt, draft_id=f'{draft_id}_{index}_{uuid.uuid4().hex[:6]}')
    if not url:
        raise HTTPException(502, 'wan2.7 生图失败')

    imgs[index] = url
    db.update_draft_fields(draft_id, image_urls=_join_images(imgs))
    log.info('[xhs] draft=%d image[%d] 重生 → %s', draft_id, index, url)
    return {'ok': True, 'index': index, 'url': url, 'prompt': prompt}
