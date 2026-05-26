/**
 * 单个场景 = Ken Burns 背景图 + 字幕 + scene audio。
 * 父 <Sequence> 控制起止帧。
 */
import React from "react";
import { Audio, staticFile } from "remotion";
import { KenBurnsImage } from "./KenBurnsImage";
import { Subtitle } from "./Subtitle";
import type { SceneInput } from "./types";

interface Props {
  scene: SceneInput;
  durationInFrames: number;
  /** 第几个场景，用来交替 Ken Burns 方向 */
  index: number;
}

const PANS = ["lr", "rl", "tb", "bt"] as const;

export const Scene: React.FC<Props> = ({ scene, durationInFrames, index }) => {
  const pan = PANS[index % PANS.length];
  const audioUrl =
    scene.audio.startsWith("/") || scene.audio.startsWith("http")
      ? scene.audio
      : staticFile(scene.audio);

  return (
    <>
      <KenBurnsImage
        src={scene.asset}
        durationInFrames={durationInFrames}
        pan={pan}
      />
      <Subtitle text={scene.narration} durationInFrames={durationInFrames} />
      <Audio src={audioUrl} />
    </>
  );
};
