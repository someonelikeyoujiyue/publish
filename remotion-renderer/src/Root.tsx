/**
 * Remotion root：暴露一个 'video' composition。
 * 实际 props 在渲染时通过 inputProps 注入（见 scripts/render-sample.ts）。
 */
import React from "react";
import { Composition, getInputProps } from "remotion";
import { Video } from "./Video";
import type { VideoInput } from "./types";

const DEFAULT_INPUT: VideoInput = {
  title: "示例视频",
  fps: 30,
  width: 1080,
  height: 1920,
  scenes: [],
};

// 实际 props 由 renderMedia 通过 inputProps 传；这里只是给 studio 预览用的占位。
const studioInput = getInputProps() as Partial<VideoInput>;
const seed: VideoInput = { ...DEFAULT_INPUT, ...studioInput };

// 计算总帧数（默认占位时给 1 帧避免崩）
const totalFrames = Math.max(
  1,
  seed.scenes.reduce((acc, s) => acc + Math.round(s.durationSec * seed.fps), 0)
);

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="video"
      // Remotion 的 LooseComponentType 要求 props 是 Record<string,unknown>。
      // 我们的 VideoInput 是具名 interface，这里走一次 unknown 强转——运行期一致。
      component={Video as unknown as React.FC<Record<string, unknown>>}
      durationInFrames={totalFrames}
      fps={seed.fps}
      width={seed.width}
      height={seed.height}
      defaultProps={seed as unknown as Record<string, unknown>}
      calculateMetadata={({ props }) => {
        const p = props as unknown as VideoInput;
        const frames = Math.max(
          1,
          p.scenes.reduce((acc, s) => acc + Math.round(s.durationSec * p.fps), 0)
        );
        return {
          durationInFrames: frames,
          fps: p.fps,
          width: p.width,
          height: p.height,
        };
      }}
    />
  );
};
