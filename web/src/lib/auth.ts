export type Role = 'admin' | 'editor' | 'user'

const KEY = 'publisher-hub-auth'

interface Session {
  token: string
  role: Role
  expires_at: number  // unix seconds
}

export function saveSession(s: Session): void {
  localStorage.setItem(KEY, JSON.stringify(s))
}

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return null
    const s = JSON.parse(raw) as Session
    if (s.expires_at * 1000 < Date.now()) {
      localStorage.removeItem(KEY)
      return null
    }
    return s
  } catch {
    return null
  }
}

export function clearSession(): void {
  localStorage.removeItem(KEY)
}

export function getRole(): Role | null {
  return getSession()?.role ?? null
}

export function isAdmin(): boolean {
  return getRole() === 'admin'
}

/** 能否仿写 / 推送等"写"动作：admin 或 editor。user 只读不行。 */
export function canWrite(): boolean {
  const r = getRole()
  return r === 'admin' || r === 'editor'
}

export function isLoggedIn(): boolean {
  return getSession() !== null
}

export function getToken(): string | null {
  return getSession()?.token ?? null
}
