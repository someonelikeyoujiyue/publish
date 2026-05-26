/**
 * Pipeline → Remotion 之间的数据合约。
 * Pixelle 现在的 storyboard 拍扁成这个形状塞给 Remotion 渲染。
 */
export interface SceneInput {
  /** 静态图片或视频的本地路径 / staticFile() 名字。public/ 下放就行 */
  asset: string;
  assetType?: "image" | "video";
  /** 这一段的旁白文字（用来生成逐词字幕） */
  narration: string;
  /** 对应的 mp3 音频本地路径（edge-tts 产出，扔进 public/）*/
  audio: string;
  /** 这段时长（秒）。后端用 ffprobe 量音频时长后填进来 */
  durationSec: number;
}

export interface VideoInput {
  /** 视频顶部大字标题（可空）*/
  title: string;
  /** 各场景 */
  scenes: SceneInput[];
  /** 帧率 — Remotion 用 frame index，不直接看秒 */
  fps: number;
  /** 输出宽高 */
  width: number;
  height: number;
  /** 可选 BGM（mp3 在 public/）*/
  bgm?: string;
  bgmVolume?: number;
}
