"""短视频生成模块。

数据流：
  topic (+ 可选 narrations + 可选 用户上传图片)
    → LLM 生 narration（复用 prompts.yaml.video_narration，与 xhs/douyin 风格一致）
    → edge-tts 给每段生 mp3
    → 拷图（用户提供的优先，不够 / 没提供从 assets/video-defaults/ 补）
    → 调 publisher-hub/remotion-renderer 的 Node 入口渲染 mp4
    → 更新 hub_video_jobs 表（status=done, output_path=...）

对外接口：
  VideoGenPipeline.run_job(job_id, db, config, prompts) — 同步阻塞跑完一条任务
                                                          调用方负责丢到后台线程
"""
from .pipeline import VideoGenPipeline, VideoJobError

__all__ = ['VideoGenPipeline', 'VideoJobError']
