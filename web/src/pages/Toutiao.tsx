import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Btn, Flash } from '@/components/ui'
import type { ToutiaoStatus } from '@/lib/types'

export function Toutiao() {
  const { userId = '' } = useParams()
  const qc = useQueryClient()

  const { data: status } = useQuery({
    queryKey: ['toutiao', userId],
    queryFn: () => api.toutiaoStatus(userId),
    refetchInterval: 3_000,
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

  // 扫码成功 → 清掉二维码
  if (status?.status === 'logged_in' && bind.data?.qr_image) bind.reset()

  return (
    <>
      <div className="mb-5">
        <h2 className="text-lg font-semibold">头条号绑定</h2>
        <p className="text-sm text-slate-500 mt-1">
          每个用户独享一个 Chrome 实例 + 持久化 cookie。扫码绑定一次后登录态长期有效。
        </p>
      </div>

      <div className="bg-white rounded-lg border border-slate-200 p-5 mb-4">
        <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-2">当前状态</div>
        <StatusLine s={status} />
      </div>

      <div className="flex gap-2 mb-4">
        <Btn loading={bind.isPending} onClick={() => bind.mutate()}>
          {bind.isPending ? '启动 Chrome + 截二维码（10-30s）…' : '📱 扫码绑定 / 重新绑定'}
        </Btn>
        <Btn variant="danger" onClick={() => {
          if (confirm('确认解绑？会清除 cookie + Chrome user_data_dir')) unbind.mutate()
        }}>
          🗑 解绑
        </Btn>
      </div>

      {bind.error && <Flash tone="error">{(bind.error as Error).message}</Flash>}

      {bind.data?.already_logged_in && (
        <Flash tone="success">
          ✓ 该 Chrome 实例已经登录头条号，无需重新扫码。要换头条号请先点 🗑 解绑。
        </Flash>
      )}

      {bind.data?.qr_image && status?.status !== 'logged_in' && (
        <div className="p-5 bg-amber-50 border-2 border-amber-300 rounded-lg">
          <div className="flex justify-between items-start mb-3">
            <div>
              <div className="font-semibold text-amber-900 text-[15px]">
                📱 用今日头条 App / 微信扫码登录
              </div>
              <div className="text-xs text-amber-800 mt-1">
                扫码后页面顶部状态会在 3-5 秒内自动变绿
              </div>
            </div>
            <span className="text-[11px] text-amber-800 bg-amber-100 px-2 py-0.5 rounded font-mono">
              CDP :{bind.data.port}
            </span>
          </div>
          <img
            src={bind.data.qr_image}
            alt="头条号登录页"
            className="w-full max-w-2xl border border-amber-200 rounded bg-white"
          />
        </div>
      )}
    </>
  )
}

function StatusLine({ s }: { s: ToutiaoStatus | undefined }) {
  if (!s) return <span className="text-slate-500 text-sm"><span className="spinner mr-1" />检测中…</span>
  if (s.status === 'unbound') {
    return <span className="text-slate-500 text-sm">⚪ 未绑定（点下方"扫码绑定"开始）</span>
  }
  if (s.status === 'logged_out') {
    return (
      <div className="text-sm">
        <span className="text-red-700">✗ 已掉线 / 登录已失效</span>
        {s.url && <div className="text-[11px] text-slate-400 mt-1 truncate font-mono">{s.url}</div>}
      </div>
    )
  }
  if (s.status === 'logged_in') {
    return (
      <span className="text-emerald-700 text-sm font-medium">
        ✓ 已登录{s.name && `（${s.name}）`}
        {s.cookie_expires_days != null && (
          <span className="ml-2 text-slate-500 font-normal">· cookie 剩 {s.cookie_expires_days} 天</span>
        )}
      </span>
    )
  }
  return <span className="text-amber-700 text-sm">⚠ 检测出错：{s.error}</span>
}
