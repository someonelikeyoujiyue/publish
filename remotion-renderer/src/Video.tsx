/**
 * 整段视频：按 scenes 顺序拼接，最上面常驻一个 Title。
 */
import React from "react";
import { AbsoluteFill, Sequence, Audio, staticFile } from "remotion";
import { Title } from "./Title";
import { Scene } from "./Scene";
import type { VideoInput } from "./types";

export const Video: React.FC<VideoInput> = ({ title, scenes, fps, bgm, bgmVolume = 0.2 }) => {
  let cursor = 0;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      {scenes.map((sc, i) => {
        const dur = Math.max(1, Math.round(sc.durationSec * fps));
        const seq = (
          <Sequence key={i} from={cursor} durationInFrames={dur} layout="none">
            <Scene scene={sc} durationInFrames={dur} index={i} />
          </Sequence>
        );
        cursor += dur;
        return seq;
      })}

      <Title text={title} />

      {bgm && (
        <Audio
          src={bgm.startsWith("/") || bgm.startsWith("http") ? bgm : staticFile(bgm)}
          volume={bgmVolume}
        />
      )}
    </AbsoluteFill>
  );
};
