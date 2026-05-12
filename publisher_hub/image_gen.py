"""调用 wan2.7-image-pro 生成单张配图，并做"反 AI 识别"处理。

流程（vision-aware）：
    1. 用 qwen3.6-plus 看原帖图 → ≤30 字中文描述（如有原帖图）
    2. build_xhs_prompt(title, content, image_desc) → 综合 prompt
    3. POST /v1/images/generations → 阿里 OSS URL（24h 有效）
    4. 下载 → PIL 加噪声/抖动/重压缩 → MD5 改变、感知哈希漂移
    5. 服务器本地 cp 或 SCP → /data/assets/hub-generated/<id>.jpg
    6. 返回永久 URL: http://47.236.168.208:8899/img/hub-gen/<id>.jpg

任何步骤失败 → 回退到原 OSS URL（24h 有效）或纯文字 prompt（无 image_desc）。
"""
from __future__ import annotations

import base64
import io
import json
import logging
import random
import shutil
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageEnhance

log = logging.getLogger('publisher_hub.image_gen')

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_CACHE  = _PROJECT_ROOT / 'data' / 'generated-images'
_LOCAL_CACHE.mkdir(parents=True, exist_ok=True)


class ImageGenerator:
    def __init__(self, config: dict):
        ig = config.get('image_gen') or {}
        # 默认复用主 LLM 的 key/base_url（valueclue）
        llm = config.get('llm') or {}
        self.api_key  = (ig.get('api_key')  or llm.get('api_key', '')).strip()
        self.base_url = (ig.get('base_url') or llm.get('base_url', '')).rstrip('/')
        self.model    = ig.get('model', 'wan2.7-image-pro')
        self.size     = ig.get('size', '1024*1024')
        self.proxy    = ig.get('proxy') or None       # 默认空，本地调试可填 SOCKS5
        self.timeout  = float(ig.get('timeout_seconds', 180))
        self.enabled  = bool(self.api_key) and bool(ig.get('enabled', True))

        # vision 识图：用独立的 vision 段配置（不再用 valueclue + qwen，换成
        # 第三方 Gemini 网关，gemini-2.5-flash 视觉效果好、便宜）
        vc = config.get('vision') or {}
        self.vision_base_url = (vc.get('base_url') or '').rstrip('/')
        self.vision_api_key  = (vc.get('api_key')  or '').strip()
        self.vision_model    = vc.get('model', 'gemini-2.5-flash')
        # 老配置兼容：image_gen.vision_model + llm.base_url（用于 fallback 到 qwen）
        if not self.vision_base_url:
            self.vision_base_url = self.base_url
            self.vision_api_key  = self.api_key
            self.vision_model    = ig.get('vision_model', 'qwen3.6-plus')

        # 反 AI 识别 + 永久 URL（newmedia 服务器侧 /img/hub-gen 挂载）
        ms = config.get('mysql') or {}
        self.server_host = ms.get('host', '47.236.168.208')
        self.server_url  = (ms.get('image_server_url') or '').rstrip('/')
        self.remote_dir  = '/data/assets/hub-generated'
        self.url_path    = '/img/hub-gen'

    def generate(self, prompt: str, n: int = 1) -> list[str]:
        """生成 n 张图，返回 OSS URL 列表（24h 有效，**未经处理**）。失败返回 []。"""
        if not self.enabled:
            log.debug('[image_gen] 未启用，跳过')
            return []

        body = {
            'model':  self.model,
            'prompt': (prompt or '').strip()[:500],
            'size':   self.size,
            'n':      n,
        }
        try:
            with httpx.Client(timeout=self.timeout, proxy=self.proxy) as c:
                r = c.post(
                    f'{self.base_url}/images/generations',
                    headers={
                        'Authorization': f'Bearer {self.api_key}',
                        'Content-Type':  'application/json',
                    },
                    json=body,
                )
            if r.status_code >= 400:
                log.warning('[image_gen] HTTP %d: %s', r.status_code, r.text[:300])
                return []
            data = r.json()
            urls = [item.get('url') for item in (data.get('data') or []) if item.get('url')]
            log.info('[image_gen] ✓ wan 生成 %d 张  prompt=%s…', len(urls), (prompt or '')[:40])
            return urls
        except Exception as e:
            log.warning('[image_gen] 调用失败: %s', e)
            return []

    # ── 反 AI 识别 + 永久托管 ────────────────────────────────────────────────

    def generate_processed(self, prompt: str, draft_id: int | str = '') -> Optional[str]:
        """生成 + 加噪声 + 上传到永久 URL。失败返回 None（调用方应该自己 fallback）。

        Args:
            draft_id: 用于命名文件，避免冲突；空则用 uuid。
        """
        urls = self.generate(prompt, n=1)
        if not urls:
            return None
        return self._post_process(urls[0], draft_id=draft_id)

    def _post_process(self, oss_url: str, draft_id: int | str = '') -> Optional[str]:
        """下载 OSS 图 → 加噪声 → 写到 newmedia 服务器 hub-generated/ → 返回永久 URL。

        部署位置自动选路：
          - 远程（开发本机）→ scp 上传
          - 在 newmedia 服务器本地运行 → 直接写本地路径，跳过 SSH

        失败返回原 OSS URL（24h 有效）。
        """
        try:
            with httpx.Client(timeout=60) as c:
                r = c.get(oss_url, follow_redirects=True)
            r.raise_for_status()
            processed = self._add_noise(r.content)

            fname = f'{draft_id or uuid.uuid4().hex[:12]}.jpg'
            local_path = _LOCAL_CACHE / fname
            local_path.write_bytes(processed)

            remote_path = Path(self.remote_dir) / fname
            if self._is_running_on_server():
                # 服务器本地：直接 cp 到挂载目录
                remote_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(str(local_path), str(remote_path))
                log.debug('[image_gen] 服务器本地 cp → %s', remote_path)
            else:
                # 远程：scp
                subprocess.run(
                    ['scp', '-o', 'StrictHostKeyChecking=no', '-q',
                     str(local_path), f'root@{self.server_host}:{remote_path}'],
                    check=True, timeout=30, capture_output=True,
                )

            url = f'{self.server_url}{self.url_path}/{fname}'
            log.info('[image_gen] ✓ 反识别处理完成 %s', url)
            return url

        except Exception as e:
            log.warning('[image_gen] post_process 失败 (回退原 OSS URL): %s', e)
            return oss_url

    def _is_running_on_server(self) -> bool:
        """判断当前进程是否就跑在 self.server_host 上（部署后跳过 scp）。"""
        if not self.server_host or self.server_host in ('localhost', '127.0.0.1'):
            return True
        try:
            target_ips = {info[4][0] for info in socket.getaddrinfo(self.server_host, None)}
            local_ips  = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)}
            return bool(target_ips & local_ips)
        except Exception:
            return False

    @staticmethod
    def _add_noise(img_bytes: bytes) -> bytes:
        """对图片加噪声 + 微调亮度对比度 + 重压缩，破坏 MD5 和 perceptual hash。

        参考 newmedia/pipeline/image.py:_make_variant 的稳定算法。
        """
        try:
            import numpy as np
        except ImportError:
            # 没装 numpy 也兜底（仅用 PIL 做简单变换）
            return ImageGenerator._add_noise_no_numpy(img_bytes)

        img = Image.open(io.BytesIO(img_bytes)).convert('RGB')

        # ① RGB 通道整体微小偏移（人眼几乎察觉不到，但 MD5 必变）
        arr = np.array(img, dtype=np.float32)
        r_shift = random.uniform(-6, 6)
        b_shift = -r_shift * random.uniform(0.4, 0.9)
        arr[:, :, 0] = np.clip(arr[:, :, 0] + r_shift, 0, 255)
        arr[:, :, 2] = np.clip(arr[:, :, 2] + b_shift, 0, 255)
        img = Image.fromarray(arr.astype(np.uint8))

        # ② 微调亮度/对比度/饱和度/锐度（小幅，避免可见劣化）
        img = ImageEnhance.Brightness(img).enhance(random.uniform(0.94, 1.06))
        img = ImageEnhance.Contrast(img).enhance(random.uniform(0.95, 1.05))
        img = ImageEnhance.Color(img).enhance(random.uniform(0.92, 1.08))
        img = ImageEnhance.Sharpness(img).enhance(random.uniform(0.9, 1.2))

        # ③ 在某个角落加极小噪点（破坏感知哈希的角落特征）
        arr2 = np.array(img, dtype=np.int16)
        h, w = arr2.shape[:2]
        nw = max(1, w // 25)
        nh = max(1, h // 25)
        corner = random.randint(0, 3)
        x0, y0 = [(0, 0), (w - nw, 0), (0, h - nh), (w - nw, h - nh)][corner]
        noise = np.random.randint(-12, 13, (nh, nw, 3), dtype=np.int16)
        arr2[y0:y0 + nh, x0:x0 + nw] = np.clip(
            arr2[y0:y0 + nh, x0:x0 + nw] + noise, 0, 255,
        )
        img = Image.fromarray(arr2.astype(np.uint8))

        # ④ 重压缩（quality 随机）
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=random.randint(82, 92), optimize=True)
        return out.getvalue()

    @staticmethod
    def _add_noise_no_numpy(img_bytes: bytes) -> bytes:
        """numpy 不可用时的简化版本（仅用 PIL）。"""
        img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        img = ImageEnhance.Brightness(img).enhance(random.uniform(0.94, 1.06))
        img = ImageEnhance.Contrast(img).enhance(random.uniform(0.95, 1.05))
        img = ImageEnhance.Color(img).enhance(random.uniform(0.92, 1.08))
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=random.randint(82, 92), optimize=True)
        return out.getvalue()

    # ── 识图：下载图片 → 压缩 → Gemini 2.5-flash 看图 → 返回 ≤30 字中文描述 ─

    _VISION_PROMPT = '用 30 字以内中文，简洁描述这张图片的主体、色调和氛围。只输出描述本身。'

    def analyze_image(self, image_url: str) -> str:
        """用 vision 模型看图，返回简洁中文描述。失败返回 ''。

        根据 vision_model 自动选协议：含 'gemini' 走 Gemini 原生
        /v1beta/.../generateContent，否则走 OpenAI 兼容 /v1/chat/completions。
        """
        if not self.enabled or not image_url:
            return ''
        if not self.vision_base_url or not self.vision_api_key:
            return ''

        # 下载 + 压缩到 < 1MB（vision API 图大了慢且 token 贵）
        try:
            with httpx.Client(timeout=30, proxy=self.proxy) as c:
                r = c.get(image_url, follow_redirects=True)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content))
            img.thumbnail((1024, 1024))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            out = io.BytesIO()
            img.save(out, 'JPEG', quality=85)
            img_b64 = base64.b64encode(out.getvalue()).decode()
        except Exception as e:
            log.warning('[image_gen] 识图前下载/压缩失败 %s: %s', image_url[:60], e)
            return ''

        if 'gemini' in self.vision_model.lower():
            return self._analyze_gemini(img_b64)
        return self._analyze_openai(img_b64)

    def _analyze_gemini(self, img_b64: str) -> str:
        """Gemini 原生协议 generateContent。
        gemini-2.5-flash 默认开 thinking mode，要留够 maxOutputTokens 给 thoughts。
        网关账户池间歇性空，503 时自动重试（间隔 3s × 最多 3 次）。
        """
        import time as _t

        body = {
            'contents': [{
                'role': 'user',
                'parts': [
                    {'text': self._VISION_PROMPT},
                    {'inline_data': {'mime_type': 'image/jpeg', 'data': img_b64}},
                ],
            }],
            'generationConfig': {
                'maxOutputTokens': 1500,
                'temperature': 0.3,
            },
        }
        url = f'{self.vision_base_url}/v1beta/models/{self.vision_model}:generateContent'
        headers = {
            'x-goog-api-key': self.vision_api_key,
            'Content-Type':   'application/json',
        }

        MAX_TRIES, BACKOFF = 3, 3
        for attempt in range(MAX_TRIES):
            try:
                with httpx.Client(timeout=120) as c:
                    r = c.post(url, headers=headers, json=body)
            except Exception as e:
                log.warning('[image_gen] Gemini 识图连接异常 (try %d/%d): %s',
                            attempt + 1, MAX_TRIES, e)
                if attempt < MAX_TRIES - 1:
                    _t.sleep(BACKOFF)
                    continue
                return ''

            # 503 = 账户池空，等几秒可能恢复
            if r.status_code == 503 and attempt < MAX_TRIES - 1:
                log.info('[image_gen] Gemini 503 账户池空，%ds 后重试 (%d/%d)',
                         BACKOFF, attempt + 1, MAX_TRIES)
                _t.sleep(BACKOFF)
                continue
            if r.status_code >= 400:
                log.warning('[image_gen] Gemini 识图 HTTP %d (try %d/%d): %s',
                            r.status_code, attempt + 1, MAX_TRIES, r.text[:200])
                return ''

            try:
                data = r.json()
                parts = (data.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
                desc = ''.join(p.get('text', '') for p in parts).strip().replace('\n', ' ')
                log.info('[image_gen] ✓ Gemini 识图 (try %d): %s', attempt + 1, desc[:60])
                return desc
            except Exception as e:
                log.warning('[image_gen] Gemini 识图响应解析失败: %s', e)
                return ''
        return ''

    def _analyze_openai(self, img_b64: str) -> str:
        """OpenAI 兼容协议（qwen3.6-plus / claude 视觉走这条）+ valueclue stream。"""
        body = {
            'model': self.vision_model,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'image_url',
                     'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}},
                    {'type': 'text', 'text': self._VISION_PROMPT},
                ],
            }],
            'max_tokens': 200,
            'stream':     True,    # valueclue 16.8s 硬超时，stream 绕过
        }
        content_parts: list[str] = []
        try:
            with httpx.Client(timeout=120, proxy=self.proxy) as c:
                with c.stream('POST',
                              f'{self.vision_base_url}/chat/completions',
                              headers={
                                  'Authorization': f'Bearer {self.vision_api_key}',
                                  'Content-Type':  'application/json',
                              },
                              json=body) as resp:
                    if resp.status_code >= 400:
                        body_text = b''.join(resp.iter_bytes()).decode('utf-8', errors='replace')
                        log.warning('[image_gen] OpenAI 识图 HTTP %d: %s',
                                    resp.status_code, body_text[:300])
                        return ''
                    for line in resp.iter_lines():
                        line = (line or '').strip()
                        if not line:
                            continue
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
                        delta = (chunk.get('choices') or [{}])[0].get('delta') or {}
                        c_text = delta.get('content') or ''
                        if c_text:
                            content_parts.append(c_text)
        except Exception as e:
            log.warning('[image_gen] OpenAI 识图调用失败: %s', e)
            return ''
        desc = ''.join(content_parts).strip().replace('\n', ' ')
        log.info('[image_gen] ✓ OpenAI 识图: %s', desc[:60])
        return desc

    @staticmethod
    def build_xhs_prompt(title: str, content: str, image_desc: str = '') -> str:
        """从仿写标题+正文 + (可选) 原帖图描述 综合构造 prompt。"""
        snippet = (content or '').replace('\n', ' ').strip()[:150]
        lines = ['为小红书图文笔记配一张图。']
        if image_desc:
            lines.append(f'参考原帖图特征：{image_desc}（用于风格/主体借鉴，不是复制）')
        lines.append(f'仿写标题：{title}')
        lines.append(f'仿写主题：{snippet}')
        lines.append(
            '风格要求：小清新摄影风格，色调明亮自然、年轻人喜欢的视觉。'
            '符合泰国留学/校园生活/热带风光/学生分享氛围。'
            '画面构图简洁、不要文字水印、不要拼贴感。'
        )
        return '\n'.join(lines)
