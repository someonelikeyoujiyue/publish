import { Link, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { isAdmin } from '@/lib/auth'
import { Btn, Card, Badge, Empty, Flash } from '@/components/ui'
import type { ToutiaoStatus, DraftStatus } from '@/lib/types'

const STATUS_LABEL: Record<DraftStatus, { text: string; kind: 'ready' | 'pushed' | 'failed' }> = {
  ready:  { text: '待发',   kind: 'ready' },
  pushed: { text: '✓ 已发', kind: 'pushed' },
  failed: { text: '✗ 失败', kind: 'failed' },
}

export function Toutiao() {
  const { userId = '' } = useParams()
  const qc = useQueryClient()
  const admin = isAdmin()

  // ── 绑定状态 ──────────────────────────────────────────────────
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

  if (status?.status === 'logged_in' && bind.data?.qr_image) bind.reset()

  // ── 草稿列表 ──────────────────────────────────────────────────
  const { data: dlist } = useQuery({
    queryKey: ['drafts', userId, 'toutiao'],
    queryFn: () => api.listDrafts(userId, 'toutiao'),
    enabled: !!userId,
  })

  const refresh = useMutation({
    mutationFn: () => api.refresh(userId, 'toutiao'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['drafts', userId, 'toutiao'] })
      qc.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const drafts = dlist?.drafts ?? []

  return (
    <>
      {/* ── 绑定说明 ───────────────────────────────────────────── */}
      <div className="bg-amber-50 border-l-4 border-amber-400 rounded p-4 mb-4 text-sm text-slate-700 leading-relaxed">
        <div className="font-semibold text-amber-900 mb-1">⚠ 是否绑定头条号？</div>
        <div className="mb-2">
          <b>绑定后</b>：服务器用你的浏览器 cookie 帮你 <span className="text-emerald-700">自动推送到草稿箱 / 自动发布</span>，每条草稿一键完成。
          但平台风控可能识别自动化行为，<span className="text-red-700">存在封号风险</span>，请自行评估。
        </div>
        <div>
          <b>不绑定</b>：每次进草稿详情点「📋 复制 + 打开发布页」，自己粘贴 + 改图 + 点发布，<span className="text-emerald-700">零风险</span>，但要手动操作。
        </div>
      </div>

      {/* ── 绑定卡 ─────────────────────────────────────────────── */}
      <div className="bg-white rounded-lg border border-slate-200 p-5 mb-6">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-1">头条号绑定</div>
            <StatusLine s={status} />
          </div>
          <div className="flex gap-2">
            <Btn loading={bind.isPending} onClick={() => bind.mutate()}>
              {bind.isPending ? '截二维码中…' : '📱 扫码绑定'}
            </Btn>
            <Btn variant="danger" onClick={() => {
              if (confirm('确认解绑？会清除 cookie + Chrome user_data_dir')) unbind.mutate()
            }}>🗑 解绑</Btn>
          </div>
        </div>

        {bind.error && <Flash tone="error">{(bind.error as Error).message}</Flash>}

        {bind.data?.already_logged_in && (
          <Flash tone="success">
            ✓ 该 Chrome 实例已经登录头条号，无需重新扫码。换号请先点 🗑 解绑。
          </Flash>
        )}

        {bind.data?.qr_image && status?.status !== 'logged_in' && (
          <div className="mt-3 p-4 bg-amber-50 border-2 border-amber-300 rounded">
            <div className="flex justify-between items-start mb-2">
              <div>
                <div className="font-semibold text-amber-900 text-[14px]">
                  📱 用今日头条 App / 微信扫码登录
                </div>
                <div className="text-xs text-amber-800 mt-0.5">
                  扫码后状态会在 3-5 秒内自动变绿
                </div>
              </div>
              <span className="text-[11px] text-amber-800 bg-amber-100 px-2 py-0.5 rounded font-mono">
                CDP :{bind.data.port}
              </span>
            </div>
            <img src={bind.data.qr_image} alt="头条号登录页"
                 className="w-full max-w-xl border border-amber-200 rounded bg-white" />
          </div>
        )}
      </div>

      {/* ── 草稿列表 ───────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">
          微头条草稿
          <span className="ml-2 text-[13px] text-slate-400 font-normal">{drafts.length} 条</span>
        </h2>
        {admin && (
          <Btn onClick={() => refresh.mutate()} loading={refresh.isPending}>
            {refresh.isPending ? '仿写中…（30-90s）' : '⟳ 立即仿写'}
          </Btn>
        )}
      </div>

      {refresh.data?.ok === false && <Flash tone="error">✗ 仿写失败：{refresh.data.error}</Flash>}
      {refresh.data?.ok && <Flash tone="success">✓ 仿写完成，新增 {refresh.data.new_count} 条</Flash>}

      {drafts.length === 0 ? (
        <Empty icon="✍️" title="还没有微头条草稿" action={
          admin ? <Btn onClick={() => refresh.mutate()} loading={refresh.isPending}>立即仿写</Btn>
                : <p className="text-xs text-slate-400">等管理员仿写或定时任务</p>
        } />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {drafts.map((d) => {
            const s = STATUS_LABEL[d.status]
            return (
              <Card key={d.id} hover>
                <div className="flex gap-2 mb-3 items-center">
                  <Badge kind={s.kind}>{s.text}</Badge>
                  <Badge kind="platform">微头条</Badge>
                  <span className="ml-auto text-[11px] text-slate-400">{d.created_at.slice(0, 16)}</span>
                </div>
                <Link
                  to={`/${userId}/toutiao/${d.id}`}
                  className="block text-[17px] font-semibold leading-snug text-slate-800 hover:text-brand-700 line-clamp-2 mb-3"
                >
                  {d.title || '(无标题)'}
                </Link>
                {d.status === 'failed' && d.error && (
                  <div className="text-xs text-red-700 bg-red-50 px-2 py-1 rounded mb-2">
                    {d.error.slice(0, 80)}
                  </div>
                )}
                <div className="flex gap-2 pt-3 border-t border-slate-100">
                  <Link to={`/${userId}/toutiao/${d.id}`} className="flex-1 text-center py-1.5 text-sm bg-brand-50 text-brand-700 rounded hover:bg-brand-100">
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

function StatusLine({ s }: { s: ToutiaoStatus | undefined }) {
  if (!s) return <span className="text-slate-500 text-sm"><span className="spinner mr-1" />检测中…</span>
  if (s.status === 'unbound') return <span className="text-slate-500 text-sm">⚪ 未绑定</span>
  if (s.status === 'logged_out') return <span className="text-red-700 text-sm">✗ 已掉线 / 登录已失效</span>
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
