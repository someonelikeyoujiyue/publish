import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Btn, Flash } from '@/components/ui'

export function Admin() {
  const [user, setUser] = useState('')
  const { data: sched } = useQuery({
    queryKey: ['scheduler'],
    queryFn: api.schedulerStatus,
    refetchInterval: 30_000,
  })
  const run = useMutation({ mutationFn: () => api.runNow(user || undefined) })

  return (
    <>
      <h2 className="text-lg font-semibold mb-5">管理</h2>

      <div className="bg-white border border-slate-200 rounded-lg p-5 mb-4">
        <div className="font-semibold mb-3">立即触发仿写 + 推送</div>
        <div className="flex gap-2">
          <input
            value={user}
            onChange={(e) => setUser(e.target.value)}
            placeholder="user_id（留空 = 全部用户）"
            className="flex-1 border border-slate-300 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50"
          />
          <Btn loading={run.isPending} onClick={() => run.mutate()}>▶ 触发</Btn>
        </div>
        {run.data?.ok && (
          <Flash tone="success">
            ✓ 已后台触发{run.data.target ? `（用户 ${run.data.target}）` : ''}；
            进度看 <code className="text-xs bg-slate-100 px-1.5 py-0.5 rounded">journalctl -u publisher-hub -f</code>
          </Flash>
        )}
      </div>

      <div className="bg-white border border-slate-200 rounded-lg p-5">
        <div className="font-semibold mb-3">调度器</div>
        {!sched ? (
          <p className="text-slate-500 text-sm">查询中…</p>
        ) : !sched.running ? (
          <p className="text-red-600 text-sm">未启动</p>
        ) : (
          <dl className="text-sm grid grid-cols-[120px_1fr] gap-y-2">
            <dt className="text-slate-500">job_id</dt>
            <dd className="font-mono text-slate-800">{sched.job_id}</dd>
            <dt className="text-slate-500">cron</dt>
            <dd className="font-mono text-slate-800">{sched.cron}</dd>
            <dt className="text-slate-500">下次触发</dt>
            <dd className="text-slate-800">{sched.next || '—'}</dd>
          </dl>
        )}
      </div>
    </>
  )
}
