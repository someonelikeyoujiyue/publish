/**
 * 公众号 AppID / AppSecret 绑定弹窗。
 *
 * 触发时机：用户点「推送到公众号草稿箱」，后端 409 返回 need_binding=true。
 * 输入 AppID + AppSecret → PUT /users/{id} 更新 → 关 modal → 调用方 onBound 自动重推。
 */
import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Btn, Flash } from '@/components/ui'

interface Props {
  userId:  string
  onClose: () => void
  onBound: () => void
}

export function WechatBindModal({ userId, onClose, onBound }: Props) {
  const [appId,     setAppId]     = useState('')
  const [appSecret, setAppSecret] = useState('')

  const save = useMutation({
    mutationFn: () => api.updateUser(userId, {
      wechat_app_id: appId.trim(),
      wechat_app_secret: appSecret.trim(),
    }),
    onSuccess: () => onBound(),
  })

  // ESC 关闭
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  const inputCls =
    'w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50'

  const canSubmit = appId.trim().length > 0 && appSecret.trim().length > 0 && !save.isPending

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg max-w-md w-full p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold mb-1">绑定公众号</h3>
        <p className="text-xs text-slate-500 mb-4">
          这个用户 <span className="font-mono">{userId}</span> 还没填 AppID / AppSecret，填一下就能推送。
        </p>

        <div className="text-xs text-slate-600 bg-amber-50 border-l-4 border-amber-300 rounded p-3 leading-relaxed mb-4">
          <div className="font-semibold text-amber-900 mb-1">📝 怎么取 AppID / AppSecret</div>
          1. 登录 <a
            href="https://mp.weixin.qq.com/"
            target="_blank"
            rel="noopener"
            className="underline text-brand-700"
          >mp.weixin.qq.com</a> 公众号后台<br />
          2. 左侧菜单 →「设置与开发」→「基本配置」<br />
          3.「公众号开发信息」区域复制 <b>开发者 ID(AppID)</b><br />
          4. AppSecret 点旁边的「重置」（注意：旧 secret 会立即失效）→ 短信验证后显示完整值<br />
          5. 你的服务器出口 IP 也要加到「IP 白名单」（部署文档里有）
        </div>

        <form
          onSubmit={(e) => { e.preventDefault(); if (canSubmit) save.mutate() }}
          className="space-y-3"
        >
          <div>
            <label className="block text-sm text-slate-700 font-medium mb-1">公众号 AppID</label>
            <input
              autoFocus
              value={appId}
              onChange={(e) => setAppId(e.target.value)}
              placeholder="wx 开头的 18 位字符串"
              className={inputCls + ' font-mono'}
              required
            />
          </div>
          <div>
            <label className="block text-sm text-slate-700 font-medium mb-1">公众号 AppSecret</label>
            <input
              type="password"
              value={appSecret}
              onChange={(e) => setAppSecret(e.target.value)}
              placeholder="32 位 hex 字符串"
              className={inputCls + ' font-mono'}
              required
            />
          </div>

          {save.error && <Flash tone="error">{(save.error as Error).message}</Flash>}

          <div className="flex gap-2 pt-2">
            <Btn type="submit" loading={save.isPending} disabled={!canSubmit}>
              {save.isPending ? '保存中…' : '保存并重新推送'}
            </Btn>
            <Btn type="button" variant="secondary" onClick={onClose}>取消</Btn>
          </div>
        </form>
      </div>
    </div>
  )
}
