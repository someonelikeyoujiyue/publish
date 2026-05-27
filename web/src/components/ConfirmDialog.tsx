/**
 * 通用确认对话框。用来替代浏览器原生 confirm()。
 *
 * 用法：
 *   const [open, setOpen] = useState(false)
 *   ...
 *   <ConfirmDialog
 *     open={open}
 *     title="删除草稿"
 *     message="删了就找不回来"
 *     tone="danger"
 *     confirmText="删"
 *     onConfirm={() => { doDelete(); setOpen(false) }}
 *     onCancel={() => setOpen(false)}
 *   />
 */
import { useEffect, type ReactNode } from 'react'
import { Btn } from '@/components/ui'

interface Props {
  open: boolean
  title: string
  message?: ReactNode
  /** danger = 红确认按钮（删除类）；normal = brand 蓝（中性确认）。默认 normal */
  tone?: 'danger' | 'normal'
  confirmText?: string
  cancelText?: string
  onConfirm: () => void
  onCancel: () => void
  /** confirm 按钮是否 loading（mutation pending 时用） */
  loading?: boolean
}

export function ConfirmDialog({
  open, title, message, tone = 'normal',
  confirmText = '确认', cancelText = '取消',
  onConfirm, onCancel, loading = false,
}: Props) {
  // ESC 关
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && !loading && onCancel()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, loading, onCancel])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={() => !loading && onCancel()}
    >
      <div
        className="bg-white rounded-lg max-w-sm w-full p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold mb-2 text-slate-800">{title}</h3>
        {message && (
          <div className="text-sm text-slate-600 leading-relaxed mb-4">{message}</div>
        )}
        <div className="flex gap-2 justify-end">
          <Btn variant="secondary" onClick={onCancel} disabled={loading}>
            {cancelText}
          </Btn>
          <Btn
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={loading}
          >
            {confirmText}
          </Btn>
        </div>
      </div>
    </div>
  )
}
