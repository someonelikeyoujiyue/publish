"""subprocess 调 publisher-hub/remotion-renderer 的 render-from-job.ts。

不走 HTTP 服务（多一个进程要管），直接 spawn node 一次性渲染。
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger('publisher_hub.video_gen.remotion')


def _renderer_root() -> Path:
    """publisher-hub/remotion-renderer/ 绝对路径。"""
    here = Path(__file__).resolve()
    # here = publisher-hub/publisher_hub/video_gen/remotion.py
    return here.parent.parent.parent / 'remotion-renderer'


def render_job(job_dir: Path, timeout_seconds: int = 1800) -> Path:
    """从 job_dir 渲染视频，返回输出 mp4 路径。

    要求 job_dir 里已经写好 input.json + 所有素材文件（图、音频、bgm 也都已 stage 进去）。

    Raises:
        RuntimeError: node 子进程失败 / 超时 / 找不到 output.mp4
    """
    renderer = _renderer_root()
    if not (renderer / 'node_modules').exists():
        raise RuntimeError(
            f'{renderer}/node_modules 不存在；请在该目录跑 `npm install`'
        )

    log.info('[remotion] 渲染 job=%s', job_dir.name)

    # 用 npm run 而不是直接 node：避免 ESM / tsx 路径问题
    proc = subprocess.run(
        ['npm', 'run', '--silent', 'render:job', '--', str(job_dir)],
        cwd=str(renderer),
        capture_output=True, text=True, timeout=timeout_seconds,
        env={**os.environ, 'NODE_OPTIONS': '--max-old-space-size=4096'},
    )

    # stdout 含 PROGRESS=... 和 OUTPUT=... 行；全量也打到 log
    log.info('[remotion] stdout 尾部:\n%s', proc.stdout[-1500:])
    if proc.stderr:
        log.warning('[remotion] stderr:\n%s', proc.stderr[-1500:])

    if proc.returncode != 0:
        raise RuntimeError(
            f'remotion 渲染失败 returncode={proc.returncode}\n'
            f'stderr: {proc.stderr[-500:]}'
        )

    out_path = job_dir / 'output.mp4'
    if not out_path.exists():
        raise RuntimeError(f'渲染似乎成功但没产生 {out_path}')

    log.info('[remotion] ✓ %s (%.1f MB)', out_path, out_path.stat().st_size / 1024 / 1024)
    return out_path
