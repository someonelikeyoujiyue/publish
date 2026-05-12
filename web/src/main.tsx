import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import { App } from './App'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 平衡：先显示旧数据避免空白闪烁，但同时后台 refetch 拉最新。
      // 这样 cron / 外部触发产生的新草稿，用户切 tab 就能自动看到。
      staleTime: 15_000,            // 15s 内不重新拉
      gcTime:    5 * 60_000,        // 5min 内保留缓存
      retry:     1,
      refetchOnWindowFocus: true,   // 切回浏览器 tab 自动刷新
      refetchOnMount:       'always', // 每次进入页面后台刷新
      placeholderData: (prev: unknown) => prev,  // 但先显示旧数据，无空白
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
