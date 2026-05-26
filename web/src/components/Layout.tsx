import { useEffect } from 'react'
import { Link, Outlet, useParams, useLocation, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, authApi } from '@/lib/api'
import { getRole, clearSession } from '@/lib/auth'
import type { Platform } from '@/lib/types'

// listDrafts 通用 API 适用的 platform（youtube 走单独的 ytList，不在这里 prefetch）
const PREFETCH_PLATFORMS: Platform[] = ['wechat', 'xhs', 'toutiao', 'douyin']

export function Layout() {
  const { userId } = useParams()
  const { pathname } = useLocation()
  const nav = useNavigate()
  const qc = useQueryClient()
  const role = getRole()
  const { data } = useQuery({ queryKey: ['users'], queryFn: api.listUsers })
  const users = data?.users ?? []
  const currentUser = users.find((u) => u.id === userId)

  const handleLogout = async () => {
    try { await authApi.logout() } catch { /* ignore */ }
    clearSession()
    qc.clear()
    nav('/login', { replace: true })
  }

  // 进入某个用户的任意 tab 时，prefetch 其它 3 个 tab 的草稿列表
  // 切 tab 时已经命中缓存，瞬间显示（跨区 SQL ~500ms 提前付掉）
  useEffect(() => {
    if (!userId) return
    for (const p of PREFETCH_PLATFORMS) {
      qc.prefetchQuery({
        queryKey: ['drafts', userId, p],
        queryFn:  () => api.listDrafts(userId, p),
        staleTime: 60_000,
      })
    }
    // youtube 走单独 API
    qc.prefetchQuery({
      queryKey: ['drafts', userId, 'youtube'],
      queryFn:  () => api.ytList(userId),
      staleTime: 60_000,
    })
  }, [userId, qc])

  return (
    <div className="min-h-full">
      {/* Top nav */}
      <nav className="bg-white border-b border-slate-200 px-7 py-3.5 flex items-center gap-3 sticky top-0 z-20">
        <Link to="/" className="font-bold text-brand-700">📤 Publisher Hub</Link>
        {currentUser && (
          <>
            <span className="text-slate-400">/</span>
            <Link to={`/${currentUser.id}`} className="font-semibold text-slate-800">
              {currentUser.name}
            </Link>
          </>
        )}
        <span className="flex-1" />
        <Link to="/" className="text-slate-500 text-sm hover:text-brand-700">所有用户</Link>
        {role === 'admin' && (
          <Link to="/admin" className={`text-sm ${pathname === '/admin' ? 'text-brand-700 font-medium' : 'text-slate-500 hover:text-brand-700'}`}>
            ⚙ 管理
          </Link>
        )}
        <span className={`text-[11px] px-2 py-0.5 rounded ${role === 'admin' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-600'}`}>
          {role === 'admin' ? '👑 管理员' : '👤 用户'}
        </span>
        <button onClick={handleLogout} className="text-slate-500 text-sm hover:text-red-600">
          退出
        </button>
      </nav>

      {/* Tabs (only on user pages) */}
      {currentUser && (
        <div className="bg-white border-b-2 border-slate-200">
          <div className="max-w-[1180px] mx-auto px-7 flex gap-1">
            <TabLink to={`/${currentUser.id}/wechat`}  active={pathname.startsWith(`/${currentUser.id}/wechat`)}>📰 公众号<Pill>{currentUser.wechat_count}</Pill></TabLink>
            <TabLink to={`/${currentUser.id}/xhs`}     active={pathname.startsWith(`/${currentUser.id}/xhs`)}>🌹 小红书<Pill>{currentUser.xhs_count}</Pill></TabLink>
            <TabLink to={`/${currentUser.id}/toutiao`} active={pathname.startsWith(`/${currentUser.id}/toutiao`)}>📱 今日头条<Pill>{currentUser.toutiao_count}</Pill></TabLink>
            <TabLink to={`/${currentUser.id}/douyin`}  active={pathname.startsWith(`/${currentUser.id}/douyin`)}>🎵 抖音<Pill>{currentUser.douyin_count}</Pill></TabLink>
            <TabLink to={`/${currentUser.id}/youtube`} active={pathname.startsWith(`/${currentUser.id}/youtube`)}>📺 YouTube<Pill>{currentUser.youtube_count}</Pill></TabLink>
            <TabLink to={`/${currentUser.id}/video`}   active={pathname.startsWith(`/${currentUser.id}/video`)}>🎬 短视频</TabLink>
          </div>
        </div>
      )}

      <div className="max-w-[1180px] mx-auto px-7 py-7">
        <Outlet />
      </div>
    </div>
  )
}

function TabLink({ to, active, children }: { to: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className={`px-5 py-2.5 -mb-[2px] border-b-2 text-[15px] transition ${
        active
          ? 'border-brand-700 text-brand-700 font-bold'
          : 'border-transparent text-slate-500 hover:text-brand-700 font-medium'
      }`}
    >
      {children}
    </Link>
  )
}

function Pill({ children }: { children: React.ReactNode }) {
  return (
    <span className="ml-1.5 inline-block px-2 py-px text-[11px] bg-slate-100 text-slate-500 rounded-full align-middle">
      {children}
    </span>
  )
}
