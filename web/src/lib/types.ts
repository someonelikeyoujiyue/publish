export interface User {
  id: string
  name: string
  wechat: { app_id?: string; proxy?: string; author?: string }
  xhs: { display_name?: string }
  toutiao: { cdp_port?: number }
  wechat_count: number
  xhs_count: number
}

export type Platform = 'wechat' | 'xhs'

export type DraftStatus = 'ready' | 'pushed' | 'failed'

export interface DraftSummary {
  id: number
  title: string
  status: DraftStatus
  created_at: string
  pushed_at: string | null
  error: string | null
}

export interface DraftDetail extends DraftSummary {
  content: string
  content_html?: string
  images: string[]
  pushed_result?: string
  qr_url?: string
}

export type ToutiaoStatus =
  | { status: 'unbound' }
  | { status: 'logged_out'; url?: string; reason?: string }
  | { status: 'logged_in'; name?: string; cookie_expires_days?: number | null; url?: string }
  | { status: 'error'; error: string }

export interface BindResult {
  ok: boolean
  qr_image?: string
  already_logged_in?: boolean
  port?: number
}
