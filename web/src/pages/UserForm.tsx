import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

function Form({ mode, defaultValues }: {
  mode: 'new' | 'edit'
  defaultValues: { id: string; name: string; wechat_app_id: string }
}) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [form, setForm] = useState({
    id: defaultValues.id, name: defaultValues.name,
    wechat_app_id: defaultValues.wechat_app_id, wechat_app_secret: '',
  })

  const m = useMutation({
    mutationFn: async () => {
      if (mode === 'new') {
        return api.createUser({
          id: form.id, name: form.name,
          wechat_app_id: form.wechat_app_id, wechat_app_secret: form.wechat_app_secret,
        })
      }
      return api.updateUser(defaultValues.id, {
        name: form.name || undefined,
        wechat_app_id: form.wechat_app_id || undefined,
        wechat_app_secret: form.wechat_app_secret || undefined,
      })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      nav('/')
    },
  })

  return (
    <div className="max-w-lg">
      <h1 className="text-xl font-semibold mb-4">
        {mode === 'new' ? '新增用户' : `编辑 ${defaultValues.id}`}
      </h1>
      <form
        onSubmit={(e) => { e.preventDefault(); m.mutate() }}
        className="bg-white border border-slate-200 rounded p-5 space-y-4"
      >
        <div>
          <label className="block text-sm text-slate-600 mb-1">用户 ID</label>
          <input
            value={form.id}
            onChange={(e) => setForm({ ...form, id: e.target.value })}
            disabled={mode === 'edit'}
            placeholder="小写字母/数字/_-，1-32 字"
            className="w-full border border-slate-300 rounded px-3 py-1.5 text-sm disabled:bg-slate-50"
            required
          />
        </div>
        <div>
          <label className="block text-sm text-slate-600 mb-1">显示名</label>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full border border-slate-300 rounded px-3 py-1.5 text-sm"
            required={mode === 'new'}
          />
        </div>
        <div>
          <label className="block text-sm text-slate-600 mb-1">公众号 AppID</label>
          <input
            value={form.wechat_app_id}
            onChange={(e) => setForm({ ...form, wechat_app_id: e.target.value })}
            className="w-full border border-slate-300 rounded px-3 py-1.5 text-sm font-mono"
            required={mode === 'new'}
          />
        </div>
        <div>
          <label className="block text-sm text-slate-600 mb-1">
            公众号 AppSecret {mode === 'edit' && <span className="text-slate-400">（留空不改）</span>}
          </label>
          <input
            type="password"
            value={form.wechat_app_secret}
            onChange={(e) => setForm({ ...form, wechat_app_secret: e.target.value })}
            className="w-full border border-slate-300 rounded px-3 py-1.5 text-sm font-mono"
            required={mode === 'new'}
          />
        </div>
        {m.error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-2">
            {(m.error as Error).message}
          </div>
        )}
        <div className="flex gap-2 pt-2">
          <button
            type="submit"
            disabled={m.isPending}
            className="px-4 py-1.5 bg-violet-600 text-white rounded text-sm hover:bg-violet-700 disabled:opacity-50"
          >
            {m.isPending ? '保存中…' : '保存'}
          </button>
          <button
            type="button"
            onClick={() => nav('/')}
            className="px-4 py-1.5 border border-slate-300 rounded text-sm hover:bg-slate-50"
          >
            取消
          </button>
        </div>
      </form>
    </div>
  )
}

export function UserNew() {
  return <Form mode="new" defaultValues={{ id: '', name: '', wechat_app_id: '' }} />
}

export function UserEdit() {
  const { userId = '' } = useParams()
  const { data, isLoading } = useQuery({
    queryKey: ['user', userId],
    queryFn: () => api.getUser(userId),
    enabled: !!userId,
  })
  if (isLoading) return <p className="text-slate-500">加载中…</p>
  if (!data) return <p className="text-slate-500">用户不存在</p>
  return (
    <Form
      mode="edit"
      defaultValues={{
        id: data.id,
        name: data.name,
        wechat_app_id: data.wechat.app_id || '',
      }}
    />
  )
}
