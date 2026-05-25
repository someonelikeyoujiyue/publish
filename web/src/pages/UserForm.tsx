import { useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { isAdmin } from '@/lib/auth'
import { Btn, Flash } from '@/components/ui'

type PlatformKey = 'wechat' | 'xhs' | 'toutiao' | 'douyin'
const PLATFORMS: { key: PlatformKey; label: string; hint: string }[] = [
  { key: 'wechat',  label: '公众号',   hint: '官方 API 推草稿箱（需绑 AppID/Secret）' },
  { key: 'xhs',     label: '小红书',   hint: 'myaibot 生二维码扫码确认' },
  { key: 'toutiao', label: '头条号',   hint: 'CDP DOM 自动填表（需扫码绑定）' },
  { key: 'douyin',  label: '抖音图文', hint: '用户复制 + 跳 creator.douyin.com 自己粘贴' },
]
const ALL_KEYS: PlatformKey[] = PLATFORMS.map(p => p.key)

function Form({ mode, defaultValues }: {
  mode: 'new' | 'edit'
  defaultValues: { id: string; name: string; enabled_platforms: PlatformKey[] }
}) {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [form, setForm] = useState({
    id: defaultValues.id,
    name: defaultValues.name,
    enabled_platforms: defaultValues.enabled_platforms,
  })

  const togglePlatform = (k: PlatformKey) => {
    setForm(f => ({
      ...f,
      enabled_platforms: f.enabled_platforms.includes(k)
        ? f.enabled_platforms.filter(x => x !== k)
        : [...f.enabled_platforms, k],
    }))
  }

  const m = useMutation({
    mutationFn: async () => {
      if (mode === 'new') {
        return api.createUser({
          id: form.id, name: form.name,
          enabled_platforms: form.enabled_platforms,
        })
      }
      return api.updateUser(defaultValues.id, {
        name: form.name || undefined,
        enabled_platforms: form.enabled_platforms,
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

        <Field
          label="每日仿写平台"
          hint="cron 0:00 触发；勾上才生成内容。可以晚点改"
        >
          <div className="space-y-2 mt-1">
            {PLATFORMS.map(p => (
              <label key={p.key} className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-1 accent-brand-600"
                  checked={form.enabled_platforms.includes(p.key)}
                  onChange={() => togglePlatform(p.key)}
                />
                <span className="text-sm">
                  <span className="font-medium text-slate-800">{p.label}</span>
                  <span className="text-xs text-slate-500 ml-2">{p.hint}</span>
                </span>
              </label>
            ))}
            {form.enabled_platforms.length === 0 && (
              <div className="text-xs text-amber-700">⚠️ 一个都不勾 = 这个用户每天不生成任何内容</div>
            )}
          </div>
        </Field>

        <div className="text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded p-3 leading-relaxed">
          <div className="font-semibold text-slate-700 mb-1">💡 公众号 AppID / AppSecret 怎么办？</div>
          这里 <b>不用填</b>。等你第一次点「推送到草稿箱」按钮时，会弹绑定窗，里面有取 key 的步骤说明。
          也就是说：没绑也能新建用户，按需绑就行。
        </div>

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
  if (!isAdmin()) return <Navigate to="/" replace />
  return (
    <Form
      mode="new"
      defaultValues={{ id: '', name: '', enabled_platforms: ALL_KEYS }}
    />
  )
}

export function UserEdit() {
  const { userId = '' } = useParams()
  if (!isAdmin()) return <Navigate to="/" replace />
  const { data, isLoading, error } = useQuery({
    queryKey: ['user', userId],
    queryFn: () => api.getUser(userId),
    enabled: !!userId,
  })
  if (isLoading) return <p className="text-slate-500">加载中…</p>
  if (error) return <Flash tone="error">加载失败：{(error as Error).message}</Flash>
  if (!data) return <p className="text-slate-500">用户不存在</p>
  // 后端永远返回数组（缺省 = 4 个全开）。空数组表示用户主动把所有平台都关了，
  // 不要在这里"善意"展开成全选——会改变用户保存的意图。
  const enabled = (data.enabled_platforms ?? []) as PlatformKey[]
  return (
    <Form
      mode="edit"
      defaultValues={{ id: data.id, name: data.name, enabled_platforms: enabled }}
    />
  )
}
