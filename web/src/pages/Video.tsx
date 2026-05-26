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
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { canWrite, isAdmin } from '@/lib/auth'
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
  const admin = isAdmin()
  const editor = canWrite()

  // 表单状态
  const [topic, setTopic]           = useState('')
  const [title, setTitle]           = useState('')
  const [narrations, setNarrations] = useState('')
  const [nScenes, setNScenes]       = useState(3)
  const [voice, setVoice]           = useState('zh-xiaoxiao-female')
  const [rate, setRate]             = useState('+5%')
  const [images, setImages]         = useState<File[]>([])
  const [err, setErr]               = useState<string>('')

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
        images,
      }),
    onSuccess: () => {
      setTopic(''); setTitle(''); setNarrations(''); setImages([])
      setErr('')
      qc.invalidateQueries({ queryKey: ['video-jobs', userId] })
    },
    onError: (e) => setErr((e as Error).message),
  })

  const del = useMutation({
    mutationFn: (jobId: number) => api.videoDelete(userId, jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['video-jobs', userId] }),
  })

  const canSubmit = useMemo(() => {
    const hasTopic = topic.trim().length > 0
    const hasNarr = narrations.split('\n').filter((l) => l.trim()).length > 0
    return (hasTopic || hasNarr) && !submit.isPending
  }, [topic, narrations, submit.isPending])

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

          <Field label="文案 narrations" hint="每行一段；留空 = LLM 按 xhs/douyin 风格生 N 段">
            <textarea
              value={narrations}
              onChange={(e) => setNarrations(e.target.value)}
              placeholder={
                '例（不写就让 LLM 写）：\n兰实大学位于泰国曼谷北部。\n本科学费 3-8 万人民币每年。\n中国教育部认证 19 年零中断。'
              }
              rows={5}
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
            <Field label="音色" hint="">
              <select
                value={voice}
                onChange={(e) => setVoice(e.target.value)}
                className={inputCls}
              >
                {(opts?.voices ?? [{ key: 'zh-xiaoxiao-female', code: 'zh-CN-XiaoxiaoNeural' }]).map(v => (
                  <option key={v.key} value={v.key}>{v.key}</option>
                ))}
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

          <Field label="图片" hint="0-N 张；留空 = 用默认 RSU 校园图凑数">
            <input
              type="file"
              accept="image/*"
              multiple
              onChange={(e) => setImages(Array.from(e.target.files || []))}
              className="block text-sm"
            />
            {images.length > 0 && (
              <p className="text-xs text-slate-500 mt-1">已选 {images.length} 张</p>
            )}
          </Field>

          {err && <Flash tone="error">提交失败：{err}</Flash>}

          <div className="flex gap-2 pt-2">
            <Btn
              onClick={() => submit.mutate()}
              loading={submit.isPending}
              disabled={!canSubmit}
            >
              {submit.isPending ? '提交中…' : '🚀 生成视频'}
            </Btn>
            <Btn variant="ghost" onClick={() => {
              setTopic(''); setTitle(''); setNarrations(''); setImages([]); setErr('')
            }}>
              清空
            </Btn>
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
                  {admin && (
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
