/**
 * 顶部标题：前 1.2s 缩放+淡入，居中显示。
 */
import React from "react";
import { useCurrentFrame, useVideoConfig, interpolate, spring } from "remotion";

interface Props {
  text: string;
}

export const Title: React.FC<Props> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 14 } });
  const opacity = interpolate(frame, [0, fps * 0.5], [0, 1], { extrapolateRight: "clamp" });
  const scale = interpolate(s, [0, 1], [0.85, 1]);

  if (!text) return null;
  return (
    <div
      style={{
        position: "absolute",
        top: 90,
        left: 60,
        right: 60,
        textAlign: "center",
        color: "#fff",
        opacity,
        transform: `scale(${scale})`,
        fontFamily: "'PingFang SC','Source Han Sans','Microsoft YaHei',sans-serif",
        fontWeight: 900,
        fontSize: 80,
        lineHeight: 1.15,
        textShadow: "0 6px 24px rgba(0,0,0,0.75)",
        WebkitTextStroke: "2px rgba(0,0,0,0.55)",
        letterSpacing: 2,
      }}
    >
      {text}
    </div>
  );
};
