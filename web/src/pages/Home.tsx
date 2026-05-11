import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function Home() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['users'], queryFn: api.listUsers })

  const del = useMutation({
    mutationFn: (id: string) => api.deleteUser(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>

  const users = data?.users ?? []
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">用户列表</h1>
        <Link to="/users/new" className="px-3 py-1.5 bg-violet-600 text-white rounded text-sm hover:bg-violet-700">
          ＋ 新增用户
        </Link>
      </div>
      {users.length === 0 ? (
        <p className="text-slate-500">还没有用户，点击右上角新增。</p>
      ) : (
        <div className="bg-white border border-slate-200 rounded">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-4 py-2 text-left">用户</th>
                <th className="px-4 py-2 text-left">公众号 AppID</th>
                <th className="px-4 py-2 text-right">📰 ready</th>
                <th className="px-4 py-2 text-right">🌹 ready</th>
                <th className="px-4 py-2 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-slate-200">
                  <td className="px-4 py-2">
                    <Link to={`/${u.id}/wechat`} className="text-violet-700 hover:underline">
                      {u.name}
                    </Link>
                    <span className="ml-2 text-xs text-slate-400">{u.id}</span>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-slate-600">{u.wechat.app_id || '-'}</td>
                  <td className="px-4 py-2 text-right">{u.wechat_count}</td>
                  <td className="px-4 py-2 text-right">{u.xhs_count}</td>
                  <td className="px-4 py-2 text-right text-xs space-x-2">
                    <Link to={`/users/${u.id}/edit`} className="text-slate-600 hover:underline">编辑</Link>
                    <button
                      onClick={() => {
                        if (confirm(`确认删除用户 ${u.name}？草稿不会删，但用户配置会清空。`)) {
                          del.mutate(u.id)
                        }
                      }}
                      className="text-red-600 hover:underline"
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
