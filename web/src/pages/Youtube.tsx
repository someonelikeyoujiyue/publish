import { useRef, useState } from 'react'
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
  const videoRef         = useRef<HTMLInputElement>(null)
  const biRef            = useRef<HTMLInputElement>(null)
  const enRef            = useRef<HTMLInputElement>(null)
  const zhRef            = useRef<HTMLInputElement>(null)
  const [sourceUrl, setSourceUrl] = useState('')
  const [title, setTitle]         = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['drafts', userId, 'youtube'],
    queryFn: () => api.ytList(userId),
    refetchInterval: (q) => {
      const drafts = (q.state.data as { drafts: { status: string }[] } | undefined)?.drafts
      return drafts?.some((d) => d.status === 'processing') ? 5_000 : false
    },
  })

  const upload = useMutation({
    mutationFn: () => {
      const video = videoRef.current?.files?.[0]
      if (!video) throw new Error('请选择 mp4 视频文件')
      return api.ytUpload(userId, {
        video,
        bilingual_srt: biRef.current?.files?.[0] || null,
        en_srt:        enRef.current?.files?.[0] || null,
        zh_srt:        zhRef.current?.files?.[0] || null,
        source_url:    sourceUrl.trim(),
        title:         title.trim(),
      })
    },
    onSuccess: () => {
      // 清空表单
      if (videoRef.current) videoRef.current.value = ''
      if (biRef.current) biRef.current.value = ''
      if (enRef.current) enRef.current.value = ''
      if (zhRef.current) zhRef.current.value = ''
      setSourceUrl('')
      setTitle('')
      qc.invalidateQueries({ queryKey: ['drafts', userId, 'youtube'] })
    },
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  const drafts = data?.drafts ?? []

  const inputCls =
    'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50'

  return (
    <>
      {/* 上传表单（admin only） */}
      {admin && (
        <div className="bg-white border border-slate-200 rounded-lg p-5 mb-5">
          <h2 className="text-lg font-semibold mb-1">📤 上传本地处理好的视频</h2>
          <p className="text-xs text-slate-500 mb-4 leading-relaxed">
            在你本地用 rangsit 跑完后，把 <code className="bg-slate-100 px-1 rounded">video_final.mp4</code>
            （必传）和 3 个 srt（可选）上传到服务器，<b>立即入库</b>，
            前端运营人员可预览/下载。
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              upload.mutate()
            }}
            className="space-y-3"
          >
            <div>
              <label className="block text-sm text-slate-700 mb-1">
                视频文件 <span className="text-red-500">*</span>
                <span className="text-xs text-slate-400 ml-2">（mp4，含硬烧字幕）</span>
              </label>
              <input ref={videoRef} type="file" accept="video/mp4" required className={inputCls} />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="block text-xs text-slate-600 mb-1">双语 SRT</label>
                <input ref={biRef} type="file" accept=".srt" className={inputCls + ' !text-xs'} />
              </div>
              <div>
                <label className="block text-xs text-slate-600 mb-1">英文 SRT</label>
                <input ref={enRef} type="file" accept=".srt" className={inputCls + ' !text-xs'} />
              </div>
              <div>
                <label className="block text-xs text-slate-600 mb-1">中文 SRT</label>
                <input ref={zhRef} type="file" accept=".srt" className={inputCls + ' !text-xs'} />
              </div>
            </div>
            <div>
              <label className="block text-sm text-slate-700 mb-1">
                YouTube 链接 <span className="text-xs text-slate-400">（可选；用于提取 video_id 作为存储路径）</span>
              </label>
              <input
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=..."
                className={inputCls + ' font-mono text-xs'}
              />
            </div>
            <div>
              <label className="block text-sm text-slate-700 mb-1">
                标题 <span className="text-xs text-slate-400">（可选，留空用 video_id）</span>
              </label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className={inputCls}
              />
            </div>
            {upload.error && <Flash tone="error">{(upload.error as Error).message}</Flash>}
            {upload.data?.ok && (
              <Flash tone="success">
                ✓ 已上传（draft_id={upload.data.draft_id}），可在下方查看
              </Flash>
            )}
            <Btn type="submit" loading={upload.isPending}>
              📤 上传到服务器
            </Btn>
          </form>
        </div>
      )}

      {/* 列表 */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold">
          历史视频
          <span className="ml-2 text-[13px] text-slate-400 font-normal">{drafts.length} 条</span>
        </h2>
      </div>

      {drafts.length === 0 ? (
        <Empty icon="📺" title="还没有视频" />
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
                {d.source_url && (
                  <div className="text-[11px] text-slate-500 font-mono truncate mb-2">
                    {d.source_url}
                  </div>
                )}
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
