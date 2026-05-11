import { Link, Outlet, useParams, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'

export function Layout() {
  const { userId } = useParams()
  const { pathname } = useLocation()
  const { data } = useQuery({ queryKey: ['users'], queryFn: api.listUsers })
  const users = data?.users ?? []

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-60 border-r border-slate-200 bg-white flex flex-col">
        <Link to="/" className="px-4 py-4 border-b border-slate-200 font-semibold text-slate-900 hover:bg-slate-50">
          📦 Publisher Hub
        </Link>
        <nav className="flex-1 overflow-y-auto py-2">
          {users.map((u) => (
            <Link
              key={u.id}
              to={`/${u.id}/wechat`}
              className={`block px-4 py-2 text-sm hover:bg-slate-50 ${
                userId === u.id ? 'bg-violet-50 text-violet-700 font-medium' : 'text-slate-700'
              }`}
            >
              <div>{u.name}</div>
              <div className="text-xs text-slate-500 mt-0.5">
                📰 {u.wechat_count} · 🌹 {u.xhs_count}
              </div>
            </Link>
          ))}
          <Link
            to="/users/new"
            className="block px-4 py-2 text-sm text-slate-500 hover:bg-slate-50 mt-2 border-t border-slate-200"
          >
            ＋ 新增用户
          </Link>
          <Link
            to="/admin"
            className={`block px-4 py-2 text-sm hover:bg-slate-50 ${
              pathname === '/admin' ? 'bg-violet-50 text-violet-700 font-medium' : 'text-slate-700'
            }`}
          >
            ⚙ 管理
          </Link>
        </nav>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto">
        {userId && <PlatformTabs userId={userId} />}
        <div className="p-6 max-w-5xl mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

function PlatformTabs({ userId }: { userId: string }) {
  const { pathname } = useLocation()
  const tab = (k: 'wechat' | 'xhs' | 'toutiao') => pathname.startsWith(`/${userId}/${k}`)
  const cls = (active: boolean) =>
    `px-4 py-3 text-sm font-medium border-b-2 ${
      active ? 'border-violet-600 text-violet-700' : 'border-transparent text-slate-500 hover:text-slate-800'
    }`
  return (
    <div className="flex gap-1 border-b border-slate-200 bg-white sticky top-0 z-10">
      <Link to={`/${userId}/wechat`}   className={cls(tab('wechat'))}>📰 公众号</Link>
      <Link to={`/${userId}/xhs`}      className={cls(tab('xhs'))}>🌹 小红书</Link>
      <Link to={`/${userId}/toutiao`}  className={cls(tab('toutiao'))}>📱 今日头条</Link>
    </div>
  )
}
