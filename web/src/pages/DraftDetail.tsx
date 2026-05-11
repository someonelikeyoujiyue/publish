import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Platform } from '@/lib/types'

export function DraftDetail({ platform }: { platform: Platform }) {
  const { userId = '', draftId = '' } = useParams()
  const qc = useQueryClient()
  const id = Number(draftId)

  const { data, isLoading } = useQuery({
    queryKey: ['draft', userId, platform, id],
    queryFn: () => api.getDraft(userId, platform, id),
    enabled: !!userId && !!id,
  })

  const push = useMutation({
    mutationFn: () => api.push(userId, platform, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['draft', userId, platform, id] })
      qc.invalidateQueries({ queryKey: ['drafts', userId, platform] })
    },
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  if (!data) return <p className="text-slate-500">草稿不存在</p>

  const canPush = data.status === 'ready' || data.status === 'failed'

  return (
    <div className="max-w-3xl">
      <Link to={`/${userId}/${platform}`} className="text-sm text-slate-500 hover:text-slate-800">
        ← 返回列表
      </Link>
      <h1 className="text-2xl font-semibold mt-2 mb-1">{data.title || '(无标题)'}</h1>
      <div className="text-xs text-slate-400 mb-4">
        {data.created_at} · 状态: {data.status}
        {data.pushed_at && ` · 推送时间: ${data.pushed_at}`}
      </div>

      {/* 推送动作 */}
      <div className="mb-5 p-4 bg-white border border-slate-200 rounded">
        {canPush ? (
          <button
            onClick={() => push.mutate()}
            disabled={push.isPending}
            className="px-4 py-2 bg-violet-600 text-white rounded text-sm hover:bg-violet-700 disabled:opacity-50"
          >
            {push.isPending
              ? (platform === 'wechat' ? '推送中…' : '生成二维码中…')
              : (platform === 'wechat' ? '📤 推送到草稿箱' : '🌹 生成发布二维码')}
          </button>
        ) : (
          <div className="text-sm text-emerald-700">✓ 已推送</div>
        )}
        {push.data?.ok === false && (
          <div className="mt-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
            ✗ {push.data.error}
          </div>
        )}
        {push.data?.ok && platform === 'wechat' && (
          <div className="mt-3 text-sm text-emerald-700">
            ✓ 已推送，去 <a href="https://mp.weixin.qq.com" target="_blank" className="underline">公众号后台</a> 草稿箱查看
            <span className="ml-2 text-slate-400">media_id: {push.data.media_id}</span>
          </div>
        )}
        {(push.data?.qr_url || data.qr_url) && platform === 'xhs' && (
          <div className="mt-3">
            <div className="text-sm text-emerald-700 mb-2">✓ 扫码发布</div>
            <img src={push.data?.qr_url || data.qr_url} alt="发布二维码" className="w-48 border border-slate-200 rounded" />
          </div>
        )}
      </div>

      {data.error && (
        <div className="mb-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">
          上次推送错误：{data.error}
        </div>
      )}

      {/* 图片 */}
      {data.images.length > 0 && (
        <div className="mb-5">
          <div className="text-sm text-slate-600 mb-2">{data.images.length} 张图</div>
          <div className="grid grid-cols-3 gap-2">
            {data.images.map((src, i) => (
              <img key={i} src={src} className="w-full aspect-square object-cover border border-slate-200 rounded" />
            ))}
          </div>
        </div>
      )}

      {/* 正文 */}
      <article className="bg-white border border-slate-200 rounded p-5">
        {platform === 'wechat' ? (
          <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: data.content_html || '' }} />
        ) : (
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">{data.content}</pre>
        )}
      </article>
    </div>
  )
}
