"""
YouTube → bilingual (zh+en) subtitles + hardsub strip + QR code blur.

集成自 rangsit 项目（独立 CLI）→ publisher-hub library。
入口：process_youtube(url, opts) 见文件末尾。CLI 入口 main() 已移除。
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# --- Config -----------------------------------------------------------------

API_KEY = os.environ.get(
    "TRANSLATE_API_KEY",
    "vc-user-389bf1f7f2fd24fca5810468dbd3f24a",
)
BASE_URL = os.environ.get(
    "TRANSLATE_BASE_URL",
    "https://valueclue.top/claw/api/proxy/v1",
)
MODEL = os.environ.get("TRANSLATE_MODEL", "deepseek-v4-pro")

# publisher-hub 集成版本的路径：
#   parents[0] = publisher_hub/youtube/   （本模块）
#   parents[2] = publisher-hub/           （项目根，存放 .venv / cookies.txt）
PUBLISHER_HUB_ROOT  = Path(__file__).resolve().parents[2]
MODELS_DIR          = Path(__file__).resolve().parent / "models"
DEFAULT_OUTPUT_ROOT = PUBLISHER_HUB_ROOT / "data" / "youtube"
# 为兼容上游 rangsit 同名变量，保留 PROJECT_ROOT 但指向 publisher-hub 根
PROJECT_ROOT = PUBLISHER_HUB_ROOT

QR_SCAN_INTERVAL_SEC = 0.2
QR_BBOX_PAD_FACTOR = 1.6
QR_TIME_BUFFER_SEC = 0.3
QR_GROUP_GAP_SEC = 1.0
QR_BLUR_SIGMA = 50

TRANSLATE_BATCH_SIZE = 50
TRANSLATE_CONTEXT_BEFORE = 4  # last N already-translated segments shown as prior context
TRANSLATE_CONTEXT_AFTER = 4   # next N raw source segments shown as upcoming context

# Sentence-group merging — 把 YouTube 滚动式自动字幕重新分段，按"一句话"成组
SENTENCE_END_CHARS = ".!?。！？;；…"
MAX_GROUP_DURATION_SEC = 20.0  # 单条字幕最长 20 秒（YouTube 自动字幕窗口本身就宽，给长枚举句留余地）
MAX_GROUP_CHARS = 220          # 单组源文本超过这个字符数就强制断
MAX_GROUP_GAP_SEC = 1.5        # 相邻片段间静默 > 1.5s 强制断
MIN_GROUP_DURATION_SEC = 1.0   # 单条字幕最短 1 秒（避免闪一下就消失）

# 替换原片里"扫描二维码 / 关注公众号 / 访问链接"这类 CTA 为大中华区办事处口径，
# 因为中国发行版不出现二维码（详见 --blur-qr 默认关闭的逻辑）。
CHINA_BRANCH_CTA = {
    "en": "For more information or to apply for our programs, please follow the official Rangsit University China office.",
    "zh": "获取更多信息或申请课程，请关注中国兰实官方办事处。",
}

# 术语锁定 — 防止模型把 Rangsit 译成"蓝石"等直译，跨批保持一致
TRANSLATION_GLOSSARY = [
    ("Rangsit / Rangsit University / RSU", "兰实大学（绝不要译成 蓝石 / 蓝石大学）"),
    ("College of Design", "设计学院"),
    ("Visual Communication Design", "视觉传达设计"),
    ("Fashion Design", "时装设计"),
    ("Interior Design", "室内设计"),
    ("Photography", "摄影"),
    ("Product Design / Creative Product Design", "创意产品设计"),
]

# Hardsub band detection (smart crop)
HARDSUB_DETECT_FRAMES = 16
HARDSUB_BAND_SEARCH_FRAC = 0.30      # look in the bottom 30% of the frame
HARDSUB_EDGE_THRESHOLD = 0.04        # per-row edge density (frac of width) that counts as "edgy"
HARDSUB_FRAME_RATIO = 0.40           # row must be edgy in ≥40% of sampled frames
HARDSUB_MIN_BAND_HEIGHT_FRAC = 0.025 # ignore bands < 2.5% of height (likely noise)
HARDSUB_BAND_PAD_PX = 40             # safety margin above detected band (covers ascenders/descenders)
HARDSUB_MAX_CROP_FRAC = 0.30         # never crop more than 30% of height

# Try to make pyzbar find Homebrew's libzbar on Apple Silicon
try:
    ctypes.CDLL("/opt/homebrew/lib/libzbar.0.dylib")
except OSError:
    pass


# --- Helpers ----------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[*] {msg}", flush=True)


def find_bin(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} not found on PATH")
    return found


def extract_video_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|embed/|/v/|/shorts/)([\w-]{11})", url)
    if not m:
        raise ValueError(f"Cannot extract video ID from: {url}")
    return m.group(1)


_CAPTION_NOISE_BRACKET = re.compile(r"\[[^\]]+\]")  # [music], [音乐], [applause] …
_CAPTION_NOISE_SPEAKER = re.compile(r">+")           # >> / >>> speaker change markers


def clean_caption_noise(text: str) -> str:
    """Strip YouTube auto-caption noise so it doesn't reach the model or the SRT."""
    text = _CAPTION_NOISE_BRACKET.sub(" ", text)
    text = _CAPTION_NOISE_SPEAKER.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def fmt_srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# --- Steps ------------------------------------------------------------------

def download_video(url: str, out_dir: Path) -> Path:
    target = out_dir / "video.mp4"
    if target.exists():
        log(f"video.mp4 already present, skipping download")
        return target

    log(f"Downloading via yt-dlp: {url}")
    # Use the venv's yt-dlp so curl_cffi impersonation works
    yt_dlp = str(PROJECT_ROOT / ".venv/bin/yt-dlp")
    if not Path(yt_dlp).exists():
        yt_dlp = find_bin("yt-dlp")

    cmd = [
        yt_dlp,
        "-f", "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format", "mp4",
        "--impersonate", "Chrome",
        # 让 yt-dlp 自动从 GitHub 拉 EJS 组件来解 YouTube 的 JS 挑战；
        # 服务器侧（VPS IP）必须有这个才能拿到 video formats
        "--remote-components", "ejs:github",
        "-o", str(out_dir / "video.%(ext)s"),
    ]
    # cookies：从环境变量 YT_DLP_COOKIES 或项目根目录 cookies.txt 取
    # VPS IP 会被 YouTube 识别为 bot，必须带登录 cookies 才能下载
    cookies_path = os.environ.get("YT_DLP_COOKIES") or str(PROJECT_ROOT / "cookies.txt")
    if Path(cookies_path).exists():
        cmd += ["--cookies", cookies_path]
        log(f"  using cookies: {cookies_path}")
    cmd.append(url)

    subprocess.run(cmd, check=True)
    if not target.exists():
        raise RuntimeError("yt-dlp completed but video.mp4 was not produced")
    return target


def detect_hardsub_band(video_path: Path) -> tuple[int, int] | None:
    """Find a consistent horizontal subtitle band near the bottom of the frame.

    Returns (band_top_y, band_bottom_y) in original-frame pixel coords, or None
    if nothing convincing was found. Detection uses Canny edge density per row
    aggregated across sampled frames — rows with text are edgy in many frames.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(video_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    if total <= 0 or height <= 0:
        cap.release()
        return None

    sample_idxs = np.linspace(0, max(0, total - 1), HARDSUB_DETECT_FRAMES).astype(int)
    search_top = int(height * (1.0 - HARDSUB_BAND_SEARCH_FRAC))
    band_height = height - search_top
    edgy_count = np.zeros(band_height, dtype=np.int32)
    valid_frames = 0

    for idx in sample_idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok:
            continue
        region = frame[search_top:height, :, :]
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 160)
        density = edges.sum(axis=1) / (255.0 * width)
        edgy_count += (density > HARDSUB_EDGE_THRESHOLD).astype(np.int32)
        valid_frames += 1

    cap.release()
    if valid_frames < HARDSUB_DETECT_FRAMES // 2:
        return None

    min_frames = max(2, int(HARDSUB_FRAME_RATIO * valid_frames))
    is_band = edgy_count >= min_frames

    # longest contiguous True run
    best_start, best_end, best_len = -1, -1, 0
    cur_start = -1
    for y in range(band_height):
        if is_band[y]:
            if cur_start < 0:
                cur_start = y
        elif cur_start >= 0:
            length = y - cur_start
            if length > best_len:
                best_len, best_start, best_end = length, cur_start, y
            cur_start = -1
    if cur_start >= 0:
        length = band_height - cur_start
        if length > best_len:
            best_len, best_start, best_end = length, cur_start, band_height

    min_band_px = int(height * HARDSUB_MIN_BAND_HEIGHT_FRAC)
    if best_len < min_band_px:
        return None

    top = max(0, search_top + best_start - HARDSUB_BAND_PAD_PX)
    bottom = min(height, search_top + best_end + HARDSUB_BAND_PAD_PX)
    return top, bottom


def strip_hardsub_band(video_path: Path, out_dir: Path) -> Path:
    """Crop the burned-in subtitle band off the bottom, then scale back to original size.

    Returns the stripped video path if a band was found and removed, otherwise
    returns the original path unchanged.
    """
    target = out_dir / "video.stripped.mp4"
    if target.exists():
        log(f"{target.name} already present, skipping hardsub strip")
        return target

    log(f"Detecting hardsub band in {video_path.name}")
    band = detect_hardsub_band(video_path)
    if band is None:
        log("  no clear subtitle band detected — keeping original")
        return video_path

    top, _bottom = band
    width, height = get_video_dims(video_path)
    keep_h = top  # discard band-top → end-of-frame
    if keep_h <= 0 or keep_h < int(height * (1.0 - HARDSUB_MAX_CROP_FRAC)):
        log(f"  band starts too high (would crop >{HARDSUB_MAX_CROP_FRAC*100:.0f}% of height); skipping")
        return video_path

    # ffmpeg wants even dimensions for libx264
    if keep_h % 2:
        keep_h -= 1

    crop_pct = (height - keep_h) / height * 100
    log(f"  band found; cropping bottom {height - keep_h}px ({crop_pct:.1f}%), rescaling to {width}x{height}")

    ffmpeg = find_bin("ffmpeg")
    subprocess.run(
        [
            ffmpeg, "-y", "-i", str(video_path),
            "-vf", f"crop={width}:{keep_h}:0:0,scale={width}:{height}:flags=lanczos",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "copy",
            str(target),
        ],
        check=True,
    )
    return target


class SentenceGroup:
    """One linguistic-unit subtitle: spans multiple YouTube auto-caption snippets but
    reads as one sentence/phrase. Shape-compatible with youtube_transcript_api snippets
    (has .text / .start / .duration) so downstream translate/write code works unchanged.
    """
    __slots__ = ("text", "start", "duration")

    def __init__(self, text: str, start: float, duration: float) -> None:
        self.text = text
        self.start = start
        self.duration = duration


def group_snippets_into_sentences(snippets) -> list[SentenceGroup]:
    """Merge YouTube's rolling-window auto-caption snippets into proper sentence groups.

    Heuristics for "cut here":
      - prior text ends in a sentence terminator
      - silence gap to next snippet > MAX_GROUP_GAP_SEC
      - would-be group duration > MAX_GROUP_DURATION_SEC
      - would-be group character count > MAX_GROUP_CHARS
    """
    groups: list[SentenceGroup] = []
    cur_texts: list[str] = []
    cur_start: float | None = None
    cur_end: float = 0.0

    def flush() -> None:
        if cur_start is None or not cur_texts:
            return
        dur = max(cur_end - cur_start, MIN_GROUP_DURATION_SEC)
        groups.append(SentenceGroup(" ".join(cur_texts).strip(), cur_start, dur))

    for s in snippets:
        s_text = clean_caption_noise(s.text)
        if not s_text:
            continue
        s_start = float(s.start)
        s_end = s_start + float(s.duration)

        if cur_start is None:
            cur_start, cur_end = s_start, s_end
            cur_texts = [s_text]
            continue

        prev_text = cur_texts[-1]
        ends_sentence = prev_text and prev_text[-1] in SENTENCE_END_CHARS
        gap = max(0.0, s_start - cur_end)
        would_dur = max(s_end, cur_end) - cur_start
        would_chars = sum(len(t) for t in cur_texts) + 1 + len(s_text)

        if ends_sentence or gap > MAX_GROUP_GAP_SEC \
                or would_dur > MAX_GROUP_DURATION_SEC \
                or would_chars > MAX_GROUP_CHARS:
            flush()
            cur_start, cur_end = s_start, s_end
            cur_texts = [s_text]
        else:
            cur_texts.append(s_text)
            cur_end = max(cur_end, s_end)

    flush()
    return groups


class _VttSnippet:
    """模拟 youtube-transcript-api 的 FetchedTranscriptSnippet 接口（text/start/duration）。"""
    __slots__ = ('text', 'start', 'duration')

    def __init__(self, text: str, start: float, duration: float):
        self.text = text
        self.start = start
        self.duration = duration


_VTT_TS_RE = re.compile(
    r'(\d+):(\d+):(\d+)\.(\d+)\s*-->\s*(\d+):(\d+):(\d+)\.(\d+)'
)


def _parse_vtt(vtt_text: str) -> list[_VttSnippet]:
    """简易 VTT 解析。返回去重后的 snippets，shape 兼容 youtube-transcript-api。"""
    out: list[_VttSnippet] = []
    seen_text: set[tuple[int, str]] = set()
    for blk in re.split(r'\n\s*\n', vtt_text):
        lines = [ln for ln in blk.splitlines() if ln.strip()]
        if not lines:
            continue
        ts_idx = next((i for i, ln in enumerate(lines) if '-->' in ln), -1)
        if ts_idx < 0:
            continue
        m = _VTT_TS_RE.search(lines[ts_idx])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
        t0 = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        t1 = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        text = '\n'.join(lines[ts_idx + 1:])
        text = re.sub(r'<[^>]+>', '', text)             # 去 <c>, <00:00:01.000> 等内嵌标签
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            continue
        key = (int(t0 * 10), text)
        if key in seen_text:
            continue
        seen_text.add(key)
        out.append(_VttSnippet(text=text, start=t0, duration=max(0.1, t1 - t0)))
    return out


def fetch_transcript(video_id: str):
    """用 yt-dlp 拉字幕并解析 VTT。

    publisher-hub 跑在 VPS：youtube-transcript-api 的 innertube endpoint
    会被 YouTube 对 cloud IP 段单独 ban（cookies 救不了）。yt-dlp 走
    player API + EJS + cookies 能正常拿字幕。
    """
    log(f"Fetching transcript for {video_id}")
    url = f"https://www.youtube.com/watch?v={video_id}"
    yt_dlp = str(PUBLISHER_HUB_ROOT / ".venv/bin/yt-dlp")
    if not Path(yt_dlp).exists():
        yt_dlp = find_bin("yt-dlp")
    cookies_path = os.environ.get("YT_DLP_COOKIES") or str(PUBLISHER_HUB_ROOT / "cookies.txt")
    sub_langs = "en,en-US,en-GB,zh,zh-Hans,zh-CN,zh-Hant,zh-TW"

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            yt_dlp,
            "--write-subs", "--write-auto-subs",
            "--sub-langs", sub_langs,
            "--sub-format", "vtt",
            "--skip-download",
            "--no-warnings",
            "--impersonate", "Chrome",
            "--remote-components", "ejs:github",
            "-o", str(Path(tmpdir) / "vid.%(ext)s"),
        ]
        if Path(cookies_path).exists():
            cmd += ["--cookies", cookies_path]
        cmd.append(url)
        subprocess.run(cmd, check=True)

        vtts = list(Path(tmpdir).glob("vid.*.vtt"))
        if not vtts:
            raise RuntimeError("yt-dlp 没生成字幕文件，可能视频本身没字幕")

        manual = [p for p in vtts if '.auto.' not in p.name]
        chosen = (manual or vtts)[0]
        parts = chosen.stem.split('.')
        lang_code = parts[1] if len(parts) > 1 else 'unknown'
        is_auto = ('.auto.' in chosen.name) or (chosen not in manual)
        log(f"  source: {lang_code} {'auto-generated' if is_auto else 'manual'} ({chosen.name})")

        vtt_text = chosen.read_text(encoding='utf-8', errors='replace')
        snippets = _parse_vtt(vtt_text)

    if not snippets:
        raise RuntimeError(f"VTT 解析后没拿到任何字幕段，文件：{chosen.name}")
    log(f"  parsed {len(snippets)} segments")
    return snippets, lang_code, lang_code


def _llm_translate_batch(
    client,
    src_lang: str,
    snippets,
    prior_pairs: list[tuple[str, dict]] | None = None,
    next_sources: list[str] | None = None,
) -> list[dict]:
    """Translate a batch with surrounding context so the model can keep continuity.

    prior_pairs: already-translated (source_text, {en, zh}) tuples immediately before this batch.
    next_sources: raw source texts for segments that come right after this batch.
    Both are shown to the model as REFERENCE ONLY — the model must only produce
    translations for the in-batch segments.
    """
    numbered = "\n".join(
        f"[{i + 1}] {clean_caption_noise(s.text)}" for i, s in enumerate(snippets)
    )

    context_blocks = []
    if prior_pairs:
        prior_lines = "\n".join(
            f"  src: {clean_caption_noise(src)}\n   en: {pair['en']}\n   zh: {pair['zh']}"
            for src, pair in prior_pairs
        )
        context_blocks.append(
            "PRIOR CONTEXT (already translated, DO NOT re-translate — use only "
            "for tense / pronoun / topic continuity):\n" + prior_lines
        )
    if next_sources:
        next_lines = "\n".join(f"  - {clean_caption_noise(t)}" for t in next_sources)
        context_blocks.append(
            "UPCOMING CONTEXT (next source segments, DO NOT translate yet — "
            "look ahead to handle mid-sentence breaks correctly):\n" + next_lines
        )
    context_str = ("\n\n" + "\n\n".join(context_blocks)) if context_blocks else ""

    glossary_str = "\n".join(f"  - {src}  →  {dst}" for src, dst in TRANSLATION_GLOSSARY)

    prompt = (
        f"Translate these {len(snippets)} subtitle segments from {src_lang} "
        f"into BOTH natural English (`en`) and Simplified Chinese (`zh`).\n\n"
        f"Rules:\n"
        f"- Return EXACTLY {len(snippets)} items in the original order.\n"
        f"- Do not merge, split, or skip segments — one input segment maps to one output item.\n"
        f"- Each input segment has been pre-grouped into a complete (or near-complete) "
        f"spoken phrase or sentence. Translate it as a self-contained line that fits its "
        f"time window. Do NOT split or merge content across segments — one input segment "
        f"yields one output item.\n"
        f"- Preserve segment boundaries; the timing of each subtitle line is fixed.\n"
        f"- Use natural, conversational tone consistent with prior context.\n"
        f"- Keep terminology consistent with the prior translations shown in context "
        f"(re-use the same English/Chinese rendering of recurring names, programs, places).\n"
        f"- Transliterate proper nouns appropriately.\n"
        f"- Do NOT include caption-system noise like '[music]', '[音乐]', '[applause]', "
        f"'>>', etc. in your output. They have already been stripped from the source.\n"
        f"- TERMINOLOGY (use EXACTLY these renderings everywhere; never alternates):\n"
        f"{glossary_str}\n"
        f"- LOCALIZATION OVERRIDE: If a segment is a call-to-action that asks viewers to "
        f"scan a QR code (e.g. 'scan the QR above', 'สแกน QR', '扫描二维码'), follow a "
        f"Facebook/Line/Instagram/social media link, or otherwise directs them to a QR or "
        f"external link for more info / applications, do NOT translate literally. Instead, "
        f"substitute the entire segment with EXACTLY this localized copy:\n"
        f"    en: \"{CHINA_BRANCH_CTA['en']}\"\n"
        f"    zh: \"{CHINA_BRANCH_CTA['zh']}\"\n"
        f"  Apply the override segment-by-segment; segments that are NOT QR/link CTAs must "
        f"be translated normally.{context_str}\n\n"
        f'Output JSON only: {{"items": [{{"en": "...", "zh": "..."}}, ...]}}\n\n'
        f"Segments to translate:\n{numbered}"
    )
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    items = data.get("items") or data.get("translations") or []
    if len(items) != len(snippets):
        raise RuntimeError(
            f"Translation count mismatch: got {len(items)}, expected {len(snippets)}"
        )
    # Defensive: scrub any caption noise the model may have echoed back
    for it in items:
        it["en"] = clean_caption_noise(it.get("en", ""))
        it["zh"] = clean_caption_noise(it.get("zh", ""))
    return items


def translate_all(snippets, src_lang: str) -> list[dict]:
    from openai import OpenAI

    log(f"Translating {len(snippets)} segments via {MODEL}")
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    out: list[dict] = []
    i = 0
    batch_size = TRANSLATE_BATCH_SIZE
    while i < len(snippets):
        batch = snippets[i : i + batch_size]
        prior_pairs = [
            (snippets[j].text, out[j])
            for j in range(max(0, i - TRANSLATE_CONTEXT_BEFORE), i)
        ]
        next_sources = [
            s.text for s in snippets[i + len(batch) : i + len(batch) + TRANSLATE_CONTEXT_AFTER]
        ]
        try:
            out.extend(
                _llm_translate_batch(client, src_lang, batch, prior_pairs, next_sources)
            )
            i += len(batch)
            log(f"  translated {i}/{len(snippets)}")
        except RuntimeError as e:
            if len(batch) <= 1:
                raise
            new_size = max(1, len(batch) // 2)
            log(f"  {e}; retrying with smaller batch ({new_size})")
            batch_size = new_size
    return out


def write_srts(snippets, translations, out_dir: Path) -> tuple[Path, Path, Path]:
    en_srt = out_dir / "subs.en.srt"
    zh_srt = out_dir / "subs.zh-Hans.srt"
    bi_srt = out_dir / "subs.bilingual.srt"

    def _write(path: Path, lines):
        with open(path, "w", encoding="utf-8") as f:
            for i, (snip, line) in enumerate(zip(snippets, lines), 1):
                t0 = fmt_srt_ts(snip.start)
                t1 = fmt_srt_ts(snip.start + snip.duration)
                f.write(f"{i}\n{t0} --> {t1}\n{line}\n\n")

    _write(en_srt, [t["en"] for t in translations])
    _write(zh_srt, [t["zh"] for t in translations])
    _write(bi_srt, [f"{t['en']}\n{t['zh']}" for t in translations])
    log(f"  wrote {en_srt.name}, {zh_srt.name}, {bi_srt.name}")
    return en_srt, zh_srt, bi_srt


def detect_qr_windows(video_path: Path) -> list[tuple]:
    import cv2

    log(f"Scanning {video_path.name} for QR codes")
    for f in ("detect.prototxt", "detect.caffemodel", "sr.prototxt", "sr.caffemodel"):
        if not (MODELS_DIR / f).exists():
            raise RuntimeError(f"Missing WeChat QR model file: {MODELS_DIR / f}")
    detector = cv2.wechat_qrcode_WeChatQRCode(
        str(MODELS_DIR / "detect.prototxt"),
        str(MODELS_DIR / "detect.caffemodel"),
        str(MODELS_DIR / "sr.prototxt"),
        str(MODELS_DIR / "sr.caffemodel"),
    )

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(fps * QR_SCAN_INTERVAL_SEC))

    hits: list[tuple] = []
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % step == 0:
            t = frame_idx / fps
            try:
                texts, points = detector.detectAndDecode(frame)
                for txt, pts in zip(texts, points):
                    if not txt:
                        continue
                    xs = [int(p[0]) for p in pts]
                    ys = [int(p[1]) for p in pts]
                    x, y = min(xs), min(ys)
                    w, h = max(xs) - x, max(ys) - y
                    hits.append((t, txt, (x, y, w, h)))
            except Exception:
                pass
        frame_idx += 1
    cap.release()

    return _group_hits(hits)


def _group_hits(hits: list[tuple]) -> list[tuple]:
    """Group same-content hits within QR_GROUP_GAP_SEC into a single time window."""
    by_content: dict[str, list[tuple[float, tuple]]] = {}
    for t, content, bbox in hits:
        by_content.setdefault(content, []).append((t, bbox))

    windows: list[tuple] = []
    for content, lst in by_content.items():
        lst.sort(key=lambda x: x[0])
        cur_t0, cur_t1, cur_bbox = lst[0][0], lst[0][0], lst[0][1]
        for t, bbox in lst[1:]:
            if t - cur_t1 <= QR_GROUP_GAP_SEC:
                cur_t1 = t
                x1, y1, w1, h1 = cur_bbox
                x2, y2, w2, h2 = bbox
                nx, ny = min(x1, x2), min(y1, y2)
                nw = max(x1 + w1, x2 + w2) - nx
                nh = max(y1 + h1, y2 + h2) - ny
                cur_bbox = (nx, ny, nw, nh)
            else:
                windows.append((cur_t0, cur_t1, cur_bbox, content))
                cur_t0, cur_t1, cur_bbox = t, t, bbox
        windows.append((cur_t0, cur_t1, cur_bbox, content))

    padded: list[tuple] = []
    for t0, t1, (x, y, w, h), content in windows:
        cx, cy = x + w / 2, y + h / 2
        nw, nh = w * QR_BBOX_PAD_FACTOR, h * QR_BBOX_PAD_FACTOR
        nx, ny = max(0, int(cx - nw / 2)), max(0, int(cy - nh / 2))
        padded.append(
            (
                max(0.0, t0 - QR_TIME_BUFFER_SEC),
                t1 + QR_TIME_BUFFER_SEC,
                (nx, ny, int(nw), int(nh)),
                content,
            )
        )
    return padded


def get_video_dims(video_path: Path) -> tuple[int, int]:
    ffprobe = shutil.which("ffprobe") or find_bin("ffprobe")
    raw = subprocess.check_output(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video_path)]
    )
    s = json.loads(raw)["streams"][0]
    return int(s["width"]), int(s["height"])


# 硬烧字幕样式（ffmpeg `subtitles=...:force_style=...` 用的 ASS override）
# 关键：白字 + 黑色描边 + 底部居中 + 字体大小适中；中文要求字体存在
_HARDSUB_STYLE = (
    "Fontname=Noto Sans CJK SC,"
    "Fontsize=22,"
    "PrimaryColour=&H00FFFFFF,"      # 白字（&H AABBGGRR）
    "OutlineColour=&H80000000,"      # 半透明黑描边
    "BorderStyle=1,"                 # 1=描边 + 阴影
    "Outline=2,"
    "Shadow=0,"
    "Alignment=2,"                   # 底部居中
    "MarginV=40"
)


def _escape_for_ffmpeg_filter(p: Path) -> str:
    """subtitles= 内的路径需要转义冒号、引号、反斜杠。"""
    s = str(p)
    s = s.replace('\\', '\\\\')
    s = s.replace(':', r'\:')
    s = s.replace("'", r"\'")
    return s


def build_filter_chain(
    width: int, height: int, qr_windows: list[tuple], bilingual_srt: Path,
) -> str:
    """构建 ffmpeg filter chain：QR blur（可选）+ 硬烧双语字幕（必须）。"""
    bi_escaped = _escape_for_ffmpeg_filter(bilingual_srt)
    subs_filter = f"subtitles='{bi_escaped}':force_style='{_HARDSUB_STYLE}'"

    if not qr_windows:
        # 没有 QR → 单条 subtitles filter
        return f"[0:v]{subs_filter}[vout]"

    # 有 QR：先 split → 复制原帧 + crop+blur 区域 → overlay 回 → subtitles
    n = len(qr_windows)
    parts = []
    parts.append(
        "[0:v]split=" + str(n + 1) + "[base]" + "".join(f"[s{i}]" for i in range(n))
    )

    clamped = []
    for t0, t1, (x, y, w, h), _ in qr_windows:
        x = max(0, min(x, width - 2))
        y = max(0, min(y, height - 2))
        w = max(2, min(w, width - x))
        h = max(2, min(h, height - y))
        clamped.append((t0, t1, (x, y, w, h)))

    for i, (_t0, _t1, (x, y, w, h)) in enumerate(clamped):
        parts.append(
            f"[s{i}]crop={w}:{h}:{x}:{y},gblur=sigma={QR_BLUR_SIGMA},setsar=1[b{i}]"
        )

    prev = "base"
    for i, (t0, t1, (x, y, _w, _h)) in enumerate(clamped):
        out = "vqr" if i == n - 1 else f"v{i}"
        parts.append(
            f"[{prev}][b{i}]overlay={x}:{y}"
            f":enable='between(t,{t0:.3f},{t1:.3f})'[{out}]"
        )
        prev = out

    # 最后一道 overlay 出 [vqr]，再走 subtitles → [vout]
    parts.append(f"[vqr]{subs_filter}[vout]")

    return ";".join(parts)


def render_final(
    video_path: Path,
    qr_windows: list[tuple],
    sub_files: tuple[Path, Path, Path],
    out_dir: Path,
) -> Path:
    """硬烧双语字幕（subtitles=...），无论 QR 是否处理都重编码视频流。

    硬烧的代价：必须 libx264 重编码（无法 -c:v copy）。30s 视频 < 30s 完成。
    收益：浏览器 <video> / 微信播放器等到处都能看到字幕，不依赖软轨支持。

    同时仍软挂三轨字幕（mov_text），支持的播放器可以切换轨道；
    硬烧的是双语，软轨包括 双语/英文/中文 三套。
    """
    en_srt, zh_srt, bi_srt = sub_files
    final = out_dir / "video_final.mp4"
    log(f"Rendering final → {final.name}")

    ffmpeg = find_bin("ffmpeg")
    w, h = get_video_dims(video_path)
    fchain = build_filter_chain(w, h, qr_windows, bi_srt)

    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-i", str(bi_srt), "-i", str(en_srt), "-i", str(zh_srt),
        "-filter_complex", fchain,
        "-map", "[vout]", "-map", "0:a",
        "-map", "1", "-map", "2", "-map", "3",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=chi",
        "-metadata:s:s:0", "title=中英双语",
        "-metadata:s:s:1", "language=eng",
        "-metadata:s:s:1", "title=English",
        "-metadata:s:s:2", "language=chi",
        "-metadata:s:s:2", "title=简体中文",
        "-disposition:s:0", "default",
        str(final),
    ]
    subprocess.run(cmd, check=True)
    return final


# --- Library entry point ----------------------------------------------------

def process_youtube(
    url: str,
    *,
    output_subdir: str | None = None,
    strip_hardsub: bool = True,
    blur_qr: bool = False,
) -> dict[str, Any]:
    """Publisher-hub 调用入口。

    Args:
        url:           YouTube 视频 URL / ID
        output_subdir: 输出子目录名（默认用 video_id）
        strip_hardsub: 是否自动检测并裁掉底部硬字幕条
        blur_qr:       是否检测并模糊视频里的二维码

    Returns:
        summary dict（含 video_id / final_video / srt 路径 / qr_codes 等）
    """
    video_id = extract_video_id(url)
    out_dir = DEFAULT_OUTPUT_ROOT / (output_subdir or video_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Output: {out_dir}")

    raw_video = download_video(url, out_dir)
    video = strip_hardsub_band(raw_video, out_dir) if strip_hardsub else raw_video
    snippets, src_code, src_name = fetch_transcript(video_id)
    groups = group_snippets_into_sentences(snippets)
    log(f"Merged {len(snippets)} caption snippets into {len(groups)} sentence groups")
    translations = translate_all(groups, src_name)
    sub_files = write_srts(groups, translations, out_dir)

    if blur_qr:
        qr_windows = detect_qr_windows(video)
        log("=== QR codes detected ===")
        if qr_windows:
            for t0, t1, bbox, content in qr_windows:
                log(f"  [{t0:6.2f}s — {t1:6.2f}s] bbox={bbox} content={content!r}")
        else:
            log("  none")
    else:
        qr_windows = []
        log("QR detection/blur skipped (blur_qr=False)")

    final = render_final(video, qr_windows, sub_files, out_dir)

    log("=== Done ===")
    log(f"Final video: {final}")

    summary = {
        "url": url,
        "video_id": video_id,
        "source_language": f"{src_code} ({src_name})",
        "segments": len(snippets),
        "sentence_groups": len(groups),
        "qr_codes": [
            {"t_start": w[0], "t_end": w[1], "bbox": w[2], "content": w[3]}
            for w in qr_windows
        ],
        "hardsub_stripped": video != raw_video,
        "outputs": {
            "final_video": str(final),
            "raw_video": str(raw_video),
            "working_video": str(video),
            "bilingual_srt": str(sub_files[2]),
            "en_srt": str(sub_files[0]),
            "zh_srt": str(sub_files[1]),
        },
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
