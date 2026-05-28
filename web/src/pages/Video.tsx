/**
 * 短视频生成页面：上方表单 + 下方任务列表。
 *
 * 表单字段（全部可选 / 部分可选；至少要给 topic 或 narrations）：
 *   - 话题 topic       (text)        必填，给 LLM 生文案的种子；如果填了 narrations 也可以留空但建议给
 *   - 标题 title       (text)        视频顶部标题，留空 LLM 自己起
 *   - 文案 narrations  (textarea)    每行 1 段；留空 = 让 LLM 生 N 段
 *   - 段数 n_scenes    (number)      只在 narrations 留空时生效；2-6
 *   - 音色 voice       (select)      下拉
 *   - 语速 rate        (text)        "+5%" / "-10%" / "+0%" 这种
 *   - 图片 images      (file 多选)   留空 = 用默认 RSU 图
 *
 * 任务列表轮询：active 时（pending/processing）5s 刷一次。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { canWrite } from '@/lib/auth'
import { Btn, Card, Badge, Empty, Flash } from '@/components/ui'

const STATUS_LABEL = {
  pending:    { text: '⏳ 排队中',  kind: 'ready'  as const },
  processing: { text: '⏳ 处理中',  kind: 'ready'  as const },
  done:       { text: '✓ 已完成',  kind: 'pushed' as const },
  failed:     { text: '✗ 失败',    kind: 'failed' as const },
}

export function Video() {
  const { userId = '' } = useParams()
  const qc = useQueryClient()
  const editor = canWrite()
  // 视频任务删除：editor / admin 都可（user 不行）
  const canDelete = editor

  // 表单状态
  const [topic, setTopic]           = useState('')
  const [title, setTitle]           = useState('')
  const [narrations, setNarrations] = useState('')
  const [nScenes, setNScenes]       = useState(3)
  const [voice, setVoice]           = useState('zh-xiaoxiao-female')
  const [rate, setRate]             = useState('+5%')
  const [bgmMode, setBgmMode]       = useState<'default' | 'upload' | 'none'>('default')
  const [bgmFile, setBgmFile]       = useState<File | null>(null)
  const [images, setImages]         = useState<File[]>([])
  const [err, setErr]               = useState<string>('')
  // preview 结果：跑过一次「✨ 生成文案预览图」后非空；提交视频后清空
  const [preview, setPreview] = useState<{
    narrations: string[]
    image_urls: string[]
    source: string
  } | null>(null)

  // 选项 + 任务列表
  const { data: opts } = useQuery({
    queryKey: ['video-options'],
    queryFn: () => api.videoOptions(),
    staleTime: 60_000,
  })
  useEffect(() => {
    if (opts?.default_voice && voice === 'zh-xiaoxiao-female') setVoice(opts.default_voice)
    if (opts?.default_rate && rate === '+5%') setRate(opts.default_rate)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts])

  const { data: jobsResp, isLoading } = useQuery({
    queryKey: ['video-jobs', userId],
    queryFn: () => api.videoJobs(userId),
    enabled: !!userId,
    refetchInterval: (q) => {
      const jobs = (q.state.data as { jobs: { status: string }[] } | undefined)?.jobs
      return jobs?.some((j) => j.status === 'pending' || j.status === 'processing') ? 5_000 : false
    },
  })

  const submit = useMutation({
    mutationFn: () =>
      api.videoSubmit(userId, {
        topic: topic.trim(),
        title: title.trim(),
        narrations: narrations,
        n_scenes: nScenes,
        voice,
        rate,
        bgm_mode: bgmMode,
        bgm_file: bgmMode === 'upload' ? bgmFile : null,
        images,
      }),
    onSuccess: () => {
      setTopic(''); setTitle(''); setNarrations(''); setImages([])
      setBgmFile(null); setBgmMode('default')
      setPreview(null); setErr('')
      qc.invalidateQueries({ queryKey: ['video-jobs', userId] })
    },
    onError: (e) => setErr((e as Error).message),
  })

  // 预览：跑 LLM 拿 narration + 默认图但不渲染
  const previewMut = useMutation({
    mutationFn: () =>
      api.videoPreview(userId, {
        topic: topic.trim(),
        narrations: narrations,
        n_scenes: nScenes,
      }),
    onSuccess: (data) => {
      // 把 LLM 生的 narration 回填到 textarea（用户可以再改）
      setNarrations(data.narrations.join('\n'))
      if (data.title && !title.trim()) setTitle(data.title)
      if (data.topic && !topic.trim()) setTopic(data.topic)
      setPreview({
        narrations: data.narrations,
        image_urls: data.default_image_urls,
        source: data.source,
      })
      setErr('')
    },
    onError: (e) => setErr((e as Error).message),
  })

  const del = useMutation({
    mutationFn: (jobId: number) => api.videoDelete(userId, jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['video-jobs', userId] }),
  })

  // 生成视频按钮：要求 narrations 或图片至少一个有内容（topic 不够，必须有最终文案）
  // 用户必须先走「✨ 生成文案预览图」或者自己手填 narrations / 上传图片
  const hasNarr = narrations.split('\n').filter((l) => l.trim()).length > 0
  const canSubmit = !submit.isPending && (hasNarr || images.length > 0)
  const canPreview = !previewMut.isPending && !submit.isPending

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  const jobs = jobsResp?.jobs ?? []

  const inputCls =
    'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50'

  return (
    <>
      {/* ── 提交表单 ───────────────────────────────────────────── */}
      <div className="bg-white border border-slate-200 rounded-lg p-5 mb-5">
        <h2 className="text-lg font-semibold mb-1">🎬 生成短视频</h2>
        <p className="text-xs text-slate-500 mb-4">
          1080×1920 抖音/视频号格式；30s 内 / 3-5 个场景。免费 edge-tts 配音、Remotion 渲染。
          后台异步跑（约 1-3 分钟），下方列表自动刷新。
        </p>

        {!editor && (
          <Flash tone="info">
            你当前是只读账号，看得到任务但不能提交。改用 admin / lanshi 账号登录。
          </Flash>
        )}

        <fieldset disabled={!editor || submit.isPending} className="space-y-3">
          <Field label="话题（topic）" hint="LLM 用它生文案的种子；如果下方填了文案这里可以留空">
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="例：兰实大学留学指南 / 4 年 30 万本科 / 19 年零中断认证"
              className={inputCls}
            />
          </Field>

          <Field label="标题" hint="顶部大字；留空 LLM 自动起">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="例：19 年零中断认证 兰实留学的安全账本"
              className={inputCls}
            />
          </Field>

          <Field
            label="文案 narrations"
            hint="每行一段；粘一大坨也行，后端会按句切；留空 = LLM 生"
          >
            <textarea
              value={narrations}
              onChange={(e) => setNarrations(e.target.value)}
              placeholder={
                '可以每行一段：\n  兰实大学位于泰国曼谷北部。\n  本科学费 3-8 万人民币每年。\n\n或直接粘一整段，后端按 句号/分号/逗号 自动切成 ≤30 字的短句\n（长视频长配音都没问题，时长不限）'
              }
              rows={6}
              className={inputCls + ' font-mono'}
            />
          </Field>

          <div className="grid grid-cols-3 gap-3">
            <Field label="段数" hint="文案留空时生效">
              <input
                type="number" min={2} max={6}
                value={nScenes}
                onChange={(e) => setNScenes(Math.max(2, Math.min(6, parseInt(e.target.value) || 3)))}
                className={inputCls}
              />
            </Field>
            <Field label="音色" hint="选「无配音」= 只留 BGM">
              <select
                value={voice}
                onChange={(e) => setVoice(e.target.value)}
                className={inputCls}
              >
                {(opts?.voices ?? [{ key: 'zh-xiaoxiao-female', code: 'zh-CN-XiaoxiaoNeural' }]).map(v => (
                  <option key={v.key} value={v.key}>{v.key}</option>
                ))}
                <option value="none">🔇 无配音（按字数控时长）</option>
              </select>
            </Field>
            <Field label="语速 rate" hint="+5% / -10% / +0%">
              <input
                value={rate}
                onChange={(e) => setRate(e.target.value)}
                className={inputCls}
              />
            </Field>
          </div>

          <Field label="🖼️ 上传图片" hint="0-N 张；留空 = 用默认 RSU 校园图凑数">
            <ImageDropzone images={images} setImages={setImages} />
          </Field>

          <Field label="🎵 背景音乐" hint="">
            <div className="flex gap-3 items-center text-sm">
              {(['default', 'upload', 'none'] as const).map(m => (
                <label key={m} className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    name="bgm_mode"
                    checked={bgmMode === m}
                    onChange={() => setBgmMode(m)}
                    className="accent-brand-600"
                  />
                  {m === 'default' && '默认 BGM'}
                  {m === 'upload' && '上传 BGM'}
                  {m === 'none' && '不要 BGM'}
                </label>
              ))}
            </div>
            {bgmMode === 'upload' && (
              <div className="mt-2">
                <input
                  type="file"
                  accept="audio/*"
                  onChange={(e) => setBgmFile((e.target.files && e.target.files[0]) || null)}
                  className="block text-sm"
                />
                {bgmFile && (
                  <p className="text-xs text-slate-500 mt-1">
                    已选：{bgmFile.name}（{(bgmFile.size / 1024 / 1024).toFixed(2)} MB）
                  </p>
                )}
              </div>
            )}
          </Field>

          {/* preview 结果展示 */}
          {preview && (
            <div className="border border-emerald-200 bg-emerald-50/40 rounded-lg p-3">
              <div className="text-sm font-semibold text-emerald-800 mb-2">
                ✨ 已生成预览（来源：{preview.source}）—— 可修改文案后再点「生成视频」
              </div>
              {preview.image_urls.length > 0 && (
                <div>
                  <div className="text-xs text-slate-600 mb-1.5">
                    将使用的图片（如果你不另传，就用这些）：
                  </div>
                  <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
                    {preview.image_urls.map((u, i) => (
                      <div
                        key={i}
                        className="aspect-[9/16] rounded overflow-hidden border border-slate-200 bg-slate-100 relative"
                      >
                        <img src={u} alt="" className="w-full h-full object-cover" />
                        <div className="absolute top-0.5 left-0.5 bg-black/60 text-white text-[10px] font-semibold px-1.5 py-0.5 rounded">
                          {i + 1}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {err && <Flash tone="error">{err}</Flash>}

          <div className="flex gap-2 pt-2 flex-wrap">
            <Btn
              variant="secondary"
              onClick={() => previewMut.mutate()}
              loading={previewMut.isPending}
              disabled={!canPreview}
            >
              {previewMut.isPending ? 'LLM 生成中…（10-30s）' : '✨ 生成文案 + 预览图'}
            </Btn>
            <Btn
              onClick={() => submit.mutate()}
              loading={submit.isPending}
              disabled={!canSubmit}
              title={!canSubmit ? '需要先有文案或上传图片（建议先点「生成文案+预览图」）' : ''}
            >
              {submit.isPending ? '提交中…' : '🚀 生成视频'}
            </Btn>
            <Btn variant="ghost" onClick={() => {
              setTopic(''); setTitle(''); setNarrations(''); setImages([])
              setBgmFile(null); setBgmMode('default'); setPreview(null); setErr('')
            }}>
              清空
            </Btn>
            {!canSubmit && !submit.isPending && (
              <p className="text-xs text-slate-500 self-center w-full">
                💡 「生成视频」需要先有 narration 或上传图。可以手填，也可以点「生成文案+预览图」让 LLM 生。
              </p>
            )}
          </div>
        </fieldset>
      </div>

      {/* ── 任务列表 ───────────────────────────────────────────── */}
      <h3 className="text-base font-semibold mb-3">
        我的视频任务
        <span className="ml-2 text-[13px] text-slate-400 font-normal">{jobs.length} 条</span>
      </h3>

      {jobs.length === 0 ? (
        <Empty icon="🎥" title="还没有视频任务" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {jobs.map((j) => {
            const s = STATUS_LABEL[j.status] || STATUS_LABEL.pending
            return (
              <Card key={j.id} hover>
                <div className="flex gap-2 mb-3 items-center">
                  <Badge kind={s.kind}>{s.text}</Badge>
                  <span className="ml-auto text-[11px] text-slate-400">{j.created_at.slice(0, 16)}</span>
                </div>
                <div className="text-[15px] font-semibold text-slate-800 line-clamp-2 mb-2">
                  {j.title || j.topic || '(未命名)'}
                </div>
                {j.topic && j.title && j.topic !== j.title && (
                  <div className="text-xs text-slate-500 mb-2">话题：{j.topic}</div>
                )}
                {j.narrations.length > 0 && (
                  <div className="text-xs text-slate-600 mb-2 space-y-0.5">
                    {j.narrations.map((n, i) => <div key={i}>{i + 1}. {n}</div>)}
                  </div>
                )}
                {j.image_count > 0 && (
                  <div className="text-[11px] text-slate-400 mb-2">📷 {j.image_count} 张图</div>
                )}
                {j.status === 'failed' && j.error && (
                  <div className="text-xs text-red-700 bg-red-50 px-2 py-1 rounded mb-2">
                    {j.error.slice(0, 200)}
                  </div>
                )}
                {j.status === 'done' && j.video_url && (
                  <>
                    <video
                      src={j.video_url}
                      controls
                      preload="metadata"
                      className="w-full bg-black rounded mb-2"
                      style={{ aspectRatio: '9/16', maxHeight: 360 }}
                    />
                    <div className="text-[11px] text-slate-500 mb-2">
                      {j.duration_sec ? `${j.duration_sec.toFixed(1)}s · ` : ''}
                      {j.file_size ? `${(j.file_size / 1024 / 1024).toFixed(1)} MB` : ''}
                    </div>
                  </>
                )}
                <div className="flex gap-2 pt-3 border-t border-slate-100">
                  {j.video_url && (
                    <a
                      href={j.video_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="px-3 py-1.5 text-sm bg-brand-50 text-brand-700 rounded hover:bg-brand-100"
                    >
                      在新标签打开
                    </a>
                  )}
                  {j.video_url && (
                    <a
                      href={j.video_url}
                      download
                      className="px-3 py-1.5 text-sm text-slate-600 border border-slate-300 rounded hover:bg-slate-50"
                    >
                      ⬇ 下载
                    </a>
                  )}
                  {canDelete && (
                    <button
                      onClick={() => {
                        if (confirm(`确认删除任务 #${j.id} 及视频文件？`)) del.mutate(j.id)
                      }}
                      disabled={j.status === 'processing'}
                      className="ml-auto px-3 py-1.5 text-sm text-red-700 border border-red-200 rounded hover:bg-red-50 disabled:opacity-50"
                    >
                      🗑 删除
                    </button>
                  )}
                </div>
              </Card>
            )
          })}
        </div>
      )}

    </>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="flex justify-between items-baseline mb-1">
        <span className="text-sm text-slate-700 font-medium">{label}</span>
        {hint && <span className="text-xs text-slate-400">{hint}</span>}
      </label>
      {children}
    </div>
  )
}

/** 拖拽 + 点击的图片上传区，带缩略图预览 + 单张删除。 */
function ImageDropzone({ images, setImages }: { images: File[]; setImages: (fs: File[]) => void }) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  // 给每张图算一个对象 URL 做预览。images 变了重建（旧 URL revoke 防内存泄漏）。
  const previews = useMemo(() => images.map((f) => URL.createObjectURL(f)), [images])
  useEffect(() => {
    return () => previews.forEach((u) => URL.revokeObjectURL(u))
  }, [previews])

  const addFiles = (incoming: File[]) => {
    const imgs = incoming.filter((f) => f.type.startsWith('image/'))
    if (imgs.length === 0) return
    setImages([...images, ...imgs])
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    addFiles(Array.from(e.dataTransfer.files || []))
  }

  const removeAt = (i: number) => {
    setImages(images.filter((_, idx) => idx !== i))
  }

  return (
    <div>
      {/* 隐藏 input，点 zone 触发 */}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        onChange={(e) => {
          addFiles(Array.from(e.target.files || []))
          // reset 让同一文件能重选
          if (inputRef.current) inputRef.current.value = ''
        }}
        className="hidden"
      />

      <div
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true) }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
        className={`
          relative cursor-pointer rounded-lg border-2 border-dashed
          px-6 py-8 text-center transition select-none
          ${isDragging
            ? 'border-brand-600 bg-brand-50'
            : 'border-slate-300 bg-slate-50 hover:border-brand-500 hover:bg-brand-50/40'}
        `}
      >
        <div className="text-4xl mb-2">📸</div>
        <div className="text-base font-semibold text-slate-700 mb-1">
          {isDragging ? '松开鼠标添加图片' : '点击或拖拽图片到这里'}
        </div>
        <div className="text-xs text-slate-500">
          支持 JPG / PNG / WebP；多选；空着也能跑（自动用默认 RSU 校园图）
        </div>
        {images.length > 0 && (
          <div className="absolute top-3 right-3 bg-brand-600 text-white text-xs font-semibold rounded-full px-2.5 py-1">
            已选 {images.length} 张
          </div>
        )}
      </div>

      {/* 缩略图网格 */}
      {images.length > 0 && (
        <div className="grid grid-cols-4 sm:grid-cols-6 gap-2 mt-3">
          {previews.map((url, i) => (
            <div
              key={i}
              className="relative group aspect-[9/16] rounded overflow-hidden border border-slate-200 bg-slate-100"
            >
              <img src={url} alt="" className="w-full h-full object-cover" />
              <div className="absolute top-0.5 left-0.5 bg-black/60 text-white text-[10px] font-semibold px-1.5 py-0.5 rounded">
                {i + 1}
              </div>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); removeAt(i) }}
                className="absolute top-0.5 right-0.5 bg-red-600 hover:bg-red-700 text-white w-5 h-5 rounded-full text-xs leading-none flex items-center justify-center opacity-0 group-hover:opacity-100 transition"
                title="移除"
              >
                ×
              </button>
              <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent text-white text-[10px] px-1.5 py-1 truncate">
                {images[i].name}
              </div>
            </div>
          ))}
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="aspect-[9/16] rounded border-2 border-dashed border-slate-300 hover:border-brand-500 hover:bg-brand-50/40 flex flex-col items-center justify-center text-slate-400 hover:text-brand-600 transition"
          >
            <span className="text-2xl mb-0.5">+</span>
            <span className="text-[10px]">继续添加</span>
          </button>
        </div>
      )}
    </div>
  )
}
