import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Btn, Badge, Flash } from '@/components/ui'
import type { Platform } from '@/lib/types'

const PLATFORM_LABEL = { wechat: '公众号', xhs: '小红书' } as const

export function DraftDetail({ platform }: { platform: Platform }) {
  const { userId = '', draftId = '' } = useParams()
  const qc = useQueryClient()
  const id = Number(draftId)
  const [lightbox, setLightbox] = useState<string | null>(null)

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
  const qrUrl = push.data?.qr_url || data.qr_url

  return (
    <>
      <div className="mb-4">
        <Link to={`/${userId}/${platform}`} className="text-sm text-slate-500 hover:text-brand-700">
          ← 返回 {PLATFORM_LABEL[platform]} 列表
        </Link>
      </div>

      <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0,2fr) minmax(0,1fr)' }}>
        {/* Main */}
        <article className="bg-white rounded-lg border border-slate-200 px-9 py-8">
          <div className="flex gap-2 mb-3 items-center">
            <Badge kind={data.status === 'pushed' ? 'pushed' : data.status === 'failed' ? 'failed' : 'ready'}>
              {data.status}
            </Badge>
            <Badge kind="platform">{PLATFORM_LABEL[platform]}</Badge>
          </div>
          <h1 className="text-2xl font-bold leading-snug mb-3">{data.title || '(无标题)'}</h1>
          <div className="text-xs text-slate-400 pb-4 mb-6 border-b border-slate-200 flex gap-4 flex-wrap">
            <span>创建：{data.created_at}</span>
            {data.pushed_at && <span>推送：{data.pushed_at}</span>}
          </div>

          {data.error && (
            <Flash tone="error">上次推送错误：{data.error}</Flash>
          )}

          {platform === 'wechat' ? (
            <div
              className="prose-pub text-[16px] leading-[1.85]"
              dangerouslySetInnerHTML={{ __html: data.content_html || '' }}
            />
          ) : (
            <pre className="whitespace-pre-wrap font-sans text-[15px] leading-[1.85] text-slate-800">
              {data.content}
            </pre>
          )}
        </article>

        {/* Sidebar */}
        <aside className="space-y-5">
          {data.images.length > 0 && (
            <div className="bg-white rounded-lg border border-slate-200 p-5">
              <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-3 font-medium">
                {data.images.length} 张图
              </div>
              <div className="grid grid-cols-2 gap-1.5">
                {data.images.map((src, i) => (
                  <button key={i} onClick={() => setLightbox(src)}>
                    <img src={src} className="w-full aspect-square object-cover rounded bg-slate-100 hover:opacity-80 transition" alt="" />
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="bg-white rounded-lg border border-slate-200 p-5 sticky top-32">
            <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-3 font-medium">操作</div>

            {canPush ? (
              <Btn
                className="w-full justify-center"
                onClick={() => push.mutate()}
                loading={push.isPending}
              >
                {push.isPending
                  ? (platform === 'wechat' ? '推送中…' : '生成二维码中…')
                  : (platform === 'wechat' ? '📤 推送到公众号草稿箱' : '🌹 生成小红书发布二维码')}
              </Btn>
            ) : (
              <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-3 py-2">
                ✓ 已推送
              </div>
            )}

            {push.data?.ok === false && (
              <div className="mt-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
                ✗ {push.data.error}
              </div>
            )}
            {push.data?.ok && platform === 'wechat' && (
              <div className="mt-3 text-xs text-emerald-700">
                ✓ 已推送到{' '}
                <a href="https://mp.weixin.qq.com" target="_blank" className="underline">公众号后台</a>
                <div className="text-slate-400 mt-1 font-mono break-all">media_id: {push.data.media_id}</div>
              </div>
            )}
            {qrUrl && platform === 'xhs' && (
              <div className="mt-4">
                <div className="text-xs text-emerald-700 mb-2">✓ 用手机扫码发布</div>
                <img src={qrUrl} alt="发布二维码" className="w-full border border-slate-200 rounded bg-white" />
              </div>
            )}
          </div>
        </aside>
      </div>

      {/* Lightbox */}
      {lightbox && (
        <div
          className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center cursor-zoom-out p-8"
          onClick={() => setLightbox(null)}
        >
          <img src={lightbox} className="max-w-full max-h-full object-contain" />
        </div>
      )}
    </>
  )
}
