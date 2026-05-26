export interface User {
  id: string
  name: string
  wechat: { app_id?: string; proxy?: string; author?: string; bound?: boolean }
  xhs: { display_name?: string }
  toutiao: { cdp_port?: number }
  /** 启用每日仿写的平台子集；缺省 = 全开 */
  enabled_platforms?: ('wechat' | 'xhs' | 'toutiao' | 'douyin')[]
  wechat_count: number
  xhs_count: number
  toutiao_count: number
  douyin_count: number
  youtube_count: number
}

export interface VideoJob {
  id: number
  user_id: string
  topic: string
  title: string
  narrations: string[]
  image_count: number
  status: 'pending' | 'processing' | 'done' | 'failed'
  video_url: string             // e.g. "/video-jobs/42/output.mp4"
  duration_sec: number | null
  file_size: number | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface YoutubeDraft {
  id: number
  title: string
  status: 'ready' | 'processing' | 'pushed' | 'failed'
  created_at: string
  pushed_at: string | null
  error: string | null
  source_url: string
  video_url: string
}

export interface YoutubeDraftDetail extends YoutubeDraft {
  content: string  // bilingual SRT
}

export type Platform = 'wechat' | 'xhs' | 'toutiao' | 'douyin' | 'youtube'

export type DraftStatus = 'ready' | 'pushed' | 'failed'

export interface DraftSummary {
  id: number
  title: string
  status: DraftStatus
  created_at: string
  pushed_at: string | null
  error: string | null
  cover: string
  image_count: number
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
