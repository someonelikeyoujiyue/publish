import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { isAdmin } from '@/lib/auth'
import { BtnLink, Btn, Card, Empty } from '@/components/ui'

export function Home() {
  const qc  = useQueryClient()
  const nav = useNavigate()
  const admin = isAdmin()
  const { data, isLoading } = useQuery({ queryKey: ['users'], queryFn: api.listUsers })

  const del = useMutation({
    mutationFn: (id: string) => api.deleteUser(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  const users = data?.users ?? []

  return (
    <>
      <div className="bg-blue-50 border-l-4 border-blue-400 rounded p-4 mb-5 text-sm text-slate-700 leading-relaxed">
        <div className="font-semibold text-blue-900 mb-2">📌 使用提示</div>
        AI 生成的图片不一定完全符合文字内容，发布前请按各平台特性调整：
        <ul className="mt-2 space-y-1 list-disc pl-5">
          <li><b>📰 公众号</b>：自动推送到草稿箱，去 <a href="https://mp.weixin.qq.com" target="_blank" rel="noopener" className="text-brand-700 underline">公众号后台</a> 草稿箱里换图 + 群发</li>
          <li><b>🌹 小红书</b>：点详情页推送 → 用手机扫码 → 进入小红书 App 后再改图 + 发布</li>
          <li><b>📱 今日头条</b>：复制内容粘贴到 <a href="https://mp.toutiao.com" target="_blank" rel="noopener" className="text-brand-700 underline">头条号后台</a> 自己调整；或自动发后去草稿箱修改</li>
          <li><b>🎵 抖音</b>：推荐复制内容粘贴到 <a href="https://creator.douyin.com" target="_blank" rel="noopener" className="text-brand-700 underline">抖音创作者中心</a> 手动上传图片发布</li>
        </ul>
      </div>

      <div className="flex items-center justify-between mb-5">
        <h1 className="text-lg font-semibold">用户列表</h1>
        {admin && <BtnLink to="/users/new">＋ 新增用户</BtnLink>}
      </div>

      {users.length === 0 ? (
        <Empty icon="👤" title="还没有用户" action={admin ? <BtnLink to="/users/new">新增第一个用户</BtnLink> : undefined} />
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
          {users.map((u) => (
            <Card key={u.id}>
              <div className="text-lg font-bold mb-1">{u.name}</div>
              <div className="text-xs text-slate-400 font-mono mb-4">{u.id}</div>

              <div className="grid grid-cols-4 gap-2 p-3 bg-slate-50 rounded-md mb-4">
                <Stat label="📰 公众号" n={u.wechat_count} />
                <Stat label="🌹 小红书" n={u.xhs_count} />
                <Stat label="📱 头条号" n={u.toutiao_count} />
                <Stat label="🎵 抖音"   n={u.douyin_count} />
              </div>

              <div className="flex gap-2">
                <Link to={`/${u.id}/wechat`} className="flex-1 text-center py-1.5 text-sm bg-brand-50 text-brand-700 rounded hover:bg-brand-100">
                  进入 →
                </Link>
                {admin && (
                  <>
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
                  </>
                )}
              </div>
            </Card>
          ))}

          {admin && (
            <button
              onClick={() => nav('/users/new')}
              className="bg-white border-2 border-dashed border-slate-300 rounded-lg p-5 text-slate-400 hover:border-brand-700 hover:text-brand-700 transition flex items-center justify-center min-h-[180px]"
            >
              ＋ 新增用户
            </button>
          )}
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
