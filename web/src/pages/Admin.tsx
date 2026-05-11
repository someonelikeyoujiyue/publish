import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function Admin() {
  const [user, setUser] = useState('')
  const { data: sched } = useQuery({
    queryKey: ['scheduler'],
    queryFn: api.schedulerStatus,
    refetchInterval: 30_000,
  })
  const run = useMutation({ mutationFn: () => api.runNow(user || undefined) })

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-semibold mb-4">管理</h1>

      <div className="bg-white border border-slate-200 rounded p-4 mb-4">
        <h2 className="font-medium mb-2">立即触发仿写 + 推送</h2>
        <div className="flex gap-2">
          <input
            value={user}
            onChange={(e) => setUser(e.target.value)}
            placeholder="user_id（留空 = 全部用户）"
            className="flex-1 border border-slate-300 rounded px-3 py-1.5 text-sm"
          />
          <button
            onClick={() => run.mutate()}
            disabled={run.isPending}
            className="px-4 py-1.5 bg-violet-600 text-white rounded text-sm hover:bg-violet-700 disabled:opacity-50"
          >
            ▶ 触发
          </button>
        </div>
        {run.data?.ok && (
          <p className="mt-2 text-sm text-emerald-700">
            ✓ 已后台触发{run.data.target ? `（用户 ${run.data.target}）` : ''}；
            进度看 <code className="text-xs bg-slate-100 px-1">journalctl -u publisher-hub -f</code>
          </p>
        )}
      </div>

      <div className="bg-white border border-slate-200 rounded p-4">
        <h2 className="font-medium mb-2">调度器</h2>
        {!sched ? (
          <p className="text-slate-500 text-sm">查询中…</p>
        ) : !sched.running ? (
          <p className="text-red-600 text-sm">未启动</p>
        ) : (
          <dl className="text-sm space-y-1">
            <div><dt className="inline text-slate-500">job_id:</dt> <dd className="inline">{sched.job_id}</dd></div>
            <div><dt className="inline text-slate-500">cron:</dt> <dd className="inline font-mono">{sched.cron}</dd></div>
            <div><dt className="inline text-slate-500">下次触发:</dt> <dd className="inline">{sched.next || '—'}</dd></div>
          </dl>
        )}
      </div>
    </div>
  )
}
