"""仿写引擎。

数据流：
    newmedia.posts → 按 user.sources 过滤 → LLM 仿写 → hub_drafts

模式：
    per_post — 1 条原帖 → 1 篇笔记/文章
    batch    — N 条素材综合 → articles_per_batch 篇

CLI:
    python -m publisher_hub.rewrite <user_id> [<platform>]
    python -m publisher_hub.rewrite alice            # 跑 alice 的 wechat + xhs
    python -m publisher_hub.rewrite alice wechat     # 只跑 wechat
"""
from __future__ import annotations

import argparse
import json
import logging
import re

import httpx

from .config import get_user, load_config, load_prompts
from .db import Database
from .image_gen import ImageGenerator
from .rsu import DEFAULT_BASE_URL as RSU_DEFAULT_URL, pick_random as rsu_pick_random

log = logging.getLogger('publisher_hub.rewrite')


class RewriteEngine:
    def __init__(self, config: dict, prompts: dict):
        self.config  = config
        self.prompts = prompts
        llm = config['llm']
        self.api_key    = llm['api_key']
        self.base_url   = llm['base_url'].rstrip('/')
        self.model      = llm['model']
        self.max_tokens = int(llm.get('max_tokens', 65536))
        self.timeout    = float(llm.get('timeout_seconds', 300))

        # 图片：服务器本地路径 → HTTP URL（newmedia 已下载并挂载）
        mysql_cfg = config.get('mysql', {})
        self.img_server   = (mysql_cfg.get('image_server_url') or '').rstrip('/')
        self.cover_dir    = (mysql_cfg.get('cover_dir')      or '/data/newmedia/covers').rstrip('/')
        self.attach_dir   = (mysql_cfg.get('attachment_dir') or '/data/newmedia/attachments').rstrip('/')

        # RSU 素材兜底（wechat 原帖图不足时随机补）
        assets_cfg = config.get('assets', {})
        self.rsu_base_url = (assets_cfg.get('rsu_base_url') or RSU_DEFAULT_URL).rstrip('/')
        self.min_images   = int(assets_cfg.get('min_images', 4))

        # xhs 配图：原帖图 + wan2.7 生图
        self.image_gen = ImageGenerator(config)

    # ── 主入口 ────────────────────────────────────────────────────────

    def run_user(self, user_id: str, platform: str, db: Database) -> int:
        """对某用户某平台执行一次仿写。返回新增草稿数。"""
        user = get_user(self.config, user_id)
        if not user:
            log.error('用户 %s 不存在于 config.yaml', user_id)
            return 0
        pcfg = user.get(platform) or {}
        if not pcfg:
            log.warning('[%s] 未配置平台 %s，跳过', user_id, platform)
            return 0

        sources    = pcfg.get('sources') or {}
        prompt_key = pcfg.get('prompt') or ''
        mode       = pcfg.get('rewrite_mode', 'per_post')
        batch      = int(pcfg.get('rewrite_batch', 5))
        n_articles = int(pcfg.get('articles_per_batch', 1))
        pick       = pcfg.get('pick_strategy', 'latest')
        recent_pool = int(pcfg.get('recent_pool', 50))
        candidate_pool = int(pcfg.get('candidate_pool', 0))   # >0 时启用"识图 + LLM 5 选 N"

        tmpl = self.prompts.get(prompt_key)
        if not tmpl:
            log.error('prompts.yaml 未定义模板: %s', prompt_key)
            return 0

        # xhs 专属流程：每篇仿写各自抽 candidate_pool 个候选 → 识图 → LLM 选 2 张图作配图
        if platform == 'xhs' and candidate_pool > 0:
            return self._per_post_xhs(
                user_id, db, batch, candidate_pool, sources,
                pick, recent_pool, tmpl,
            )

        posts = db.get_posts_for_user(
            user_id, platform, sources, limit=batch,
            pick_strategy=pick, recent_pool=recent_pool,
        )
        if not posts:
            log.info('[rewrite] %s/%s 无可仿写原帖', user_id, platform)
            return 0

        log.info(
            '[rewrite] %s/%s 模式=%s 入选=%d 模板=%s',
            user_id, platform, mode, len(posts), prompt_key,
        )

        if mode == 'batch':
            return self._batch(posts, user_id, platform, tmpl, n_articles, db)
        return self._per_post(posts, user_id, platform, tmpl, db)

    # ── 逐条模式 ──────────────────────────────────────────────────────

    def _per_post(self, posts, user_id, platform, tmpl, db) -> int:
        count = 0
        for post in posts:
            title   = (post.get('translated_title')   or post.get('title')   or '').strip()
            content = (post.get('translated_content') or post.get('content') or '').strip()
            if not (title or content):
                continue

            try:
                prompt = tmpl.format(title=title, content=content[:4000])
            except KeyError as e:
                log.warning('[rewrite] per_post 模板缺少占位符: %s', e)
                continue

            text = self._call_llm(prompt)
            if not text:
                continue

            new_title, new_content = self._parse_response(text, platform)
            if not new_content:
                continue

            post_imgs = [u for u in self._collect_images(post).split(',') if u.strip()]
            final_imgs = self.collect_images_for_draft(
                post_imgs, category=post.get('category', ''),
            )
            # 头条号微头条只配 1 张图（多了上传慢、用户体验也不好）
            if platform == 'toutiao':
                final_imgs = final_imgs[:1]
            db.save_draft(
                user_id        = user_id,
                platform       = platform,
                source_post_id = post['id'],
                title          = new_title,
                content        = new_content,
                image_urls     = ','.join(final_imgs),
            )
            log.info('[rewrite] ✓ %s/%s post_id=%s → %s',
                     user_id, platform, post['id'], (new_title or '')[:30])
            count += 1
        return count

    # ── 批量综合模式 ──────────────────────────────────────────────────

    def _batch(self, posts, user_id, platform, tmpl, n: int, db) -> int:
        if n <= 0:
            n = 1
        chunk_size = max(1, len(posts) // n)
        chunks: list[list[dict]] = []
        for i in range(n):
            start = i * chunk_size
            end   = (i + 1) * chunk_size if i < n - 1 else len(posts)
            if start < len(posts):
                chunks.append(posts[start:end])

        count = 0
        for i, chunk in enumerate(chunks):
            parts = []
            for j, p in enumerate(chunk, 1):
                t = (p.get('translated_title')   or p.get('title')   or '').strip()
                c = (p.get('translated_content') or p.get('content') or '').strip()
                if not (t or c):
                    continue
                header = f'---来源 {j}'
                if t:
                    header += f'({t})'
                header += '---'
                parts.append(f'{header}\n{c[:2000]}')

            if not parts:
                continue

            posts_text = '\n\n'.join(parts)
            try:
                prompt = tmpl.format(
                    posts         = posts_text,
                    post_count    = len(parts),
                    article_index = i + 1,
                )
            except KeyError as e:
                log.warning('[rewrite] batch 模板缺少占位符: %s', e)
                continue

            text = self._call_llm(prompt)
            if not text:
                continue

            new_title, new_content = self._parse_response(text, platform)
            if not new_content:
                continue

            # 收集本批所有图片，再用 RSU 兜底补足
            all_imgs: list[str] = []
            for p in chunk:
                for u in self._collect_images(p).split(','):
                    u = u.strip()
                    if u and u not in all_imgs:
                        all_imgs.append(u)
            final_imgs = self.collect_images_for_draft(
                all_imgs, category=chunk[0].get('category', ''),
            )

            db.save_draft(
                user_id         = user_id,
                platform        = platform,
                source_post_id  = chunk[0]['id'],
                source_post_ids = ','.join(str(p['id']) for p in chunk),
                title           = new_title,
                content         = new_content,
                image_urls      = ','.join(final_imgs),
            )
            log.info('[rewrite] ✓ %s/%s batch[%d/%d] → %s (素材%d条)',
                     user_id, platform, i + 1, len(chunks),
                     (new_title or '')[:30], len(chunk))
            count += 1
        return count

    # ── LLM 调用 ──────────────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str | None:
        """流式调用 LLM。

        valueclue 代理对非流式请求有 ~16s 硬超时，reasoning 模型来不及完成；
        改用 stream=True 后每个 chunk 重置 idle 计时，可正常完成 60-180s 推理。

        deepseek 系列响应里 delta 可能含 reasoning_content（推理过程）和 content（最终文本），
        我们只拼接 content。
        """
        body = {
            'model':      self.model,
            'messages':   [{'role': 'user', 'content': prompt}],
            'max_tokens': self.max_tokens,
            'stream':     True,
        }
        if 'reasoner' not in self.model:
            body['temperature'] = 0.8

        content_parts: list[str] = []
        reasoning_chars = 0

        try:
            with httpx.Client(timeout=self.timeout) as c:
                with c.stream(
                    'POST',
                    f'{self.base_url}/chat/completions',
                    headers={
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type':  'application/json',
                    },
                    json=body,
                ) as resp:
                    if resp.status_code >= 400:
                        body_text = b''.join(resp.iter_bytes()).decode('utf-8', errors='replace')
                        log.warning('[llm] HTTP %d: %s', resp.status_code, body_text[:500])
                        return None

                    for line in resp.iter_lines():
                        if not line:
                            continue
                        line = line.strip()
                        if line.startswith('data:'):
                            line = line[5:].strip()
                        if line == '[DONE]':
                            break
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get('choices') or []
                        if not choices:
                            continue
                        delta = choices[0].get('delta') or {}
                        c_text = delta.get('content') or ''
                        if c_text:
                            content_parts.append(c_text)
                        r_text = delta.get('reasoning_content') or ''
                        if r_text:
                            reasoning_chars += len(r_text)

            final = ''.join(content_parts).strip()
            log.info('[llm] content=%d字 reasoning=%d字', len(final), reasoning_chars)
            return final or None
        except Exception as e:
            log.warning('[llm] 调用失败: %s', e)
            return None

    # ── 工具：解析 LLM 输出 ────────────────────────────────────────────

    @staticmethod
    def _parse_response(text: str, platform: str) -> tuple[str, str]:
        """从 LLM 响应中拆出 (title, content)。"""
        text = text.strip()

        def _strip_md(s: str) -> str:
            """剥离 markdown 装饰：开头/结尾的 * # ` > 和空白。"""
            return s.strip().strip('*').strip('#').strip('`').strip('>').strip()

        if platform == 'wechat':
            lines = text.split('\n')
            title = ''
            body_start = 0

            # 第一步：找含"标题"+冒号的行（先剥 markdown 再匹配）
            label_re = re.compile(r'^标题[\s*]*[:：][\s*]*(.+)$')
            for i, line in enumerate(lines):
                cleaned = _strip_md(line)
                if not cleaned:
                    continue
                m = label_re.match(cleaned)
                if m:
                    title = _strip_md(m.group(1))
                    body_start = i + 1
                    break

            # 兜底：第一个非空且 ≤40 字的 cleaned 行作标题
            if not title:
                for i, line in enumerate(lines):
                    s = _strip_md(line)
                    if s and len(s) <= 40:
                        title = s
                        body_start = i + 1
                        break

            # 跳过空行和"正文："label 行
            body_re = re.compile(r'^正文[\s*]*[:：]?\s*$')
            while body_start < len(lines):
                cleaned = _strip_md(lines[body_start])
                if not cleaned or body_re.match(cleaned):
                    body_start += 1
                    continue
                break

            content = '\n'.join(lines[body_start:]).strip()

            # 保险：title 仍含"标题"+冒号 → 强制剥离（兼容 fallback 走的路径）
            m = re.match(r'^[^一-龥]*标题[\s*]*[:：][\s*]*(.+)$', title)
            if m:
                title = _strip_md(m.group(1))

            return title, content

        # xhs：第一行作为标题，去掉 markdown 前缀；其余作正文
        lines = text.split('\n')
        title = (lines[0].strip().lstrip('#').strip()) if lines else ''
        # 标题超 20 字截断（小红书限制）
        if len(title) > 20:
            title = title[:20]
        content = '\n'.join(lines[1:]).strip() if len(lines) > 1 else text
        # 正文超 1000 字截断
        if len(content) > 1000:
            content = content[:997] + '…'
        return title, content

    def _collect_images(self, post: dict) -> str:
        """从 post 收集图片 URL（逗号分隔字符串）。

        优先级：
          1. attachment_local_path（XHS 多图）→ /img/attach/...
          2. cover_local_path（单封面）→ /img/cover/...
          3. cover_url CDN（兜底）
        """
        urls: list[str] = []

        attach = (post.get('attachment_local_path') or '').strip()
        if attach:
            for p in attach.split(','):
                u = self._local_to_http(p.strip())
                if u and u not in urls:
                    urls.append(u)

        cover_path = (post.get('cover_local_path') or '').strip()
        u = self._local_to_http(cover_path)
        if u and u not in urls:
            urls.append(u)

        if not urls:
            cover_cdn = (post.get('cover_url') or '').strip()
            for u in cover_cdn.split(','):
                u = u.strip()
                if u and u not in urls:
                    urls.append(u)

        return ','.join(urls)

    # ── xhs 专属流程：每篇独立"5 选 2 图" ─────────────────────────────────────

    def _per_post_xhs(self, user_id, db, batch, candidate_pool, sources,
                     pick, recent_pool, tmpl) -> int:
        """xhs 仿写：每篇笔记独立从 candidate_pool 个候选里 LLM 选 2 张图作配图。

        流程（每篇）：
          1. random 抽 candidate_pool 个 posts（"每篇各自抽 5 个"）
          2. 识图（缓存命中跳过）
          3. LLM 选 2 张最合适
          4. 这 2 张图作本篇配图
          5. 仿写文字：选中 2 张图对应 posts 的第 1 个作主素材
        """
        # 一次取 batch * candidate_pool 个 posts，按顺序拆 batch 组（已 LEFT JOIN 排除已仿写）
        total = db.get_posts_for_user(
            user_id, 'xhs', sources, limit=batch * candidate_pool,
            pick_strategy=pick, recent_pool=recent_pool,
        )
        if not total:
            log.info('[rewrite] %s/xhs 候选池为空', user_id)
            return 0

        log.info('[rewrite] %s/xhs 候选总池=%d 条，分 %d 组每组 %d',
                 user_id, len(total), batch, candidate_pool)

        count = 0
        already_main: set[int] = set()      # 防同一 post 被重复用作仿写主素材
        for i in range(batch):
            group = total[i * candidate_pool : (i + 1) * candidate_pool]
            if len(group) < 2:
                log.warning('[rewrite] %s/xhs 第 %d 组候选不足 2，跳过', user_id, i + 1)
                continue

            # 1. 识图（缓存优先）
            for p in group:
                if p.get('cover_image_desc'):
                    continue
                img_url = self._first_post_http_url(p)
                if not img_url:
                    continue
                try:
                    desc = self.image_gen.analyze_image(img_url)
                except Exception as e:
                    log.warning('[rewrite] post=%s 识图异常: %s', p.get('id'), e)
                    desc = ''
                if desc:
                    db.update_post_image_desc(p['id'], desc)
                    p['cover_image_desc'] = desc

            # 2. LLM 选 2 张最好的图
            with_desc = [p for p in group if p.get('cover_image_desc')]
            selected: list[dict] = []
            if len(with_desc) >= 2:
                ids = self._llm_pick_best_posts(with_desc, k=2)
                if ids:
                    id_set = set(ids)
                    selected = [p for p in with_desc if p['id'] in id_set][:2]
            # 兜底：LLM 失败/没足够描述 → 取前 2 个
            if len(selected) < 2:
                for p in group:
                    if p not in selected:
                        selected.append(p)
                        if len(selected) == 2:
                            break
            if len(selected) < 2:
                log.warning('[rewrite] %s/xhs 第 %d 组凑不齐 2 张图', user_id, i + 1)
                continue

            # 3. 收集 2 张配图（每个 selected post 取首图）
            selected_imgs: list[str] = []
            for p in selected:
                u = self._first_post_http_url(p)
                if u and u not in selected_imgs:
                    selected_imgs.append(u)
            if len(selected_imgs) < 2:
                # 极少：选中的 2 个 post 都没图。从同组其它 post 借
                for p in group:
                    if p in selected:
                        continue
                    u = self._first_post_http_url(p)
                    if u and u not in selected_imgs:
                        selected_imgs.append(u)
                        if len(selected_imgs) == 2:
                            break
            if len(selected_imgs) < 1:
                log.warning('[rewrite] %s/xhs 第 %d 组无图可用，跳过', user_id, i + 1)
                continue

            # 4. 仿写主素材：selected 里第一个还没用过的
            main_post = next((p for p in selected if p['id'] not in already_main), selected[0])
            already_main.add(main_post['id'])

            title   = (main_post.get('translated_title')   or main_post.get('title')   or '').strip()
            content = (main_post.get('translated_content') or main_post.get('content') or '').strip()
            if not (title or content):
                log.warning('[rewrite] %s/xhs 第 %d 组主素材无文字，跳过', user_id, i + 1)
                continue

            try:
                prompt = tmpl.format(title=title, content=content[:4000])
            except KeyError as e:
                log.warning('[rewrite] xhs 模板缺少占位符: %s', e)
                continue

            text = self._call_llm(prompt)
            if not text:
                continue
            new_title, new_content = self._parse_response(text, 'xhs')
            if not new_content:
                continue

            db.save_draft(
                user_id         = user_id,
                platform        = 'xhs',
                source_post_id  = main_post['id'],
                source_post_ids = ','.join(str(p['id']) for p in selected),
                title           = new_title,
                content         = new_content,
                image_urls      = ','.join(selected_imgs[:2]),
            )
            log.info(
                '[rewrite] ✓ %s/xhs 第 %d/%d 篇 main=%s 图=%d 张  → %s',
                user_id, i + 1, batch, main_post['id'], len(selected_imgs[:2]),
                (new_title or '')[:30],
            )
            count += 1

        return count

    def _llm_pick_best_posts(self, candidates: list, k: int) -> list[int]:
        """让 deepseek 从 candidates 里挑 k 个 id。返回 id 整数列表。"""
        rows = []
        for p in candidates:
            rows.append({
                'id':         p['id'],
                'image_desc': (p.get('cover_image_desc') or '').strip(),
                'title':      ((p.get('translated_title') or p.get('title') or '')[:60]).strip(),
            })
        prompt = (
            f'有 {len(rows)} 张候选小红书图片，请从中选出最适合作为小红书图文笔记配图的 {k} 张。\n\n'
            f'判断标准（重要度递减）：\n'
            f'- 画面具体真实（场景、人物、物品、风景；剔除纯文字截图、图标、表情包、低质量截图）\n'
            f'- 视觉吸引力（构图、色彩、生活感）\n'
            f'- 适合"泰国留学/校园生活"主题\n\n'
            f'候选（JSON）：\n{json.dumps(rows, ensure_ascii=False, indent=2)}\n\n'
            f'只输出 JSON，不要任何解释：\n'
            f'{{"selected_ids": [id1, id2]}}'
        )

        text = self._call_llm(prompt)
        if not text:
            return []
        try:
            m = re.search(r'\{[^{}]*"selected_ids"\s*:\s*\[[^\]]*\][^{}]*\}', text)
            if not m:
                m = re.search(r'\[\s*\d+(\s*,\s*\d+)*\s*\]', text)
                if m:
                    return [int(x) for x in re.findall(r'\d+', m.group(0))][:k]
                return []
            data = json.loads(m.group(0))
            ids = data.get('selected_ids') or []
            return [int(i) for i in ids][:k]
        except Exception as e:
            log.warning('[rewrite] LLM 选图响应解析失败: %s  text=%s', e, text[:200])
            return []

    def _first_post_http_url(self, post: dict) -> str:
        """取 post 第一张图的 HTTP URL（attach 优先 → cover_local → cover_url CDN 兜底）。"""
        for u in self._collect_images(post).split(','):
            u = u.strip()
            if u:
                return u
        return ''

    def collect_images_for_draft(self, post_imgs: list[str], category: str = '') -> list[str]:
        """草稿层图片汇总：原帖图 + RSU 兜底，确保至少 min_images 张。

        Args:
            post_imgs: 已从 posts 收集的本地服务器图（http://...:8899/img/cover/...）
            category: 原帖 category，用于 RSU 兜底时按 tag 智能选取
        """
        urls: list[str] = []
        for u in post_imgs:
            u = u.strip()
            if u and u not in urls:
                urls.append(u)

        # 原帖图够用就直接返回
        if len(urls) >= self.min_images:
            return urls

        # 按 category 关键词匹配 RSU tag（智能选图）
        prefer_tags = self._infer_rsu_tags(category)
        need = self.min_images - len(urls)
        rsu_urls = rsu_pick_random(
            n           = need,
            base_url    = self.rsu_base_url,
            exclude     = set(urls),
            prefer_tags = prefer_tags,
        )
        urls.extend(rsu_urls)
        log.debug('[rewrite] RSU 兜底：原帖 %d 张 + 补 %d 张  prefer=%s',
                  len(urls) - len(rsu_urls), len(rsu_urls), prefer_tags)
        return urls

    @staticmethod
    def _infer_rsu_tags(category: str) -> list[str]:
        """按 category 文本推断优先选哪些 RSU tag。"""
        c = (category or '').lower()
        tags: list[str] = []
        if any(k in c for k in ['学校', '官方', '校园', 'campus']):
            tags.append('campus')
        if any(k in c for k in ['文化', '泰国', '泰式', 'culture', 'thai']):
            tags.append('culture')
        if any(k in c for k in ['学院', 'college', '建筑', 'building']):
            tags.append('building')
        if any(k in c for k in ['风景', '自然', 'scenery']):
            tags.append('scenery')
        return tags or ['campus']    # 默认走校园

    def _local_to_http(self, local_path: str) -> str:
        """服务器路径 → HTTP URL（依赖 newmedia 已挂载 /img/cover、/img/attach）。"""
        if not local_path or not self.img_server:
            return ''
        if local_path.startswith(self.cover_dir + '/'):
            rel = local_path[len(self.cover_dir) + 1:]
            return f'{self.img_server}/img/cover/{rel}'
        if local_path.startswith(self.attach_dir + '/'):
            rel = local_path[len(self.attach_dir) + 1:]
            return f'{self.img_server}/img/attach/{rel}'
        return ''


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    parser = argparse.ArgumentParser(
        prog='python -m publisher_hub.rewrite',
        description='Publisher Hub 仿写 CLI',
    )
    parser.add_argument('user_id', help='用户 ID（config.yaml 中定义）')
    parser.add_argument(
        'platform', nargs='?', default=None, choices=['wechat', 'xhs', 'toutiao'],
        help='只跑某个平台；省略=该用户的三个平台都跑',
    )
    parser.add_argument('--debug', action='store_true', help='输出 DEBUG 级别日志')
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    config  = load_config()
    prompts = load_prompts()
    if not prompts:
        log.warning('prompts.yaml 为空或加载失败')

    db = Database(config)
    db.connect()

    engine    = RewriteEngine(config, prompts)
    platforms = [args.platform] if args.platform else ['wechat', 'xhs', 'toutiao']

    total = 0
    for p in platforms:
        try:
            n = engine.run_user(args.user_id, p, db)
        except Exception as e:
            log.error('[rewrite] %s/%s 异常: %s', args.user_id, p, e, exc_info=True)
            n = 0
        log.info('[done] %s/%s 新增草稿=%d 条', args.user_id, p, n)
        total += n

    log.info('[done] 总计 %d 条草稿', total)
    db.close()


if __name__ == '__main__':
    main()
