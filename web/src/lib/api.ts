import type {
  User, Platform, DraftSummary, DraftDetail, ToutiaoStatus, BindResult,
  YoutubeDraft, YoutubeDraftDetail, VideoJob,
} from './types'
import { getToken, clearSession } from './auth'

const BASE = '/api'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  // 透传 Bearer token；登录/me 接口也带（无害）
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (init?.headers) Object.assign(headers, init.headers as Record<string, string>)

  const res = await fetch(BASE + path, { ...init, headers })
  if (res.status === 401) {
    // token 过期 / 失效 → 清掉本地 session，跳 login
    clearSession()
    if (!location.pathname.startsWith('/login')) {
      location.href = '/login?reason=expired'
    }
  }
  if (!res.ok) {
    const text = await res.text()
    let msg = text
    try { msg = JSON.parse(text).detail || text } catch { /* not json */ }
    throw new Error(`HTTP ${res.status}: ${msg}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

type RoleResp = 'admin' | 'editor' | 'user'
interface LoginResp { token: string; role: RoleResp; expires_at: number }
export const authApi = {
  login:  (username: string, password: string) =>
    req<LoginResp>('/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () =>
    req<{ ok: boolean }>('/logout', { method: 'POST', body: '{}' }),
  me:     () =>
    req<{ role: RoleResp; expires_at: number }>('/me'),
}

export const api = {
  // users
  listUsers: () => req<{ users: User[] }>('/users'),
  getUser:   (id: string) => req<User>(`/users/${id}`),
  createUser: (body: {
    id: string; name: string;
    wechat_app_id?: string; wechat_app_secret?: string;
    enabled_platforms?: string[];
  }) => req<User>('/users', { method: 'POST', body: JSON.stringify(body) }),
  updateUser: (id: string, body: {
    name?: string;
    wechat_app_id?: string; wechat_app_secret?: string;
    enabled_platforms?: string[];
  }) => req<User>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
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
  push: async (
    userId: string, platform: Platform, id: number, opts: { draft_only?: boolean } = {},
  ): Promise<{
    ok: boolean; need_binding?: boolean;
    media_id?: string; qr_url?: string; error?: string; final_url?: string; mode?: string;
  }> => {
    // 不走 req 包装，因为我们要把 409 当业务返回值（need_binding 弹绑定 modal），
    // 而不是抛异常。
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
    const res = await fetch(
      `${BASE}/users/${userId}/${platform}/drafts/${id}/push`,
      { method: 'POST', headers, body: JSON.stringify(opts) },
    )
    if (res.status === 401) {
      clearSession()
      if (!location.pathname.startsWith('/login')) location.href = '/login?reason=expired'
      throw new Error('登录已过期')
    }
    const bodyText = await res.text()
    let body: { detail?: { need_binding?: boolean; message?: string } } & Record<string, unknown> = {}
    try { body = bodyText ? JSON.parse(bodyText) : {} } catch { /* not json */ }
    // FastAPI HTTPException(409, detail={...}) 实际响应是 {"detail": {...}}
    const detail = body.detail
    if (res.status === 409 && detail && typeof detail === 'object' && detail.need_binding) {
      return { ok: false, need_binding: true, error: detail.message || '需要先绑定公众号' }
    }
    if (!res.ok) {
      const msg = (detail && typeof detail === 'object' ? detail.message : undefined)
        ?? (typeof detail === 'string' ? detail : undefined)
        ?? bodyText
      throw new Error(`HTTP ${res.status}: ${msg}`)
    }
    return body as { ok: boolean; media_id?: string; qr_url?: string; error?: string; final_url?: string; mode?: string }
  },

  // toutiao
  toutiaoStatus: (userId: string) =>
    req<ToutiaoStatus>(`/users/${userId}/toutiao/status`),
  toutiaoBind: (userId: string) =>
    req<BindResult>(`/users/${userId}/toutiao/bind`, { method: 'POST', body: '{}' }),
  toutiaoUnbind: (userId: string) =>
    req<{ ok: boolean }>(`/users/${userId}/toutiao/unbind`, { method: 'POST', body: '{}' }),

  // youtube
  ytList: (userId: string) =>
    req<{ drafts: YoutubeDraft[] }>(`/users/${userId}/youtube/drafts`),
  ytGet:  (userId: string, id: number) =>
    req<YoutubeDraftDetail>(`/users/${userId}/youtube/drafts/${id}`),
  ytSubmit: (userId: string, body: { url: string; strip_hardsub: boolean; blur_qr: boolean }) =>
    req<{ ok: boolean; draft_id: number; status: string }>(
      `/users/${userId}/youtube/submit`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  ytDelete: (userId: string, draftId: number) =>
    req<{ ok: boolean; draft_id: number; dir_removed: boolean; slug: string | null }>(
      `/users/${userId}/youtube/drafts/${draftId}`,
      { method: 'DELETE' },
    ),

  ytUpload: async (
    userId: string,
    form: {
      video: File
      bilingual_srt?: File | null
      en_srt?: File | null
      zh_srt?: File | null
      source_url?: string
      title?: string
    },
  ): Promise<{ ok: boolean; draft_id: number; video_url: string; slug: string }> => {
    const fd = new FormData()
    fd.append('video', form.video)
    if (form.bilingual_srt) fd.append('bilingual_srt', form.bilingual_srt)
    if (form.en_srt)        fd.append('en_srt',        form.en_srt)
    if (form.zh_srt)        fd.append('zh_srt',        form.zh_srt)
    fd.append('source_url', form.source_url ?? '')
    fd.append('title',      form.title      ?? '')
    const headers: Record<string, string> = {}
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
    // 注意：FormData 时不能设 Content-Type，浏览器会自动加 multipart boundary
    const res = await fetch(`${BASE}/users/${userId}/youtube/upload`, {
      method: 'POST', headers, body: fd,
    })
    if (res.status === 401) {
      clearSession()
      if (!location.pathname.startsWith('/login')) location.href = '/login?reason=expired'
    }
    if (!res.ok) {
      const text = await res.text()
      let msg = text
      try { msg = JSON.parse(text).detail || text } catch { /* not json */ }
      throw new Error(`HTTP ${res.status}: ${msg}`)
    }
    return res.json()
  },

  // ── video（短视频生成） ─────────────────────────────────────────────
  videoOptions: () =>
    req<{
      voices: { key: string; code: string }[]
      default_voice: string
      default_rate: string
      silent_voice: string
    }>('/video/options'),

  videoPreview: (
    userId: string,
    body: { topic: string; narrations: string; n_scenes: number },
  ) =>
    req<{
      topic: string
      title: string
      narrations: string[]
      default_image_urls: string[]
      source: 'user_narrations' | 'user_topic' | 'db_seed' | 'fallback'
    }>(`/users/${userId}/video/preview`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  videoJobs: (userId: string) =>
    req<{ jobs: VideoJob[] }>(`/users/${userId}/video/jobs`),

  videoJob: (userId: string, jobId: number) =>
    req<VideoJob>(`/users/${userId}/video/jobs/${jobId}`),

  videoSubmit: async (
    userId: string,
    form: {
      topic: string
      title?: string
      narrations?: string
      n_scenes?: number
      voice?: string
      rate?: string
      bgm_mode?: 'default' | 'upload' | 'none'
      bgm_file?: File | null
      images?: File[]
    },
  ): Promise<{ ok: boolean; job_id: number; status: string }> => {
    const fd = new FormData()
    fd.append('topic',      form.topic)
    fd.append('title',      form.title ?? '')
    fd.append('narrations', form.narrations ?? '')
    fd.append('n_scenes',   String(form.n_scenes ?? 3))
    fd.append('voice',      form.voice ?? 'zh-xiaoxiao-female')
    fd.append('rate',       form.rate ?? '+5%')
    fd.append('bgm_mode',   form.bgm_mode ?? 'default')
    if (form.bgm_file) fd.append('bgm', form.bgm_file)
    for (const f of form.images ?? []) {
      fd.append('images', f)
    }
    const headers: Record<string, string> = {}
    const token = getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
    const res = await fetch(`${BASE}/users/${userId}/video/generate`, {
      method: 'POST', headers, body: fd,
    })
    if (res.status === 401) {
      clearSession()
      if (!location.pathname.startsWith('/login')) location.href = '/login?reason=expired'
      throw new Error('登录已过期')
    }
    if (!res.ok) {
      const text = await res.text()
      let msg = text
      try { msg = JSON.parse(text).detail || text } catch { /* not json */ }
      throw new Error(`HTTP ${res.status}: ${msg}`)
    }
    return res.json()
  },

  videoDelete: (userId: string, jobId: number) =>
    req<{ ok: boolean; job_id: number; dir_removed: boolean }>(
      `/users/${userId}/video/jobs/${jobId}`,
      { method: 'DELETE' },
    ),

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
