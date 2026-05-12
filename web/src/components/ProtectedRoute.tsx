import { Navigate, useLocation } from 'react-router-dom'
import { isLoggedIn } from '@/lib/auth'

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const loc = useLocation()
  if (!isLoggedIn()) {
    return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname)}`} replace />
  }
  return <>{children}</>
}
