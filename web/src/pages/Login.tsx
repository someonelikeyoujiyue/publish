import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { authApi } from '@/lib/api'
import { saveSession } from '@/lib/auth'
import { Btn, Flash } from '@/components/ui'

export function Login() {
  const nav = useNavigate()
  const [params] = useSearchParams()
  const reason = params.get('reason')
  const [form, setForm] = useState({ username: '', password: '' })

  const m = useMutation({
    mutationFn: () => authApi.login(form.username.trim(), form.password),
    onSuccess: (data) => {
      saveSession({ token: data.token, role: data.role, expires_at: data.expires_at })
      const next = params.get('next') || '/'
      nav(next, { replace: true })
    },
  })

  const inputCls = 'w-full border border-slate-300 rounded-md px-3 py-2.5 text-sm focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50'

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-brand-50 via-slate-50 to-amber-50 px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-6">
          <div className="text-2xl mb-1">📤</div>
          <h1 className="text-xl font-bold text-brand-700">Publisher Hub</h1>
          <p className="text-xs text-slate-500 mt-1">登录后管理草稿和推送</p>
        </div>

        <form
          onSubmit={(e) => { e.preventDefault(); m.mutate() }}
          className="bg-white rounded-lg shadow-sm border border-slate-200 p-6 space-y-4"
        >
          {reason === 'expired' && (
            <Flash tone="info">登录已过期，请重新登录</Flash>
          )}

          <div>
            <label className="block text-sm text-slate-700 mb-1.5">账号</label>
            <input
              autoFocus
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              className={inputCls}
              placeholder="admin / user"
              required
            />
          </div>
          <div>
            <label className="block text-sm text-slate-700 mb-1.5">密码</label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className={inputCls}
              required
            />
          </div>

          {m.error && <Flash tone="error">{(m.error as Error).message}</Flash>}

          <Btn type="submit" loading={m.isPending} className="w-full justify-center">
            {m.isPending ? '登录中…' : '登录'}
          </Btn>
        </form>

        <p className="text-center text-[11px] text-slate-400 mt-4">
          管理员可仿写/推送/管理用户，普通用户只能查看 + 推送已生成的草稿
        </p>
      </div>
    </div>
  )
}
