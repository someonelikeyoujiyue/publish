import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Btn({
  variant = 'primary', loading, children, className = '', ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  loading?: boolean
}) {
  const base = 'inline-flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition disabled:opacity-60 disabled:cursor-not-allowed'
  const variants = {
    primary:   'bg-brand-700 text-white hover:bg-brand-600',
    secondary: 'bg-white text-slate-700 border border-slate-200 hover:border-brand-700 hover:text-brand-700',
    ghost:     'bg-transparent text-slate-500 hover:text-brand-700',
    danger:    'bg-red-50 text-red-700 border border-red-200 hover:bg-red-100',
  }
  return (
    <button {...props} disabled={loading || props.disabled} className={`${base} ${variants[variant]} ${className}`}>
      {loading && <span className={`spinner ${variant === 'primary' ? 'spinner-white' : ''}`} />}
      {children}
    </button>
  )
}

export function BtnLink({
  to, variant = 'primary', children, className = '',
}: { to: string; variant?: 'primary' | 'secondary' | 'ghost'; children: ReactNode; className?: string }) {
  const variants = {
    primary:   'bg-brand-700 text-white hover:bg-brand-600',
    secondary: 'bg-white text-slate-700 border border-slate-200 hover:border-brand-700 hover:text-brand-700',
    ghost:     'bg-transparent text-slate-500 hover:text-brand-700',
  }
  return (
    <Link
      to={to}
      className={`inline-flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium transition ${variants[variant]} ${className}`}
    >
      {children}
    </Link>
  )
}

export function Card({ children, hover, className = '' }: { children: ReactNode; hover?: boolean; className?: string }) {
  return (
    <div className={`bg-white border border-slate-200 rounded-lg p-5 transition ${
      hover ? 'hover:border-brand-700 hover:shadow-md' : ''
    } ${className}`}>
      {children}
    </div>
  )
}

export function Badge({ kind, children }: { kind: 'ready' | 'pushed' | 'failed' | 'platform'; children: ReactNode }) {
  const colors = {
    ready:    'bg-emerald-100 text-emerald-800',
    pushed:   'bg-blue-100    text-blue-800',
    failed:   'bg-red-100     text-red-800',
    platform: 'bg-brand-50    text-brand-700',
  }
  return <span className={`inline-block px-2 py-px rounded text-xs font-medium ${colors[kind]}`}>{children}</span>
}

export function Empty({ icon, title, action }: { icon: string; title: string; action?: ReactNode }) {
  return (
    <div className="text-center py-20 px-5 bg-white rounded-lg border border-dashed border-slate-300">
      <div className="text-4xl opacity-40 mb-3">{icon}</div>
      <div className="text-slate-500 text-[15px] mb-4">{title}</div>
      {action}
    </div>
  )
}

export function Flash({ tone = 'info', children }: { tone?: 'info' | 'success' | 'error'; children: ReactNode }) {
  const tones = {
    info:    'bg-cyan-50    text-cyan-800    border-cyan-400',
    success: 'bg-emerald-50 text-emerald-800 border-emerald-400',
    error:   'bg-red-50     text-red-800     border-red-400',
  }
  return (
    <div className={`px-4 py-3 mb-4 border-l-4 rounded text-sm ${tones[tone]}`}>
      {children}
    </div>
  )
}
