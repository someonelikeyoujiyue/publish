import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { isAdmin } from '@/lib/auth'
import { Btn, Card, Badge, Empty, Flash } from '@/components/ui'

const STATUS_MAP: Record<string, { text: string; kind: 'ready' | 'pushed' | 'failed' }> = {
  ready:      { text: '待处理',  kind: 'ready' },
  processing: { text: '⏳ 处理中', kind: 'ready' },
  pushed:     { text: '✓ 已完成', kind: 'pushed' },
  failed:     { text: '✗ 失败',   kind: 'failed' },
}

export function Youtube() {
  const { userId = '' } = useParams()
  const qc = useQueryClient()
  const admin = isAdmin()

  const { data, isLoading } = useQuery({
    queryKey: ['drafts', userId, 'youtube'],
    queryFn: () => api.ytList(userId),
    // 有 processing 任务时加速轮询
    refetchInterval: (q) => {
      const drafts = (q.state.data as { drafts: { status: string }[] } | undefined)?.drafts
      return drafts?.some((d) => d.status === 'processing') ? 5_000 : false
    },
  })

  const [form, setForm] = useState({
    url: '',
    strip_hardsub: true,
    blur_qr: false,
  })

  const submit = useMutation({
    mutationFn: () => api.ytSubmit(userId, form),
    onSuccess: () => {
      setForm({ ...form, url: '' })
      qc.invalidateQueries({ queryKey: ['drafts', userId, 'youtube'] })
    },
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  const drafts = data?.drafts ?? []

  const inputCls =
    'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50'

  return (
    <>
      {/* 提交表单（仅 admin） */}
      {admin && (
        <div className="bg-white border border-slate-200 rounded-lg p-5 mb-5">
          <h2 className="text-lg font-semibold mb-3">📺 新建 YouTube 处理任务</h2>
          <p className="text-xs text-slate-500 mb-4 leading-relaxed">
            提交后服务器自动 yt-dlp 下载 1080p 视频 → 抓字幕 → LLM 翻译双语 →
            ffmpeg 合成软挂三轨字幕的 mp4。<b>耗时 5-15 分钟</b>，完成后下方列表自动刷新。
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (form.url.trim()) submit.mutate()
            }}
            className="space-y-3"
          >
            <div>
              <label className="block text-sm text-slate-700 mb-1">YouTube 链接</label>
              <input
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                className={inputCls + ' font-mono'}
                placeholder="https://www.youtube.com/watch?v=..."
                required
                pattern=".*(youtube\.com|youtu\.be).*"
              />
            </div>
            <div className="flex gap-4 flex-wrap">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={form.strip_hardsub}
                  onChange={(e) => setForm({ ...form, strip_hardsub: e.target.checked })}
                  className="accent-brand-600"
                />
                自动去除原片底部硬字幕（智能检测）
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={form.blur_qr}
                  onChange={(e) => setForm({ ...form, blur_qr: e.target.checked })}
                  className="accent-brand-600"
                />
                模糊视频中的二维码
              </label>
            </div>
            {submit.error && <Flash tone="error">{(submit.error as Error).message}</Flash>}
            {submit.data?.ok && (
              <Flash tone="success">
                ✓ 已提交（draft_id={submit.data.draft_id}），下方列表显示 ⏳ 处理中
              </Flash>
            )}
            <Btn type="submit" loading={submit.isPending}>
              🚀 提交处理
            </Btn>
          </form>
        </div>
      )}

      {/* 历史任务列表 */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">
          历史任务
          <span className="ml-2 text-[13px] text-slate-400 font-normal">{drafts.length} 条</span>
        </h2>
      </div>

      {drafts.length === 0 ? (
        <Empty
          icon="📺"
          title={admin ? '还没有 YouTube 处理任务' : '管理员还没提交过 YouTube 处理任务'}
        />
      ) : (
        <div className="space-y-3">
          {drafts.map((d) => {
            const s = STATUS_MAP[d.status] || STATUS_MAP['ready']
            return (
              <Card key={d.id} hover>
                <div className="flex gap-2 mb-2 items-center">
                  <Badge kind={s.kind}>{s.text}</Badge>
                  <Badge kind="platform">YouTube</Badge>
                  <span className="ml-auto text-[11px] text-slate-400">
                    {d.created_at.slice(0, 16)}
                  </span>
                </div>
                <Link
                  to={`/${userId}/youtube/${d.id}`}
                  className="block text-[15px] font-medium text-slate-800 hover:text-brand-700 line-clamp-1 mb-1"
                >
                  {d.title || '(无标题)'}
                </Link>
                <div className="text-[11px] text-slate-500 font-mono truncate mb-2">
                  {d.source_url}
                </div>
                {d.status === 'failed' && d.error && (
                  <div className="text-xs text-red-700 bg-red-50 px-2 py-1 rounded">
                    {d.error.slice(0, 120)}
                  </div>
                )}
                {d.status === 'pushed' && d.video_url && (
                  <div className="flex gap-2">
                    <Link
                      to={`/${userId}/youtube/${d.id}`}
                      className="flex-1 text-center py-1.5 text-sm bg-brand-50 text-brand-700 rounded hover:bg-brand-100"
                    >
                      ▶ 预览
                    </Link>
                    <a
                      href={d.video_url}
                      download
                      className="px-3 py-1.5 text-sm text-slate-600 border border-slate-300 rounded hover:bg-slate-50"
                    >
                      ⬇ 下载
                    </a>
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </>
  )
}
