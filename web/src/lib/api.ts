import type {
  User, Platform, DraftSummary, DraftDetail, ToutiaoStatus, BindResult,
} from './types'

const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    let msg = text
    try { msg = JSON.parse(text).detail || text } catch { /* not json */ }
    throw new Error(`HTTP ${res.status}: ${msg}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  // users
  listUsers: () => req<{ users: User[] }>('/users'),
  getUser:   (id: string) => req<User>(`/users/${id}`),
  createUser: (body: { id: string; name: string; wechat_app_id: string; wechat_app_secret: string }) =>
    req<User>('/users', { method: 'POST', body: JSON.stringify(body) }),
  updateUser: (id: string, body: { name?: string; wechat_app_id?: string; wechat_app_secret?: string }) =>
    req<User>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteUser: (id: string) =>
    req<void>(`/users/${id}`, { method: 'DELETE' }),

  // drafts (wechat / xhs 同接口形状)
  listDrafts: (userId: string, platform: Platform) =>
    req<{ drafts: DraftSummary[] }>(`/users/${userId}/${platform}/drafts`),
  getDraft: (userId: string, platform: Platform, id: number) =>
    req<DraftDetail>(`/users/${userId}/${platform}/drafts/${id}`),
  refresh: (userId: string, platform: Platform) =>
    req<{ ok: boolean; new_count?: number; error?: string }>(
      `/users/${userId}/${platform}/refresh`, { method: 'POST', body: '{}' },
    ),
  push: (userId: string, platform: Platform, id: number) =>
    req<{ ok: boolean; media_id?: string; qr_url?: string; error?: string }>(
      `/users/${userId}/${platform}/drafts/${id}/push`,
      { method: 'POST', body: '{}' },
    ),

  // toutiao
  toutiaoStatus: (userId: string) =>
    req<ToutiaoStatus>(`/users/${userId}/toutiao/status`),
  toutiaoBind: (userId: string) =>
    req<BindResult>(`/users/${userId}/toutiao/bind`, { method: 'POST', body: '{}' }),
  toutiaoUnbind: (userId: string) =>
    req<{ ok: boolean }>(`/users/${userId}/toutiao/unbind`, { method: 'POST', body: '{}' }),

  // admin
  runNow: (userId?: string) =>
    req<{ ok: boolean; target: string | null }>(
      '/admin/run-now',
      { method: 'POST', body: JSON.stringify({ user_id: userId ?? null }) },
    ),
  schedulerStatus: () =>
    req<{ running: boolean; job_id?: string; cron?: string; next?: string | null }>(
      '/admin/scheduler-status',
    ),
}
