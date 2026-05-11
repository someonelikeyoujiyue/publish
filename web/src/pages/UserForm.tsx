import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Btn, Flash } from '@/components/ui'

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

  const inputCls = 'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50 disabled:bg-slate-50'

  return (
    <div className="max-w-lg">
      <h2 className="text-lg font-semibold mb-4">
        {mode === 'new' ? '新增用户' : `编辑 ${defaultValues.id}`}
      </h2>
      <form
        onSubmit={(e) => { e.preventDefault(); m.mutate() }}
        className="bg-white border border-slate-200 rounded-lg p-6 space-y-4"
      >
        <Field label="用户 ID" hint="小写字母/数字/下划线/连字符，1-32 字">
          <input
            value={form.id}
            onChange={(e) => setForm({ ...form, id: e.target.value })}
            disabled={mode === 'edit'}
            className={inputCls + ' font-mono'}
            required
          />
        </Field>
        <Field label="显示名">
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className={inputCls}
            required={mode === 'new'}
          />
        </Field>
        <Field label="公众号 AppID">
          <input
            value={form.wechat_app_id}
            onChange={(e) => setForm({ ...form, wechat_app_id: e.target.value })}
            className={inputCls + ' font-mono'}
            required={mode === 'new'}
          />
        </Field>
        <Field label="公众号 AppSecret" hint={mode === 'edit' ? '留空不改' : undefined}>
          <input
            type="password"
            value={form.wechat_app_secret}
            onChange={(e) => setForm({ ...form, wechat_app_secret: e.target.value })}
            className={inputCls + ' font-mono'}
            required={mode === 'new'}
          />
        </Field>

        {m.error && <Flash tone="error">{(m.error as Error).message}</Flash>}

        <div className="flex gap-2 pt-2">
          <Btn type="submit" loading={m.isPending}>
            {m.isPending ? '保存中…' : '保存'}
          </Btn>
          <Btn type="button" variant="secondary" onClick={() => nav('/')}>取消</Btn>
        </div>
      </form>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="flex justify-between items-baseline mb-1">
        <span className="text-sm text-slate-700 font-medium">{label}</span>
        {hint && <span className="text-xs text-slate-400">{hint}</span>}
      </label>
      {children}
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
        id: data.id, name: data.name,
        wechat_app_id: data.wechat.app_id || '',
      }}
    />
  )
}
