import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import type { ToutiaoStatus } from '@/lib/types'

export function Toutiao() {
  const { userId = '' } = useParams()
  const qc = useQueryClient()

  const { data: status } = useQuery({
    queryKey: ['toutiao', userId],
    queryFn: () => api.toutiaoStatus(userId),
    refetchInterval: 3_000,                  // 3s 轮询，扫码后能快速看到变绿
  })

  const bind = useMutation({
    mutationFn: () => api.toutiaoBind(userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['toutiao', userId] }),
  })

  const unbind = useMutation({
    mutationFn: () => api.toutiaoUnbind(userId),
    onSuccess: () => {
      bind.reset()
      qc.invalidateQueries({ queryKey: ['toutiao', userId] })
    },
  })

  // 一旦状态变 logged_in，清掉残留的二维码截图
  if (status?.status === 'logged_in' && bind.data?.qr_image) {
    bind.reset()
  }

  return (
    <div className="max-w-2xl">
      <h1 className="text-xl font-semibold mb-1">头条号绑定</h1>
      <p className="text-sm text-slate-500 mb-5">
        每个用户独享一个 Chrome 实例 + 持久化 cookie。扫码绑定一次后登录态长期有效。
      </p>

      <div className="bg-white border border-slate-200 rounded p-4 mb-4">
        <StatusBadge s={status} />
      </div>

      <div className="flex gap-2">
        <button
          onClick={() => bind.mutate()}
          disabled={bind.isPending}
          className="px-4 py-2 bg-violet-600 text-white rounded text-sm hover:bg-violet-700 disabled:opacity-50"
        >
          {bind.isPending ? '启动 Chrome + 截二维码（10-30s）…' : '📱 扫码绑定 / 重新绑定'}
        </button>
        <button
          onClick={() => {
            if (confirm('确认解绑？会清除 cookie + Chrome user_data_dir')) unbind.mutate()
          }}
          className="px-4 py-2 border border-slate-300 rounded text-sm hover:bg-slate-50"
        >
          🗑 解绑
        </button>
      </div>

      {bind.error && (
        <div className="mt-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2">
          {(bind.error as Error).message}
        </div>
      )}

      {bind.data?.already_logged_in && (
        <div className="mt-4 p-4 bg-emerald-50 border border-emerald-200 rounded text-sm text-emerald-800">
          ✓ 该 Chrome 实例已经登录头条号，无需重新扫码。
          要换头条号请先点 🗑 解绑。
        </div>
      )}

      {bind.data?.qr_image && status?.status !== 'logged_in' && (
        <div className="mt-4 p-4 bg-amber-50 border-2 border-amber-300 rounded">
          <div className="text-sm font-semibold text-amber-900 mb-1">
            📱 用今日头条 App / 微信扫码登录
          </div>
          <div className="text-xs text-amber-800 mb-3">
            扫码后页面顶部状态会在 3-5 秒内自动变绿。
            <span className="ml-1 bg-amber-100 px-1.5 py-0.5 rounded">CDP :{bind.data.port}</span>
          </div>
          <img src={bind.data.qr_image} alt="头条号登录页"
               className="w-full max-w-xl border border-amber-200 rounded bg-white" />
        </div>
      )}
    </div>
  )
}

function StatusBadge({ s }: { s: ToutiaoStatus | undefined }) {
  if (!s) return <span className="text-slate-500 text-sm">检测中…</span>
  if (s.status === 'unbound') {
    return <span className="text-slate-500 text-sm">⚪ 未绑定（点下方"扫码绑定"开始）</span>
  }
  if (s.status === 'logged_out') {
    return (
      <div>
        <span className="text-red-700 text-sm">✗ 已掉线 / 登录已失效</span>
        {s.url && <div className="text-xs text-slate-400 mt-1 truncate">{s.url}</div>}
      </div>
    )
  }
  if (s.status === 'logged_in') {
    return (
      <span className="text-emerald-700 text-sm">
        ✓ 已登录
        {s.name && <span>（{s.name}）</span>}
        {s.cookie_expires_days != null && (
          <span className="ml-2 text-slate-500">· cookie 剩 {s.cookie_expires_days} 天</span>
        )}
      </span>
    )
  }
  return <span className="text-amber-700 text-sm">⚠ 检测出错：{s.error}</span>
}
