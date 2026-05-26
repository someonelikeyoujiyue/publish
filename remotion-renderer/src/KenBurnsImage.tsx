/**
 * 单张图片的"Ken Burns"运镜：缓推缩放 + 极轻微平移。
 * 替代 Pixelle 现在静态画面的死板感。
 */
import React from "react";
import { Img, useCurrentFrame, useVideoConfig, interpolate, staticFile } from "remotion";

interface Props {
  src: string;
  /** 这段总帧数 */
  durationInFrames: number;
  /** 起始 scale，默认 1.05 */
  startScale?: number;
  /** 结束 scale，默认 1.20 */
  endScale?: number;
  /** 平移方向：'lr' / 'rl' / 'tb' / 'bt' / 'none'。默认 'lr' */
  pan?: "lr" | "rl" | "tb" | "bt" | "none";
}

export const KenBurnsImage: React.FC<Props> = ({
  src,
  durationInFrames,
  startScale = 1.05,
  endScale = 1.20,
  pan = "lr",
}) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();

  const scale = interpolate(
    frame,
    [0, durationInFrames],
    [startScale, endScale],
    { extrapolateRight: "clamp" }
  );

  // 平移量（相对画面短边的百分比；2% 总位移已经能感觉到动感）
  const t = interpolate(frame, [0, durationInFrames], [0, 1], { extrapolateRight: "clamp" });
  const shift = 0.02 * Math.min(width, height);
  let dx = 0, dy = 0;
  if (pan === "lr") dx = -shift + 2 * shift * t;
  if (pan === "rl") dx =  shift - 2 * shift * t;
  if (pan === "tb") dy = -shift + 2 * shift * t;
  if (pan === "bt") dy =  shift - 2 * shift * t;

  // staticFile 接受 public/ 下的相对路径；如果传进来已经是绝对 URL/路径就原样用
  const url = src.startsWith("/") || src.startsWith("http") ? src : staticFile(src);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        background: "#000",
      }}
    >
      <Img
        src={url}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `translate(${dx}px, ${dy}px) scale(${scale})`,
          transformOrigin: "center center",
        }}
      />
      {/* 底部渐黑用于压字幕 */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          bottom: 0,
          height: "45%",
          background:
            "linear-gradient(to bottom, rgba(0,0,0,0) 0%, rgba(0,0,0,0.85) 100%)",
        }}
      />
    </div>
  );
};
