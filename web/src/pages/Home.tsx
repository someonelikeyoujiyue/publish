import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { BtnLink, Btn, Card, Empty } from '@/components/ui'

export function Home() {
  const qc  = useQueryClient()
  const nav = useNavigate()
  const { data, isLoading } = useQuery({ queryKey: ['users'], queryFn: api.listUsers })

  const del = useMutation({
    mutationFn: (id: string) => api.deleteUser(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  const users = data?.users ?? []

  return (
    <>
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-lg font-semibold">用户列表</h1>
        <BtnLink to="/users/new">＋ 新增用户</BtnLink>
      </div>

      {users.length === 0 ? (
        <Empty icon="👤" title="还没有用户" action={<BtnLink to="/users/new">新增第一个用户</BtnLink>} />
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {users.map((u) => (
            <Card key={u.id}>
              <div className="text-lg font-bold mb-1">{u.name}</div>
              <div className="text-xs text-slate-400 font-mono mb-4">{u.id}</div>

              <div className="flex gap-3 p-3 bg-slate-50 rounded-md mb-4">
                <Stat label="📰 公众号" n={u.wechat_count} />
                <Stat label="🌹 小红书" n={u.xhs_count} />
                <Stat label="📱 头条号" n={u.toutiao_count} />
              </div>

              <div className="flex gap-2">
                <Link to={`/${u.id}/wechat`} className="flex-1 text-center py-1.5 text-sm bg-brand-50 text-brand-700 rounded hover:bg-brand-100">
                  进入 →
                </Link>
                <Link to={`/users/${u.id}/edit`} className="px-3 py-1.5 text-sm text-slate-500 hover:text-brand-700">编辑</Link>
                <Btn
                  variant="ghost"
                  className="!px-2 !py-1.5 text-xs hover:!text-red-600"
                  onClick={() => {
                    if (confirm(`确认删除用户「${u.name}」？\n（草稿不会删，但用户配置会清空）`)) del.mutate(u.id)
                  }}
                >
                  删除
                </Btn>
              </div>
            </Card>
          ))}

          <button
            onClick={() => nav('/users/new')}
            className="bg-white border-2 border-dashed border-slate-300 rounded-lg p-5 text-slate-400 hover:border-brand-700 hover:text-brand-700 transition flex items-center justify-center min-h-[180px]"
          >
            ＋ 新增用户
          </button>
        </div>
      )}
    </>
  )
}

function Stat({ label, n }: { label: string; n: number | string }) {
  return (
    <div className="flex-1 text-center">
      <div className="text-xl font-bold text-brand-700 leading-tight">{n}</div>
      <div className="text-[11px] text-slate-500 mt-0.5">{label}</div>
    </div>
  )
}
