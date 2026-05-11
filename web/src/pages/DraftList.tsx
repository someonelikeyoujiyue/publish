import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Btn, Card, Badge, Empty, Flash } from '@/components/ui'
import type { Platform, DraftStatus } from '@/lib/types'

const STATUS_LABEL: Record<DraftStatus, { text: string; kind: 'ready' | 'pushed' | 'failed' }> = {
  ready:  { text: '待推',   kind: 'ready' },
  pushed: { text: '✓ 已推', kind: 'pushed' },
  failed: { text: '✗ 失败', kind: 'failed' },
}

const PLATFORM_LABEL = { wechat: '公众号', xhs: '小红书', toutiao: '微头条' } as const

export function DraftList({ platform }: { platform: Platform }) {
  const { userId = '' } = useParams()
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['drafts', userId, platform],
    queryFn: () => api.listDrafts(userId, platform),
    enabled: !!userId,
  })

  const refresh = useMutation({
    mutationFn: () => api.refresh(userId, platform),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['drafts', userId, platform] })
      qc.invalidateQueries({ queryKey: ['users'] })
    },
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  const drafts = data?.drafts ?? []

  return (
    <>
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-lg font-semibold">
          {PLATFORM_LABEL[platform]} 草稿
          <span className="ml-2 text-[13px] text-slate-400 font-normal">{drafts.length} 条</span>
        </h2>
        <Btn onClick={() => refresh.mutate()} loading={refresh.isPending}>
          {refresh.isPending ? '仿写中…（30-90s）' : '⟳ 立即仿写'}
        </Btn>
      </div>

      {refresh.data?.ok === false && <Flash tone="error">✗ 仿写失败：{refresh.data.error}</Flash>}
      {refresh.data?.ok && <Flash tone="success">✓ 仿写完成，新增 {refresh.data.new_count} 条</Flash>}

      {drafts.length === 0 ? (
        <Empty icon="✍️" title={`还没有 ${PLATFORM_LABEL[platform]} 草稿`} action={
          <Btn onClick={() => refresh.mutate()} loading={refresh.isPending}>立即仿写</Btn>
        } />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {drafts.map((d) => {
            const s = STATUS_LABEL[d.status]
            return (
              <Card key={d.id} hover>
                <div className="flex gap-3 mb-3 items-center">
                  <Badge kind={s.kind}>{s.text}</Badge>
                  <Badge kind="platform">{PLATFORM_LABEL[platform]}</Badge>
                  <span className="ml-auto text-[11px] text-slate-400">{d.created_at.slice(0, 16)}</span>
                </div>
                <Link
                  to={`/${userId}/${platform}/${d.id}`}
                  className="block text-[17px] font-semibold leading-snug text-slate-800 hover:text-brand-700 line-clamp-2 mb-3"
                >
                  {d.title || '(无标题)'}
                </Link>
                {d.cover && (
                  <div className="flex gap-1.5 mb-3">
                    <img src={d.cover} className="w-20 h-20 object-cover rounded bg-slate-100" alt="" />
                    {d.image_count > 1 && (
                      <div className="w-20 h-20 bg-slate-50 rounded flex items-center justify-center text-xs text-slate-500">
                        +{d.image_count - 1}
                      </div>
                    )}
                  </div>
                )}
                {d.status === 'failed' && d.error && (
                  <div className="text-xs text-red-700 bg-red-50 px-2 py-1 rounded mb-2">
                    {d.error.slice(0, 80)}
                  </div>
                )}
                <div className="flex gap-2 pt-3 border-t border-slate-100">
                  <Link to={`/${userId}/${platform}/${d.id}`} className="flex-1 text-center py-1.5 text-sm bg-brand-50 text-brand-700 rounded hover:bg-brand-100">
                    查看 →
                  </Link>
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </>
  )
}
