import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { Platform, DraftStatus } from '@/lib/types'

const STATUS_LABEL: Record<DraftStatus, { text: string; cls: string }> = {
  ready:  { text: '待推',   cls: 'bg-amber-100  text-amber-800' },
  pushed: { text: '✓ 已推', cls: 'bg-emerald-100 text-emerald-800' },
  failed: { text: '✗ 失败', cls: 'bg-red-100    text-red-800' },
}

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
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drafts', userId, platform] }),
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  const drafts = data?.drafts ?? []

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">
          {platform === 'wechat' ? '公众号草稿' : '小红书草稿'}
          <span className="ml-2 text-sm text-slate-500">{drafts.length} 条</span>
        </h1>
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="px-3 py-1.5 bg-violet-600 text-white rounded text-sm hover:bg-violet-700 disabled:opacity-50"
        >
          {refresh.isPending ? '⟳ 仿写中…（30-90s）' : '⟳ 立即仿写'}
        </button>
      </div>

      {refresh.data?.ok === false && (
        <div className="mb-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
          {refresh.data.error}
        </div>
      )}
      {refresh.data?.ok && (
        <div className="mb-3 text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded p-2">
          ✓ 仿写完成，新增 {refresh.data.new_count} 条
        </div>
      )}

      {drafts.length === 0 ? (
        <p className="text-slate-500">还没有草稿，点上面"立即仿写"。</p>
      ) : (
        <div className="bg-white border border-slate-200 rounded divide-y divide-slate-200">
          {drafts.map((d) => {
            const s = STATUS_LABEL[d.status]
            return (
              <Link
                key={d.id}
                to={`/${userId}/${platform}/${d.id}`}
                className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50"
              >
                <span className={`px-2 py-0.5 text-xs rounded ${s.cls}`}>{s.text}</span>
                <span className="flex-1 truncate">{d.title || '(无标题)'}</span>
                <span className="text-xs text-slate-400 shrink-0">{d.created_at}</span>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
