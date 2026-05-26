/**
 * End-to-end sample：用 edge-tts 生 3 段 audio + 拷 3 张 RSU 默认图 → Remotion 渲染 → open 播放。
 *
 * 跑法：
 *   npm install
 *   npm run render:sample
 *
 * 产物：
 *   output/sample.mp4
 *
 * 这是手动 demo 入口；正式 pipeline 走 render-from-job.ts。
 */
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import { spawn } from "node:child_process";
import { promisify } from "node:util";
import { exec as execCb } from "node:child_process";
import { mkdirSync, copyFileSync, existsSync } from "node:fs";
import { resolve, basename, join } from "node:path";
import type { VideoInput, SceneInput } from "../src/types";

const exec = promisify(execCb);

const ROOT = resolve(import.meta.dirname, "..");
const PUBLIC = resolve(ROOT, "public");
const OUTPUT = resolve(ROOT, "output");
// publisher-hub 自带的 4 张 RSU 默认图（assets/video-defaults/），与 newmedia 解耦
const RSU_ASSETS_DIR = resolve(ROOT, "..", "assets", "video-defaults");

// TTS：优先用系统 PATH 上的 python（项目 venv 装了 edge-tts），其它情况兜底找 newmedia 的 pixelle venv。
// 部署时 publisher-hub 自己的 venv 安装 edge-tts 包，下面这两个候选都能命中。
const PYTHON_CANDIDATES = [
  process.env.EDGE_TTS_PYTHON,                                              // 显式指定
  resolve(ROOT, "..", ".venv", "bin", "python"),                            // publisher-hub venv
  resolve(ROOT, "..", "..", "newmedia", "vendor", "pixelle-video", ".venv", "bin", "python"), // mac 本地老路径
  "python3",
].filter(Boolean) as string[];

// ── Sample 脚本（话题 = RSU 留学）────────────────────────────────────────────
// 这 4 张图是 publisher-hub/assets/video-defaults/，跟 video_gen Python 端默认值一致
const SAMPLE_SCENES: { narration: string; asset: string }[] = [
  {
    narration: "兰实大学位于泰国曼谷北部，是泰国最大的私立综合性大学之一。",
    asset: "RSU.jpg",
  },
  {
    narration: "学校提供全英文授课的国际课程，本科学费每年大约七到九万元人民币。",
    asset: "DSC_9393-1.jpg",
  },
  {
    narration: "适合想低分出国留学的同学，毕业证受中国教育部认证。",
    asset: "IMG_0242.jpg",
  },
];

const EDGE_TTS_VOICE = "zh-CN-XiaoxiaoNeural"; // 温柔女声；XiaoyiNeural 知性，YunyangNeural 男声
const EDGE_TTS_RATE = "+5%";                   // 语速微快，留学视频节奏感

const VIDEO_TITLE = "兰实大学留学指南";

// ── helpers ─────────────────────────────────────────────────────────────────

/** 试候选 Python 直到找到一个能 import edge_tts 的。返回路径或抛错。 */
import { existsSync } from "node:fs";
let _resolvedPython: string | null = null;
function resolvePython(): string {
  if (_resolvedPython) return _resolvedPython;
  for (const cand of PYTHON_CANDIDATES) {
    if (cand === "python3" || existsSync(cand)) {
      _resolvedPython = cand;
      return cand;
    }
  }
  throw new Error(`找不到 Python 解释器，候选都不在: ${PYTHON_CANDIDATES.join(", ")}`);
}

async function ttsBatch(items: { text: string; outPath: string }[]): Promise<void> {
  // 一次 Python 进程做完所有 TTS：避免反复创建 SSL 连接被微软端 RST，
  // 同时段间加 1.5s sleep + 失败自动 retry 3 次。
  const code = `
import asyncio, sys, ssl
try:
    # 优先用 pixelle-video 那一套，带 certifi + 限速
    from pixelle_video.utils.tts_util import edge_tts as _edge_tts
    async def edge_tts(*, text, voice, rate, output_path):
        await _edge_tts(text=text, voice=voice, rate=rate, output_path=output_path)
except Exception:
    # 兜底：用 edge-tts 包原生 API（publisher-hub venv 装的就是它）
    import edge_tts as _edge
    async def edge_tts(*, text, voice, rate, output_path):
        com = _edge.Communicate(text, voice=voice, rate=rate)
        await com.save(output_path)

ITEMS = ${JSON.stringify(items)}
VOICE = ${JSON.stringify(EDGE_TTS_VOICE)}
RATE  = ${JSON.stringify(EDGE_TTS_RATE)}

async def one(it, idx):
    last = None
    for attempt in range(3):
        if attempt > 0:
            wait = 3 * attempt
            print(f"  retry {attempt} after {wait}s", flush=True)
            await asyncio.sleep(wait)
        try:
            await edge_tts(text=it["text"], voice=VOICE, rate=RATE, output_path=it["outPath"])
            print(f"  TTS [{idx+1}/{len(ITEMS)}] OK -> {it['outPath']}", flush=True)
            return
        except Exception as e:
            last = e
            print(f"  TTS [{idx+1}/{len(ITEMS)}] 失败 attempt={attempt}: {type(e).__name__}: {e}", flush=True)
    raise last

async def main():
    for i, it in enumerate(ITEMS):
        if i > 0:
            await asyncio.sleep(1.5)
        await one(it, i)

asyncio.run(main())
`.trim();

  const py = resolvePython();
  await new Promise<void>((res, rej) => {
    const child = spawn(py, ["-c", code], {
      stdio: ["ignore", "inherit", "inherit"],
    });
    child.on("error", rej);
    child.on("close", (code) => {
      if (code === 0) res();
      else rej(new Error(`edge-tts 批量退出码 ${code}`));
    });
  });
}

async function probeDuration(audio: string): Promise<number> {
  // ffprobe 量精确秒数
  const { stdout } = await exec(
    `ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 ${JSON.stringify(audio)}`
  );
  const v = parseFloat(stdout.trim());
  if (!isFinite(v) || v <= 0) throw new Error(`无效音频时长: ${audio}`);
  return v;
}

// ── main ────────────────────────────────────────────────────────────────────

async function main() {
  mkdirSync(PUBLIC, { recursive: true });
  mkdirSync(OUTPUT, { recursive: true });

  console.log("== Step 1a: 准备素材图（拷到 public/）==");
  const sceneInputs: SceneInput[] = [];
  const ttsBatchInput: { text: string; outPath: string }[] = [];
  for (let i = 0; i < SAMPLE_SCENES.length; i++) {
    const sc = SAMPLE_SCENES[i];
    const srcAsset = join(RSU_ASSETS_DIR, sc.asset);
    if (!existsSync(srcAsset)) {
      throw new Error(`素材不存在: ${srcAsset}`);
    }
    const destName = `scene_${i + 1}_${sc.asset}`;
    const destAsset = join(PUBLIC, destName);
    copyFileSync(srcAsset, destAsset);
    console.log(`  scene ${i + 1}: ${destName}`);

    const audioName = `scene_${i + 1}.mp3`;
    const audioPath = join(PUBLIC, audioName);
    ttsBatchInput.push({ text: sc.narration, outPath: audioPath });

    sceneInputs.push({
      asset: destName,
      assetType: "image",
      narration: sc.narration,
      audio: audioName,
      durationSec: 0, // 占位，TTS 完后回填
    });
  }

  console.log("\n== Step 1b: 批量 edge-tts ==");
  await ttsBatch(ttsBatchInput);

  console.log("\n== Step 1c: 量音频时长 ==");
  for (let i = 0; i < sceneInputs.length; i++) {
    const audioPath = join(PUBLIC, sceneInputs[i].audio);
    const dur = await probeDuration(audioPath);
    sceneInputs[i].durationSec = dur + 0.4; // 留 0.4s 间隔避免硬切
    console.log(`  scene ${i + 1}: ${dur.toFixed(2)}s`);
  }

  const fps = 30;
  const totalSec = sceneInputs.reduce((a, s) => a + s.durationSec, 0);
  console.log(`\n== 总时长 ${totalSec.toFixed(2)}s，${Math.round(totalSec * fps)} 帧 @ ${fps}fps ==\n`);

  console.log("== Step 2: bundle Remotion 项目 ==");
  const serveUrl = await bundle({
    entryPoint: resolve(ROOT, "src", "index.ts"),
    onProgress: (p) => process.stdout.write(`\r  bundling ${Math.round(p)}%`),
  });
  console.log("\n  ✓ bundle 完成");

  const inputProps: VideoInput = {
    title: VIDEO_TITLE,
    scenes: sceneInputs,
    fps,
    width: 1080,
    height: 1920,
  };

  console.log("\n== Step 3: 选 composition ==");
  const composition = await selectComposition({
    serveUrl,
    id: "video",
    inputProps: inputProps as unknown as Record<string, unknown>,
  });
  console.log(`  ✓ ${composition.id}  ${composition.width}x${composition.height}  ${composition.durationInFrames} frames`);

  console.log("\n== Step 4: 渲染 mp4（视质量+并发约 1-3 分钟）==");
  const outPath = join(OUTPUT, "sample.mp4");
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: outPath,
    inputProps: inputProps as unknown as Record<string, unknown>,
    concurrency: 2,
    onProgress: ({ progress }) => {
      process.stdout.write(`\r  rendering ${(progress * 100).toFixed(1)}%`);
    },
  });
  console.log("\n  ✓ 渲染完成");

  console.log(`\n✅ 输出: ${outPath}`);
  console.log("正在用系统播放器打开…");
  spawn("open", [outPath], { stdio: "ignore", detached: true });
}

main().catch((e) => {
  console.error("\n❌ 渲染失败:", e);
  process.exit(1);
});
