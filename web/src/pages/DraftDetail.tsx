import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { canWrite } from '@/lib/auth'
import { Btn, Badge, Flash } from '@/components/ui'
import type { Platform } from '@/lib/types'
import { WechatBindModal } from '@/components/WechatBindModal'
import { ConfirmDialog } from '@/components/ConfirmDialog'

const PLATFORM_LABEL = { wechat: '公众号', xhs: '小红书', toutiao: '微头条', douyin: '抖音图文', youtube: 'YouTube' } as const

// 各平台发布页（手动模式跳转用）
const PUBLISH_URL: Partial<Record<Platform, string>> = {
  toutiao: 'https://mp.toutiao.com/profile_v4/weitoutiao/publish',
  douyin:  'https://creator.douyin.com/creator-micro/content/upload?default-tab=3',
}

export function DraftDetail({ platform }: { platform: Platform }) {
  const { userId = '', draftId = '' } = useParams()
  const qc = useQueryClient()
  const id = Number(draftId)
  const [lightbox, setLightbox] = useState<string | null>(null)
  const [copied, setCopied] = useState<'' | 'all' | 'title' | 'body'>('')
  // 公众号未绑 AppID/Secret 时弹绑定 modal
  const [bindOpen, setBindOpen] = useState(false)

  // ── xhs 专属：编辑文案 / 图片增删 / 重生 ──
  const editor = canWrite()
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')
  const uploadRef = useRef<HTMLInputElement | null>(null)
  const [editErr, setEditErr] = useState('')
  // 单一确认 modal：当前要确认的动作
  const [confirming, setConfirming] = useState<
    | { kind: 'del-image'; index: number }
    | { kind: 'toutiao-publish' }
    | null
  >(null)

  const { data, isLoading } = useQuery({
    queryKey: ['draft', userId, platform, id],
    queryFn: () => api.getDraft(userId, platform, id),
    enabled: !!userId && !!id,
  })

  // 头条号已绑定 + 在线状态（只在 toutiao 平台用）
  const { data: ttStatus } = useQuery({
    queryKey: ['toutiao', userId],
    queryFn: () => api.toutiaoStatus(userId),
    enabled: platform === 'toutiao' && !!userId,
    refetchInterval: 15_000,
  })

  const push = useMutation({
    mutationFn: () => api.push(userId, platform, id),
    onSuccess: (resp) => {
      // 公众号专属：缺绑定 → 弹绑定 modal（不算成功也不算失败，等绑定后重推）
      if (resp.need_binding) {
        setBindOpen(true)
        return
      }
      qc.invalidateQueries({ queryKey: ['draft', userId, platform, id] })
      qc.invalidateQueries({ queryKey: ['drafts', userId, platform] })
    },
  })

  // xhs 编辑 mutation 簇
  const invalidateDraft = () => {
    qc.invalidateQueries({ queryKey: ['draft', userId, platform, id] })
    qc.invalidateQueries({ queryKey: ['drafts', userId, platform] })
  }
  const onMutationError = (e: unknown) => setEditErr((e as Error).message)

  const saveEdit = useMutation({
    mutationFn: () => api.xhsUpdateDraft(userId, id, {
      title: editTitle, content: editContent,
    }),
    onSuccess: () => { setEditing(false); setEditErr(''); invalidateDraft() },
    onError: onMutationError,
  })

  const regenNarration = useMutation({
    mutationFn: () => api.xhsRegenNarration(userId, id),
    onSuccess: (resp) => {
      setEditTitle(resp.title || '')
      setEditContent(resp.content || '')
      setEditing(true)   // 直接进编辑态，让用户决定保不保留 LLM 新文案
      setEditErr('')
      invalidateDraft()
    },
    onError: onMutationError,
  })

  const delImage = useMutation({
    mutationFn: (index: number) => api.xhsDeleteImage(userId, id, index),
    onSuccess: () => { setEditErr(''); invalidateDraft() },
    onError: onMutationError,
  })

  const uploadImages = useMutation({
    mutationFn: (files: File[]) => api.xhsUploadImages(userId, id, files),
    onSuccess: () => { setEditErr(''); invalidateDraft() },
    onError: onMutationError,
  })

  const regenImage = useMutation({
    mutationFn: (index: number) => api.xhsRegenImage(userId, id, index),
    onSuccess: () => { setEditErr(''); invalidateDraft() },
    onError: onMutationError,
  })

  // 进入编辑态时填入当前文案
  const enterEdit = () => {
    if (!data) return
    setEditTitle(data.title || '')
    setEditContent(data.content || '')
    setEditErr('')
    setEditing(true)
  }
  // 数据刷新时如果在编辑态但 mutation 还没改完，避免被新数据覆盖；用户主动 enter/cancel 才同步
  useEffect(() => {
    if (!editing && data) {
      setEditTitle(data.title || '')
      setEditContent(data.content || '')
    }
  }, [data, editing])

  const saveDraftOnly = useMutation({
    mutationFn: () => api.push(userId, platform, id, { draft_only: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['draft', userId, platform, id] })
      qc.invalidateQueries({ queryKey: ['drafts', userId, platform] })
    },
  })

  const copyTo = async (kind: 'all' | 'title' | 'body') => {
    if (!data) return
    const text = kind === 'title' ? data.title
      : kind === 'body' ? data.content
      : `${data.title}\n\n${data.content}`
    try {
      await navigator.clipboard.writeText(text)
      setCopied(kind)
      setTimeout(() => setCopied(''), 2000)
    } catch (e) {
      alert('复制失败：' + (e as Error).message)
    }
  }

  if (isLoading) return <p className="text-slate-500">加载中…</p>
  if (!data) return <p className="text-slate-500">草稿不存在</p>

  const canPush = data.status === 'ready' || data.status === 'failed'
  const qrUrl = push.data?.qr_url || data.qr_url

  return (
    <>
      <div className="mb-4">
        <Link to={`/${userId}/${platform}`} className="text-sm text-slate-500 hover:text-brand-700">
          ← 返回 {PLATFORM_LABEL[platform]} 列表
        </Link>
      </div>

      <div className="grid gap-5" style={{ gridTemplateColumns: 'minmax(0,2fr) minmax(0,1fr)' }}>
        {/* Main */}
        <article className="bg-white rounded-lg border border-slate-200 px-9 py-8">
          <div className="flex gap-2 mb-3 items-center">
            <Badge kind={data.status === 'pushed' ? 'pushed' : data.status === 'failed' ? 'failed' : 'ready'}>
              {data.status}
            </Badge>
            <Badge kind="platform">{PLATFORM_LABEL[platform]}</Badge>
          </div>
          <h1 className="text-2xl font-bold leading-snug mb-3">{data.title || '(无标题)'}</h1>
          <div className="text-xs text-slate-400 pb-4 mb-6 border-b border-slate-200 flex gap-4 flex-wrap">
            <span>创建：{data.created_at}</span>
            {data.pushed_at && <span>推送：{data.pushed_at}</span>}
          </div>

          {data.status === 'failed' && data.error && (
            <Flash tone="error">推送错误：{data.error}</Flash>
          )}

          {platform === 'wechat' ? (
            <div
              className="prose-pub text-[16px] leading-[1.85]"
              dangerouslySetInnerHTML={{ __html: data.content_html || '' }}
            />
          ) : platform === 'xhs' ? (
            <>
              {/* xhs 编辑工具栏 */}
              {editor && (
                <div className="flex gap-2 mb-3 flex-wrap">
                  {!editing ? (
                    <>
                      <Btn variant="secondary" onClick={enterEdit}>✏️ 编辑文案</Btn>
                      <Btn
                        variant="secondary"
                        onClick={() => regenNarration.mutate()}
                        loading={regenNarration.isPending}
                      >
                        {regenNarration.isPending ? 'LLM 生成中…' : '🔄 重新生成文案'}
                      </Btn>
                    </>
                  ) : (
                    <>
                      <Btn onClick={() => saveEdit.mutate()} loading={saveEdit.isPending}>
                        {saveEdit.isPending ? '保存中…' : '💾 保存'}
                      </Btn>
                      <Btn variant="secondary" onClick={() => { setEditing(false); setEditErr('') }}>
                        ✗ 取消
                      </Btn>
                    </>
                  )}
                </div>
              )}
              {editErr && <Flash tone="error">{editErr}</Flash>}

              {editing ? (
                <div className="space-y-2">
                  <input
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    placeholder="标题"
                    className="w-full border border-slate-300 rounded-md px-3 py-2 text-base font-semibold focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50"
                  />
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    placeholder="正文（保留原格式，标签 #xxx 也可以保留）"
                    rows={14}
                    className="w-full border border-slate-300 rounded-md px-3 py-2 text-[15px] leading-[1.85] font-sans focus:outline-none focus:border-brand-600 focus:ring-2 focus:ring-brand-50"
                  />
                </div>
              ) : (
                <pre className="whitespace-pre-wrap font-sans text-[15px] leading-[1.85] text-slate-800">
                  {data.content}
                </pre>
              )}
            </>
          ) : (
            <pre className="whitespace-pre-wrap font-sans text-[15px] leading-[1.85] text-slate-800">
              {data.content}
            </pre>
          )}
        </article>

        {/* Sidebar */}
        <aside className="space-y-5">
          {(data.images.length > 0 || platform === 'xhs') && (
            <div className="bg-white rounded-lg border border-slate-200 p-5">
              <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-3 font-medium flex justify-between items-center">
                <span>{data.images.length} 张图</span>
                {platform === 'xhs' && editor && (
                  <button
                    onClick={() => uploadRef.current?.click()}
                    disabled={uploadImages.isPending}
                    className="text-brand-700 hover:underline normal-case tracking-normal text-xs disabled:opacity-50"
                  >
                    {uploadImages.isPending ? '上传中…' : '+ 上传图片'}
                  </button>
                )}
              </div>
              {data.images.length > 0 ? (
                <div className="grid grid-cols-2 gap-1.5">
                  {data.images.map((src, i) => (
                    <div key={i} className="relative group">
                      <button onClick={() => setLightbox(src)} className="block w-full">
                        <img src={src} className="w-full aspect-square object-cover rounded bg-slate-100 hover:opacity-80 transition" alt="" />
                      </button>
                      <div className="absolute top-1 left-1 bg-black/60 text-white text-[10px] font-semibold px-1.5 py-0.5 rounded">
                        {i + 1}
                      </div>
                      {platform === 'xhs' && editor && (
                        <div className="absolute top-1 right-1 flex gap-1 opacity-0 group-hover:opacity-100 transition">
                          <button
                            onClick={() => regenImage.mutate(i)}
                            disabled={regenImage.isPending}
                            className="bg-amber-500 hover:bg-amber-600 text-white w-6 h-6 rounded text-xs font-bold disabled:opacity-50"
                            title="从素材库换一张相关图"
                          >
                            🔄
                          </button>
                          <button
                            onClick={() => setConfirming({ kind: 'del-image', index: i })}
                            disabled={delImage.isPending}
                            className="bg-red-600 hover:bg-red-700 text-white w-6 h-6 rounded text-xs font-bold disabled:opacity-50"
                            title="删除这张"
                          >
                            ×
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-slate-400">还没图，点上方「+ 上传图片」加</p>
              )}

              {platform === 'xhs' && editor && (
                <input
                  ref={uploadRef}
                  type="file"
                  accept="image/*"
                  multiple
                  onChange={(e) => {
                    const fs = Array.from(e.target.files || [])
                    if (fs.length) uploadImages.mutate(fs)
                    if (uploadRef.current) uploadRef.current.value = ''
                  }}
                  className="hidden"
                />
              )}
            </div>
          )}

          <div className="bg-white rounded-lg border border-slate-200 p-5 sticky top-32">
            <div className="text-[11px] text-slate-400 uppercase tracking-wider mb-3 font-medium">操作</div>

            {/* ── 微头条 / 抖音：手动复制 + 跳转发布页 ─────────────────── */}
            {(platform === 'toutiao' || platform === 'douyin') ? (
              <div className="space-y-3">
                {/* 状态 */}
                <div className="text-[12px] text-slate-500 pb-2 border-b border-slate-100">
                  状态：
                  {data.status === 'pushed' ? <span className="text-emerald-700">✓ 已发布</span>
                    : data.status === 'failed' ? <span className="text-red-700">✗ 失败</span>
                    : <span className="text-amber-700">待发</span>}
                </div>

                {/* 手动模式 */}
                <div>
                  <div className="text-xs font-semibold text-slate-700 mb-2">
                    📋 {platform === 'toutiao' ? '手动发（推荐）' : '手动发（抖音图文）'}
                  </div>
                  <p className="text-[11px] text-slate-500 mb-2 leading-relaxed">
                    复制内容 → 跳到{PLATFORM_LABEL[platform]}发布页 → 在已登录的浏览器粘贴
                  </p>
                  <div className="space-y-1.5">
                    <Btn
                      variant="primary"
                      className="w-full justify-center"
                      onClick={async () => {
                        await copyTo('all')
                        const url = PUBLISH_URL[platform]
                        if (url) window.open(url, '_blank', 'noopener')
                      }}
                    >
                      📋 复制 + 打开发布页
                    </Btn>
                    <div className="flex gap-1.5">
                      <Btn variant="ghost" className="flex-1 !text-xs !py-1" onClick={() => copyTo('title')}>
                        {copied === 'title' ? '✓ 已复制' : '只复制标题'}
                      </Btn>
                      <Btn variant="ghost" className="flex-1 !text-xs !py-1" onClick={() => copyTo('body')}>
                        {copied === 'body' ? '✓ 已复制' : '只复制正文'}
                      </Btn>
                    </div>
                    {copied === 'all' && (
                      <div className="text-[11px] text-emerald-700">✓ 已复制（标题 + 空行 + 正文）</div>
                    )}
                  </div>
                </div>

                {/* 自动发只在头条号有（douyin 不做扫码+CDP） */}
                {platform === 'toutiao' && (
                <div className="border-t border-slate-100 pt-3">
                  <div className="text-xs font-semibold text-slate-700 mb-2">🚀 自动发（需绑定）</div>
                  {ttStatus?.status === 'logged_in' ? (
                    <>
                      <p className="text-[11px] text-slate-500 mb-2">
                        ✓ 已登录{ttStatus.name && `（${ttStatus.name}）`}，可直接调用浏览器发布
                      </p>
                      <div className="space-y-1.5">
                        <Btn
                          className="w-full justify-center"
                          onClick={() => setConfirming({ kind: 'toutiao-publish' })}
                          loading={push.isPending}
                        >
                          {push.isPending ? '发布中…（约 30s）' : '🚀 立即发布'}
                        </Btn>
                        <Btn
                          variant="secondary"
                          className="w-full justify-center !text-xs"
                          onClick={() => saveDraftOnly.mutate()}
                          loading={saveDraftOnly.isPending}
                        >
                          {saveDraftOnly.isPending ? '存草稿中…' : '📥 仅存草稿（不发布）'}
                        </Btn>
                      </div>
                    </>
                  ) : (
                    <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
                      未绑定头条号，<Link to={`/${userId}/toutiao`} className="underline">去扫码 →</Link>
                    </p>
                  )}
                </div>
                )}

                {((push.data?.ok === false && !push.data.need_binding) || saveDraftOnly.data?.ok === false) && (
                  <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
                    ✗ {push.data?.error || saveDraftOnly.data?.error}
                  </div>
                )}
                {push.data?.ok && (
                  <div className="text-xs text-emerald-700">✓ 已发布</div>
                )}
                {saveDraftOnly.data?.ok && (
                  <div className="text-xs text-emerald-700">✓ 已存草稿，去头条号后台手动审核发布</div>
                )}
              </div>
            ) : (
              /* ── 公众号 / 小红书：保持原行为 ───────────────────────── */
              <>
                {canPush ? (
                  <Btn
                    className="w-full justify-center"
                    onClick={() => push.mutate()}
                    loading={push.isPending}
                  >
                    {push.isPending
                      ? (platform === 'wechat' ? '推送中…' : '生成二维码中…')
                      : (platform === 'wechat' ? '📤 推送到公众号草稿箱' : '🌹 生成小红书发布二维码')}
                  </Btn>
                ) : (
                  <div className="text-sm text-emerald-700 bg-emerald-50 border border-emerald-200 rounded px-3 py-2">
                    ✓ 已推送
                  </div>
                )}

                {push.data?.ok === false && !push.data.need_binding && (
                  <div className="mt-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
                    ✗ {push.data.error}
                  </div>
                )}
                {push.data?.ok && platform === 'wechat' && (
                  <div className="mt-3 text-xs text-emerald-700">
                    ✓ 已推送到{' '}
                    <a href="https://mp.weixin.qq.com" target="_blank" className="underline">公众号后台</a>
                    <div className="text-slate-400 mt-1 font-mono break-all">media_id: {push.data.media_id}</div>
                  </div>
                )}
                {qrUrl && platform === 'xhs' && (
                  <div className="mt-4">
                    <div className="text-xs text-emerald-700 mb-2">✓ 用手机扫码发布</div>
                    <img src={qrUrl} alt="发布二维码" className="w-full border border-slate-200 rounded bg-white" />
                  </div>
                )}
              </>
            )}
          </div>
        </aside>
      </div>

      {/* Lightbox */}
      {lightbox && (
        <div
          className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center cursor-zoom-out p-8"
          onClick={() => setLightbox(null)}
        >
          <img src={lightbox} className="max-w-full max-h-full object-contain" />
        </div>
      )}

      {/* 公众号 AppID / AppSecret 绑定弹窗（首次推送时触发） */}
      {bindOpen && platform === 'wechat' && (
        <WechatBindModal
          userId={userId}
          onClose={() => setBindOpen(false)}
          onBound={() => {
            // 关 modal + 让用户/草稿缓存刷新；自动再点一次推送
            setBindOpen(false)
            qc.invalidateQueries({ queryKey: ['users'] })
            qc.invalidateQueries({ queryKey: ['user', userId] })
            push.reset()
            push.mutate()
          }}
        />
      )}

      {/* 通用确认 modal（替代 native confirm()） */}
      <ConfirmDialog
        open={confirming?.kind === 'del-image'}
        title="删除这张图？"
        message={
          <>
            将删除第 <b>{confirming?.kind === 'del-image' ? confirming.index + 1 : '?'}</b> 张图。
            <br />
            如果是你自己上传的图，磁盘文件也会一起删；删后无法恢复。
          </>
        }
        tone="danger"
        confirmText="删除"
        loading={delImage.isPending}
        onConfirm={() => {
          if (confirming?.kind === 'del-image') {
            delImage.mutate(confirming.index, { onSettled: () => setConfirming(null) })
          }
        }}
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        open={confirming?.kind === 'toutiao-publish'}
        title="确认发布到头条号？"
        message="点确认后会立即调用浏览器自动发布。发布后无法撤回。"
        tone="danger"
        confirmText="立即发布"
        loading={push.isPending}
        onConfirm={() => { push.mutate(); setConfirming(null) }}
        onCancel={() => setConfirming(null)}
      />
    </>
  )
}
