import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Badge, Flash } from '@/components/ui'

export function YoutubeDetail() {
  const { userId = '', draftId = '' } = useParams()
  const id = Number(draftId)

  const { data, isLoading } = useQuery({
    queryKey: ['draft', userId, 'youtube', id],
    queryFn: () => api.ytGet(userId, id),
    enabled: !!userId && !!id,
    refetchInterval: (q) => {
      const d = q.state.data as { status: string } | undefined
      return d?.status === 'processing' ? 5_000 : false
    },
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  if (!data) return <p className="text-slate-500">草稿不存在</p>

  return (
    <>
      <div className="mb-4">
        <Link to={`/${userId}/youtube`} className="text-sm text-slate-500 hover:text-brand-700">
          ← 返回 YouTube 列表
        </Link>
      </div>

      <div className="bg-white rounded-lg border border-slate-200 px-8 py-6">
        <div className="flex gap-2 mb-3 items-center">
          <Badge
            kind={
              data.status === 'pushed'  ? 'pushed'
              : data.status === 'failed' ? 'failed'
              : 'ready'
            }
          >
            {data.status === 'processing' ? '⏳ 处理中（5-15 分钟）'
              : data.status === 'pushed' ? '✓ 已完成'
              : data.status === 'failed' ? '✗ 失败'
              : '待处理'}
          </Badge>
          <Badge kind="platform">YouTube</Badge>
        </div>
        <h1 className="text-xl font-semibold mb-2">{data.title}</h1>
        <p className="text-xs text-slate-400 mb-1 font-mono break-all">
          源链接：<a href={data.source_url} target="_blank" rel="noopener" className="hover:underline">{data.source_url}</a>
        </p>
        <p className="text-xs text-slate-400 mb-5">
          提交时间：{data.created_at}
          {data.pushed_at && <>　·　完成时间：{data.pushed_at}</>}
        </p>

        {data.status === 'failed' && data.error && (
          <Flash tone="error">{data.error}</Flash>
        )}

        {data.status === 'processing' && (
          <Flash tone="info">
            正在后台处理（下载视频 → 抓字幕 → LLM 翻译 → ffmpeg 合成）。
            页面会自动每 5 秒刷新。
          </Flash>
        )}

        {data.status === 'pushed' && data.video_url && (
          <>
            <video
              controls
              src={data.video_url}
              className="w-full max-h-[480px] bg-black rounded mb-4"
            />
            <div className="flex gap-2 mb-5 flex-wrap text-sm">
              <a
                href={data.video_url}
                download
                className="px-4 py-2 bg-brand-700 text-white rounded hover:bg-brand-600"
              >
                ⬇ 下载 MP4
              </a>
              <a
                href={data.video_url.replace('/video_final.mp4', '/subs.bilingual.srt')}
                download
                className="px-4 py-2 border border-slate-300 rounded hover:bg-slate-50"
              >
                ⬇ 双语 SRT
              </a>
              <a
                href={data.video_url.replace('/video_final.mp4', '/subs.zh-Hans.srt')}
                download
                className="px-4 py-2 border border-slate-300 rounded hover:bg-slate-50"
              >
                ⬇ 中文 SRT
              </a>
              <a
                href={data.video_url.replace('/video_final.mp4', '/subs.en.srt')}
                download
                className="px-4 py-2 border border-slate-300 rounded hover:bg-slate-50"
              >
                ⬇ 英文 SRT
              </a>
              <a
                href={data.video_url.replace('/video_final.mp4', '/summary.json')}
                target="_blank"
                rel="noopener"
                className="px-4 py-2 border border-slate-300 rounded hover:bg-slate-50"
              >
                📋 summary.json
              </a>
            </div>
            <details className="mt-4">
              <summary className="text-sm text-slate-600 cursor-pointer hover:text-brand-700">
                双语字幕预览（{data.content.split('\n\n').length - 1} 段）
              </summary>
              <pre className="mt-3 p-4 bg-slate-50 border border-slate-200 rounded text-[12px] leading-relaxed font-mono whitespace-pre-wrap max-h-[400px] overflow-auto">
                {data.content}
              </pre>
            </details>
          </>
        )}
      </div>
    </>
  )
}
