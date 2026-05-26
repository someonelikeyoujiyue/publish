# remotion-renderer

publisher-hub 视频渲染子项目。给 Python 端的 `publisher_hub.video_gen` 当 subprocess 用。

## 安装

```bash
cd publisher-hub/remotion-renderer
npm install
```

第一次 `selectComposition` / `renderMedia` 会自动下载 ~85MB 的 Chrome Headless Shell（Remotion 自带，不污染系统 Chrome），缓存路径见 Remotion 文档。

服务器（Linux）部署时如果系统没装 Chromium 依赖会自动拉，但要确保以下包齐：

```
libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1
libxfixes3 libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 libgtk-3-0
```

## 用法

### 1. 手动 sample（一键演示，用本地 RSU 默认图 + edge-tts）

```bash
npm run render:sample
# 产物：output/sample.mp4
```

### 2. 从 job 目录渲染（生产 pipeline 走这条）

```bash
npm run render:job /path/to/data/video-jobs/<job_id>
```

job 目录里需要预先准备好的 `input.json`：见 `scripts/render-from-job.ts` 顶部注释的格式。
渲染产物：`<job_dir>/output.mp4`。

## 架构

```
src/
├── index.ts           registerRoot
├── Root.tsx           <Composition id="video">  动态 calculateMetadata
├── Video.tsx          顶层组件：Sequence 拼接 + BGM
├── Scene.tsx          单 scene = KenBurnsImage + Subtitle + Audio
├── KenBurnsImage.tsx  缓推 + 4 方向交替平移
├── Subtitle.tsx       逐字渐显 + 高亮（按音频时长均分）
├── Title.tsx          顶部 spring 标题
└── types.ts           VideoInput / SceneInput 数据契约
```

字幕精度：当前按字数对音频时长均分。要升级到 word-level 精确同步可接 `@remotion/install-whisper-cpp`。
