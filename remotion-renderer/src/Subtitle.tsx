/**
 * 字幕条：把整段 narration 按汉字数均分到 durationInFrames，逐字渐显。
 *
 * 这是过渡方案——精度够日常视频用。要做"卡拉 OK 风"精确逐词高亮，后续把
 * @remotion/install-whisper-cpp 接进来按真实 timestamp 切就行。
 */
import React from "react";
import { useCurrentFrame, interpolate } from "remotion";

interface Props {
  text: string;
  durationInFrames: number;
  /** 字号（px），默认 56 */
  fontSize?: number;
  /** 高亮色，默认黄 */
  highlightColor?: string;
  /** 待显示色，默认半透白 */
  dimColor?: string;
}

/** 把中文/英文混合文本切成"显示单元"。中文一字一切，英文按空格切。*/
function splitIntoChunks(text: string): string[] {
  const out: string[] = [];
  let buf = "";
  for (const ch of text) {
    if (/[一-鿿　-〿＀-￯]/.test(ch)) {
      if (buf) { out.push(buf); buf = ""; }
      out.push(ch);
    } else if (ch === " ") {
      if (buf) { out.push(buf); buf = ""; }
    } else {
      buf += ch;
    }
  }
  if (buf) out.push(buf);
  return out;
}

export const Subtitle: React.FC<Props> = ({
  text,
  durationInFrames,
  fontSize = 56,
  highlightColor = "#FFD93D",
  dimColor = "rgba(255,255,255,0.45)",
}) => {
  const frame = useCurrentFrame();
  const chunks = splitIntoChunks(text);
  if (chunks.length === 0) return null;

  // 起始静音 + 结尾留白都吃 8 帧，避免字幕跟音频边界完全贴合显得僵硬
  const startPad = 6;
  const endPad = 6;
  const liveFrames = Math.max(1, durationInFrames - startPad - endPad);
  const perChunk = liveFrames / chunks.length;
  // 当前应该亮到第几个
  const litCount = Math.max(0, Math.min(chunks.length, Math.floor((frame - startPad) / perChunk) + 1));

  return (
    <div
      style={{
        position: "absolute",
        left: 60,
        right: 60,
        bottom: 140,
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        alignItems: "flex-end",
        rowGap: 8,
        columnGap: 4,
        fontFamily: "'PingFang SC','Source Han Sans','Microsoft YaHei',sans-serif",
        fontWeight: 800,
        fontSize,
        lineHeight: 1.25,
        textShadow: "0 4px 16px rgba(0,0,0,0.85)",
        WebkitTextStroke: "1.5px rgba(0,0,0,0.6)",
      }}
    >
      {chunks.map((c, i) => {
        const lit = i < litCount;
        // 单字渐显（最近亮起的字给个柔和的 fade）
        const fadeWin = perChunk * 0.6;
        const localFrame = frame - startPad - i * perChunk;
        const opacity = lit
          ? interpolate(localFrame, [0, fadeWin], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            })
          : 0.25;
        return (
          <span
            key={i}
            style={{
              color: lit ? highlightColor : dimColor,
              opacity,
              transition: "color 0.06s ease-out",
            }}
          >
            {c}
          </span>
        );
      })}
    </div>
  );
};
