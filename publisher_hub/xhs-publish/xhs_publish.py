#!/usr/bin/env python3
"""小红书发布 skill：Tavily 搜索 → LLM 仿写 → myaibot.vip 二维码发布。

非交互式，最后一行 stdout 始终是单行 JSON 供调用方解析。

用法：
    python3 xhs_publish.py --query "泰国留学最新政策"
    python3 xhs_publish.py --query "..." --category 单招 --dry-run
    python3 xhs_publish.py --title "标题" --body "正文" --images "url1,url2"
"""
from __future__ import annotations

import argparse
import base64
import itertools
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

# ── Tavily 搜索（6 key 轮询 + 失败回退）─────────────────────────────
TAVILY_KEYS = [
    "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",
    "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",
    "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",
    "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",
    "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",
    "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",
]
TAVILY_API_URL = "https://api.tavily.com/search"
# 持久化游标：每次启动从该文件读上次用到第几个 key，本次用 (cursor+1)%N
TAVILY_CURSOR_PATH = "/tmp/tavily_cursor.txt"


def _next_tavily_start_index() -> int:
    """读 cursor → +1 写回 → 返回这次该从哪个 index 起."""
    try:
        with open(TAVILY_CURSOR_PATH) as f:
            cur = int(f.read().strip())
    except Exception:
        cur = -1
    nxt = (cur + 1) % len(TAVILY_KEYS)
    try:
        with open(TAVILY_CURSOR_PATH, "w") as f:
            f.write(str(nxt))
    except Exception:
        pass
    return nxt

# ── 仿写 LLM（valueclue + deepseek-v4-pro，必须 stream）────────────
API_KEY        = "vc-user-1d1277d40afb8b5a4eb4cd98ea61cd22"
API_BASE_URL   = "https://valueclue.top/claw/api/proxy/v1"
MODEL_TEXT     = "deepseek-v4-pro"
LLM_MAX_TOKENS = 50000
LLM_TIMEOUT    = 300

# ── myaibot.vip 发布 ──────────────────────────────────────────────
MYAIBOT_TOKEN = "rn_f9ad924918cfa1a979a9adcc1ab75668772bd4584c4bcc0e7262658dce03"
MYAIBOT_URL   = "https://www.myaibot.vip/api/rednote/publish"

# ── 仿写提示词（按分类匹配）────────────────────────────────────────
PROMPT_CONFIGS = [
    {
        "keywords": ["泰国留学"],
        "prompt": """\
你是一位小红书内容运营专家，擅长泰国留学类内容，服务职高、大专、低分考生及家长。

请直接输出仿写笔记，不要任何前言或解释，只输出笔记本体。

仿写要求：
1. 保留原帖核心信息（学校/城市/费用/申请条件等）
2. 完全改变表达，不抄袭原文句子
3. 语气亲切真实，像有泰国留学经历的学长/学姐在分享
4. 结构：标题（含数字或疑问句）+ 正文 2-4 段（可加 emoji）+ 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
    {
        "keywords": ["东南亚留学"],
        "prompt": """\
你是一位小红书内容运营专家，专注东南亚留学（泰国、马来西亚、新加坡等）内容，
目标读者是想以低成本出国深造的中国学生。

请直接输出仿写笔记，不要任何前言或解释。

仿写要求：
1. 保留核心信息（目的地国家/学校/花费/申请要求）
2. 完全改变表达，不照抄原文
3. 语气像真人分享亲身经历或对比干货，自然接地气
4. 结构：标题（吸睛，可含对比/数字/疑问）+ 正文 2-4 段 + 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
    {
        "keywords": ["低分留学", "低成本留学", "普通家庭留学"],
        "prompt": """\
你是一位小红书内容运营专家，专注为低分、普通家庭学生挖掘性价比出路，
主打"分不高也能读本科"的留学路线（重点方向：泰国、东南亚）。

请直接输出仿写笔记，不要任何前言或解释。

仿写要求：
1. 紧扣低门槛、低成本、高性价比主题，保留原帖核心数据
2. 完全改变表达，不照抄原文
3. 语气像真人吐露自己找到出路的喜悦，带共情感
4. 结构：标题（突出"逆袭/低分/省钱"等关键词）+ 正文 2-4 段 + 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
    {
        "keywords": ["单招"],
        "prompt": """\
你是一位小红书内容运营专家，专注职高/中专/技校学生的升学规划，
包括单招、对口升学及海外本科（泰国）等路线。

请直接输出仿写笔记，不要任何前言或解释。

仿写要求：
1. 保留原帖核心升学信息（考试科目/录取线/学校/时间节点等）
2. 完全改变表达，不照抄原文
3. 语气像过来人或备考同学，实用接地气，带鼓励感
4. 结构：标题（含"单招/职高/逆袭"等关键词）+ 正文 2-4 段 + 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
    {
        "keywords": ["艺考"],
        "prompt": """\
你是一位小红书内容运营专家，专注艺考（美术/音乐/舞蹈/传媒）学生的升学规划，
了解艺考流程及海外艺术类本科申请（含泰国艺术类院校）。

请直接输出仿写笔记，不要任何前言或解释。

仿写要求：
1. 保留原帖艺考核心信息（科目/时间线/评分标准/院校要求等）
2. 完全改变表达，不照抄原文
3. 语气像有经验的艺考生或老师，专业中带亲切
4. 结构：标题（含"艺考/美术/音乐"等关键词）+ 正文 2-4 段 + 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
    {
        "keywords": ["高考志愿"],
        "prompt": """\
你是一位小红书内容运营专家，专注高考志愿填报及升学路线规划，
会结合国内院校和海外本科（重点：泰国）两条路线给出建议。

请直接输出仿写笔记，不要任何前言或解释。

仿写要求：
1. 保留原帖核心志愿/院校信息，数据准确
2. 完全改变表达，不照抄原文
3. 语气像学长学姐分享踩坑和经验，实用接地气
4. 结构：标题（含"志愿/报考/分数线"等词）+ 正文 2-4 段 + 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
    {
        "keywords": ["高考规划", "学业规划"],
        "prompt": """\
你是一位小红书内容运营专家，专注学生和家长的高考及升学规划，
内容涵盖国内路线规划和海外本科（泰国/东南亚）作为备选方案。

请直接输出仿写笔记，不要任何前言或解释。

仿写要求：
1. 保留原帖核心规划思路和关键时间节点
2. 完全改变表达，不照抄原文
3. 语气像有经验的规划老师或家长，温和实用有说服力
4. 结构：标题（含"规划/备考/出路"等词）+ 正文 2-4 段 + 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
    {
        # 兜底
        "keywords": [],
        "prompt": """\
你是一位小红书内容运营专家，专注升学规划和海外留学（重点：泰国）内容。

请直接输出仿写笔记，不要任何前言或解释。

仿写要求：
1. 保留原帖核心信息
2. 完全改变表达，不照抄原文
3. 语气真实亲切，像真人分享经历或干货
4. 结构：标题 + 正文 2-4 段 + 5-8 个 #话题标签
5. 不夸大、不虚假

原帖标题：{title}
原帖文案：
{content}
""",
    },
]

TOPIC_QUERIES = {
    "泰国留学": "泰国留学 2025 最新 费用 申请条件",
    "东南亚留学": "东南亚留学 性价比 低成本 本科",
    "低分留学": "低分出国留学 分数低 读本科 泰国",
    "单招": "单招 2025 报考 职高 升本",
    "艺考": "艺考 2025 美术 音乐 备考",
    "高考志愿": "高考志愿填报 2025 技巧 分数线",
    "高考规划": "高考规划 学业规划 升学路线",
}


# ── 工具 ─────────────────────────────────────────────────────────

def log(msg: str) -> None:
    """日志走 stderr，stdout 最后一行留给 JSON 结果。"""
    print(msg, file=sys.stderr, flush=True)


def get_prompt(category: str) -> str:
    for cfg in PROMPT_CONFIGS:
        if not cfg["keywords"]:
            continue
        for kw in cfg["keywords"]:
            if kw in (category or ""):
                return cfg["prompt"]
    return PROMPT_CONFIGS[-1]["prompt"]


def _http_post(url: str, payload: dict, headers: dict, timeout: int = 30) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {err_body[:400]}")


# ── Step 1: Tavily 搜索 ──────────────────────────────────────────

def tavily_search(query: str, max_results: int = 5) -> tuple[list[dict], str, list[str]]:
    """跨进程持久化游标 round-robin + 单 key 失败时按顺序回退到下一个 key。"""
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": True,
        "include_raw_content": False,
        "include_images": True,
    }
    n = len(TAVILY_KEYS)
    start = _next_tavily_start_index()
    last_err: Exception | None = None

    for offset in range(n):
        idx = (start + offset) % n
        key = TAVILY_KEYS[idx]
        tag = f"key#{idx+1}/{n} ...{key[-8:]}"
        try:
            log(f"[Tavily] {tag} query={query!r}")
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
            data = _http_post(TAVILY_API_URL, payload, headers, timeout=20)
            results = data.get("results", []) or []
            answer  = data.get("answer", "") or ""
            images_raw = data.get("images", []) or []
            images: list[str] = []
            for it in images_raw:
                if isinstance(it, str):
                    images.append(it)
                elif isinstance(it, dict) and it.get("url"):
                    images.append(it["url"])
            log(f"[Tavily] OK {tag}: {len(results)} 条 / answer={len(answer)} 字 / images={len(images)} 张")
            return results, answer, images
        except Exception as e:
            # 典型失败：HTTP 401 (key 失效) / 429 (限流) / 432 (额度耗尽) / 网络异常
            log(f"[Tavily] FAIL {tag}: {e}; 换下一个 key")
            last_err = e

    raise RuntimeError(f"Tavily 6 个 key 全失败，最后错误: {last_err}")


def pick_best_result(results: list[dict], answer: str) -> tuple[str, str]:
    if answer and len(answer) > 50:
        title = results[0].get("title", "热点资讯") if results else "热点资讯"
        return title, answer
    if results:
        r = results[0]
        return r.get("title", ""), r.get("content", "")
    return "", ""


# ── Step 2: LLM 仿写 ─────────────────────────────────────────────

def rewrite_content(category: str, title: str, content: str) -> str:
    """流式调 deepseek-v4-pro。

    踩坑（来自 publisher-hub DEVELOPMENT.md §8.2）：
    - valueclue 代理非流式 16.8s 会断 → 必须 stream=True
    - deepseek-v4-pro 是 reasoning 模型，响应里有 delta.reasoning_content 流，
      只拼接 delta.content，跳过 reasoning_content
    - reasoner 类不能传 temperature（v4-pro 名字没 reasoner 实测能传，保守不传）
    """
    prompt_tpl = get_prompt(category)
    prompt_text = prompt_tpl.format(
        title=title or "(无标题)",
        content=(content or "(无文案)")[:3000],
    )
    payload = {
        "model": MODEL_TEXT,
        "messages": [{"role": "user", "content": prompt_text}],
        "max_tokens": LLM_MAX_TOKENS,
        "stream": True,
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {API_KEY}",
    }
    log(f"[LLM] stream 仿写中（{MODEL_TEXT} 分类={category or '通用'}）...")

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req  = urllib.request.Request(
        f"{API_BASE_URL}/chat/completions", data=body, headers=headers, method="POST",
    )

    out_chunks: list[str] = []
    reasoning_len = 0
    finish_reason = None
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            buf = b""
            for raw in resp:
                buf += raw
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    payload_s = line[5:].strip()
                    if payload_s == b"[DONE]":
                        continue
                    try:
                        evt = json.loads(payload_s)
                    except Exception:
                        continue
                    for ch in evt.get("choices", []) or []:
                        delta = ch.get("delta") or {}
                        # 只收正文，跳过 reasoning
                        c = delta.get("content")
                        if c:
                            out_chunks.append(c)
                        r = delta.get("reasoning_content")
                        if r:
                            reasoning_len += len(r)
                        if ch.get("finish_reason"):
                            finish_reason = ch["finish_reason"]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {err_body[:400]}")

    text = "".join(out_chunks).strip()
    log(f"[LLM] finish={finish_reason} content={len(text)}字 reasoning={reasoning_len}字")
    if not text:
        raise RuntimeError("LLM 流式返回空 content（reasoning 可能没收敛完）")
    return text


# ── Step 3: 拆标题 / 正文 ───────────────────────────────────────

def _truncate_xhs_title(s: str, limit: float = 20.0) -> str:
    """按 myaibot 字符计数规则截断（中文 1 / ASCII 0.5 / emoji 2）。"""
    if not s:
        return ""
    budget = limit - 0.5
    used = 0.0
    out: list[str] = []
    for c in s:
        cp = ord(c)
        if cp < 128:
            w = 0.5
        elif 0x1F000 <= cp <= 0x1FFFF or 0x2600 <= cp <= 0x27BF:
            w = 2.0
        else:
            w = 1.0
        if used + w > budget:
            break
        out.append(c)
        used += w
    return "".join(out)


# 兼容 LLM 输出的标题前缀（半角/全角冒号），含 markdown 装饰
_TITLE_PREFIX_RE = re.compile(r"^[\s\*#]*标题[\s\*]*[:：][\s\*]*")


def split_title_body(rewritten: str) -> tuple[str, str]:
    lines = rewritten.strip().splitlines()
    title_line = ""
    body_lines: list[str] = []
    found_title = False
    for line in lines:
        stripped = line.strip()
        if not found_title:
            if stripped:
                title_line = stripped
                found_title = True
        else:
            body_lines.append(line)
    # 剥 markdown 装饰
    title_line = title_line.strip().lstrip("#").strip().lstrip("*").rstrip("*").strip()
    # 剥 "标题：" / "标题:" / "**标题**：" 等前缀
    title_line = _TITLE_PREFIX_RE.sub("", title_line)
    title_line = title_line.strip().strip("*").strip()
    title_line = _truncate_xhs_title(title_line, limit=20.0)
    body = "\n".join(body_lines).strip()
    # 正文如果首行也是 "正文：" 之类的引导词，剥掉
    body = re.sub(r"^[\s\*#]*正文[\s\*]*[:：][\s\*]*", "", body).strip()
    if len(body) > 1000:
        body = body[:997] + "…"
    return title_line, body


# ── Step 4: myaibot.vip 发布 ─────────────────────────────────────

def publish_to_xhs(title: str, content: str, images: list[str]) -> dict:
    """新版 myaibot API：api_key 在 body，type 必填，images 可空。"""
    payload = {
        "api_key": MYAIBOT_TOKEN,
        "type":    "normal",
        "title":   title,
        "content": content,
        "images":  images,
    }
    headers = {"Content-Type": "application/json"}
    log(f"[myaibot] 发布中  images={len(images)} 张  title={title!r}")
    return _http_post(MYAIBOT_URL, payload, headers, timeout=30)


def _save_qr_to_file(qr: str) -> Optional[str]:
    """如果是 data:image/png;base64,... 就解码存 PNG，返回路径。
    https URL 则跳过（agent 自己能渲染链接）。
    """
    if not qr.startswith("data:image"):
        return None
    m = re.match(r"data:image/(\w+);base64,(.*)", qr, re.DOTALL)
    if not m:
        return None
    ext = m.group(1).lower()
    if ext == "jpeg":
        ext = "jpg"
    try:
        raw = base64.b64decode(m.group(2))
    except Exception:
        return None
    path = f"/tmp/xhs_qr_{int(time.time() * 1000)}.{ext}"
    try:
        with open(path, "wb") as f:
            f.write(raw)
        os.chmod(path, 0o644)
        return path
    except Exception:
        return None


def extract_qr(resp: dict) -> Optional[str]:
    """提取二维码（base64 data URI 或 URL）。"""
    if not isinstance(resp, dict):
        return None
    sub = resp.get("data") if isinstance(resp.get("data"), dict) else {}
    candidates = [
        sub.get("qrcode"),       # 优先：base64
        sub.get("qr_code"),
        sub.get("qrcode_url"),
        sub.get("qr_url"),
        sub.get("qrUrl"),
        sub.get("url"),          # 兜底：发布页 URL
        resp.get("qrcode"),
        resp.get("qrcode_url"),
        resp.get("qr_url"),
        resp.get("qrUrl"),
    ]
    for u in candidates:
        if u and isinstance(u, str) and (u.startswith("data:image") or u.startswith("http")):
            return u
    return None


# ── 主流程 ──────────────────────────────────────────────────────

def run(args) -> dict:
    images = [u.strip() for u in (args.images or "").split(",") if u.strip()]

    # 直发模式：跳过搜索+仿写
    if args.title and args.body:
        title = _truncate_xhs_title(args.title.strip(), limit=20.0)
        body  = args.body.strip()
        if len(body) > 1000:
            body = body[:997] + "…"
        log(f"[direct] 直接用传入的 title/body 发布")
    else:
        # 搜索词
        query = args.query
        if not query:
            if args.category:
                query = TOPIC_QUERIES.get(args.category, f"{args.category} 小红书 2025 热点")
            else:
                return {"ok": False, "error": "缺少 --query（或同时给 --title+--body 直发）"}
        # 推断分类
        category = args.category
        if not category:
            for cat in TOPIC_QUERIES:
                if cat in query:
                    category = cat
                    break
        log(f"==== query={query} category={category or '通用'} ====")

        # 1. 搜索
        try:
            results, answer, tv_images = tavily_search(query)
        except Exception as e:
            return {"ok": False, "error": f"Tavily 搜索失败: {e}"}
        src_title, src_content = pick_best_result(results, answer)
        if not src_title and not src_content:
            return {"ok": False, "error": "Tavily 无有效搜索结果"}
        log(f"[source] {src_title[:60]}  /  {src_content[:80]}...")

        # 用户没显式给 --images 时，自动取 Tavily 返回的图（最多 4 张）
        if not images and tv_images:
            images = tv_images[:4]
            log(f"[images] 自动从 Tavily 取 {len(images)} 张图")

        # 2. 仿写
        try:
            rewritten = rewrite_content(category or "", src_title, src_content)
        except Exception as e:
            return {"ok": False, "error": f"LLM 仿写失败: {e}"}
        title, body = split_title_body(rewritten)

    log(f"\n──── 生成结果 ────")
    log(f"标题（{len(title)}/20）: {title}")
    log(f"正文（{len(body)}/1000）:\n{body}")
    log(f"────────────────")

    if args.dry_run:
        return {"ok": True, "dry_run": True, "title": title, "body": body, "images": images, "qr_url": ""}

    if not images:
        return {
            "ok": False,
            "error": "myaibot 要求至少 1 张图。直发模式请用 --images 'url1,url2' 传入",
            "title": title, "body": body,
        }

    # 3. 发布
    try:
        resp = publish_to_xhs(title, body, images)
    except Exception as e:
        return {"ok": False, "error": f"myaibot 发布失败: {e}", "title": title, "body": body}

    qr = extract_qr(resp)
    if qr:
        log(f"[myaibot] 二维码获取成功")
        qr_file = _save_qr_to_file(qr)
        if qr_file:
            log(f"[myaibot] 二维码已存到 {qr_file}")
        # 精简 raw：去掉 qrcode base64（很长，会污染 agent 上下文 + 干扰 OpenClaw 的
        # toolResult media path 自动识别），只留 id / url 供调用方参考。
        slim_raw = {
            "success": resp.get("success"),
            "message": resp.get("message"),
            "data": {
                "id":  (resp.get("data") or {}).get("id"),
                "url": (resp.get("data") or {}).get("url"),
            },
        }
        return {
            "ok": True,
            "qr_file": qr_file,        # 本地 PNG，OpenClaw 看到这一行会自动发图
            "title": title, "body": body,
            "publish_url": slim_raw["data"]["url"],
            "raw": slim_raw,
        }

    # 无二维码但响应表示成功
    if resp.get("code") in (0, 200) or resp.get("success"):
        return {"ok": True, "qr_url": "", "title": title, "body": body, "raw": resp,
                "note": "myaibot 返回成功但无二维码，请直接到小红书 App 确认"}

    return {"ok": False, "error": "未提取到二维码", "title": title, "body": body, "raw": resp}


def main():
    parser = argparse.ArgumentParser(description="小红书一键发布 skill")
    parser.add_argument("--query", "-q", default=None, help="Tavily 搜索词")
    parser.add_argument("--category", "-c", default=None,
                        help=f"分类: {' | '.join(TOPIC_QUERIES.keys())}")
    parser.add_argument("--images", default="", help="逗号分隔图片 URL（可选）")
    parser.add_argument("--dry-run", action="store_true", help="只生成不发布")
    parser.add_argument("--title", default=None, help="直发模式：标题")
    parser.add_argument("--body",  default=None, help="直发模式：正文")
    args = parser.parse_args()

    result = run(args)
    # stdout 三行：人类提示 + OpenClaw MEDIA directive + 单行 JSON
    qr_file = result.get("qr_file")
    if qr_file:
        print(f"✅ 二维码已生成：{qr_file}")
        print(f"MEDIA:{qr_file}")
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
