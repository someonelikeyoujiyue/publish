import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import { App } from './App'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 控制机 ↔ 旧机 MySQL ping RTT 244ms，每次 SQL ~500ms 不可避免。
      // 用更长的 staleTime + 切 tab 时显示旧数据，让 UI 感知瞬间响应
      staleTime: 60_000,            // 60s 内不重新拉
      gcTime:    5 * 60_000,        // 5min 内保留缓存
      retry:     1,
      refetchOnWindowFocus: false,
      refetchOnMount:       false,  // 进入页面如果缓存还在 fresh 期就不重拉
      placeholderData: (prev: unknown) => prev,  // 切 key 时先显示旧数据再悄悄刷
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
