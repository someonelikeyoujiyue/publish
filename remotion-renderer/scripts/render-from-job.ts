/**
 * 从一个 job 目录渲染视频。Python 端写 input.json 进去 → spawn 我 → 我读 → 渲染 → 写 output.mp4 → 退出。
 *
 * 用法：
 *   node --import tsx scripts/render-from-job.ts /path/to/data/video-jobs/<id>
 *
 * 入参文件 input.json 格式（与 src/types.ts 的 VideoInput 一致 + 额外 outputName 选项）：
 *   {
 *     "title": "...",
 *     "scenes": [
 *       {
 *         "asset":       "<绝对路径或 URL>",   // jpg/png/mp4
 *         "assetType":   "image" | "video",
 *         "narration":   "字幕文字",
 *         "audio":       "<绝对路径>",          // mp3 旁白
 *         "durationSec": 6.14
 *       }
 *     ],
 *     "fps": 30,
 *     "width": 1080,
 *     "height": 1920,
 *     "bgm":       "<绝对路径>",               // 可选
 *     "bgmVolume": 0.2,
 *     "outputName": "output.mp4"
 *   }
 *
 * 产出：<job_dir>/output.mp4 （或 input.json.outputName）。
 *
 * 退出码：
 *   0 成功
 *   1 通用失败
 *   2 input.json 不存在 / 解析错
 *
 * stdout 实时打印 PROGRESS=<0..1> 行，方便 Python 端解析（不展开做了，可选）。
 */
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import { existsSync, readFileSync, copyFileSync, mkdirSync, statSync } from "node:fs";
import { resolve, basename, dirname, join, isAbsolute } from "node:path";
import type { VideoInput, SceneInput } from "../src/types";

interface JobFile extends VideoInput {
  outputName?: string;
}

const ROOT = resolve(import.meta.dirname, "..");

function abort(code: number, msg: string): never {
  console.error(msg);
  process.exit(code);
}

function ensureAbs(p: string, baseDir: string): string {
  if (!p) return p;
  if (p.startsWith("http://") || p.startsWith("https://")) return p;
  return isAbsolute(p) ? p : resolve(baseDir, p);
}

/**
 * Remotion 加载本地文件用 publicDir + staticFile()。
 * 我们的 job 里全是绝对路径——把每个文件 hardlink/copy 到 jobDir/static/ 下，让 Remotion 能从 publicDir 看到。
 */
function stageStatic(srcAbs: string, staticDir: string, prefix: string): string {
  const base = `${prefix}_${basename(srcAbs)}`;
  const dest = join(staticDir, base);
  if (!existsSync(dest)) {
    copyFileSync(srcAbs, dest);
  }
  return base; // 相对 staticDir 的文件名（staticFile() 能直接用）
}

async function main() {
  const jobDir = process.argv[2];
  if (!jobDir) abort(2, "用法: render-from-job.ts <job_dir>");
  const jobDirAbs = isAbsolute(jobDir) ? jobDir : resolve(process.cwd(), jobDir);
  if (!existsSync(jobDirAbs) || !statSync(jobDirAbs).isDirectory()) {
    abort(2, `job 目录不存在: ${jobDirAbs}`);
  }

  const inputPath = join(jobDirAbs, "input.json");
  if (!existsSync(inputPath)) abort(2, `缺 input.json: ${inputPath}`);
  let job: JobFile;
  try {
    job = JSON.parse(readFileSync(inputPath, "utf-8")) as JobFile;
  } catch (e) {
    abort(2, `input.json 解析失败: ${(e as Error).message}`);
  }

  if (!job.scenes || job.scenes.length === 0) {
    abort(1, "scenes 为空");
  }

  // ── 1. 把所有 asset / audio / bgm 拷到 jobDir/static/ ──
  const staticDir = join(jobDirAbs, "static");
  mkdirSync(staticDir, { recursive: true });

  const stagedScenes: SceneInput[] = job.scenes.map((sc, i) => {
    const assetAbs = ensureAbs(sc.asset, jobDirAbs);
    const audioAbs = ensureAbs(sc.audio, jobDirAbs);
    const assetName = sc.asset.startsWith("http")
      ? sc.asset
      : stageStatic(assetAbs, staticDir, `scene_${i + 1}_asset`);
    const audioName = sc.audio.startsWith("http")
      ? sc.audio
      : stageStatic(audioAbs, staticDir, `scene_${i + 1}_audio`);
    return { ...sc, asset: assetName, audio: audioName };
  });

  let bgmName: string | undefined;
  if (job.bgm) {
    const bgmAbs = ensureAbs(job.bgm, jobDirAbs);
    bgmName = job.bgm.startsWith("http")
      ? job.bgm
      : stageStatic(bgmAbs, staticDir, "bgm");
  }

  const inputProps: VideoInput = {
    title: job.title || "",
    scenes: stagedScenes,
    fps: job.fps || 30,
    width: job.width || 1080,
    height: job.height || 1920,
    bgm: bgmName,
    bgmVolume: job.bgmVolume ?? 0.2,
  };

  // ── 2. bundle Remotion ──
  console.log("PROGRESS=0.05  bundling");
  const serveUrl = await bundle({
    entryPoint: resolve(ROOT, "src", "index.ts"),
    publicDir: staticDir, // ⭐ 关键：让 staticFile() 指向这个 job 自己的目录
  });

  // ── 3. selectComposition + renderMedia ──
  console.log("PROGRESS=0.15  composition");
  const composition = await selectComposition({
    serveUrl,
    id: "video",
    inputProps: inputProps as unknown as Record<string, unknown>,
  });

  const outName = job.outputName || "output.mp4";
  const outPath = join(jobDirAbs, outName);

  console.log(`PROGRESS=0.20  rendering ${composition.durationInFrames} frames`);
  await renderMedia({
    composition,
    serveUrl,
    codec: "h264",
    outputLocation: outPath,
    inputProps: inputProps as unknown as Record<string, unknown>,
    concurrency: 2,
    onProgress: ({ progress }) => {
      // 渲染阶段映射到 0.20 → 0.98
      const p = 0.20 + progress * 0.78;
      process.stdout.write(`\rPROGRESS=${p.toFixed(3)}  rendering ${(progress * 100).toFixed(1)}%`);
    },
  });
  console.log(`\nPROGRESS=1.0  done`);
  console.log(`OUTPUT=${outPath}`);
}

main().catch((e) => {
  console.error("RENDER_FAILED:", e?.stack || e);
  process.exit(1);
});
