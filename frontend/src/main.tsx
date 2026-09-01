import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.tsx'
import './index.css'

/**
 * UI-010 and TD-08: polling, never WebSockets. MCP exposes no streaming, and
 * nobody notices a 15s refresh while everybody notices a dead socket on demo
 * day. UI-001 sets the interval at 10s.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 10_000,
      refetchOnWindowFocus: true,
      staleTime: 5_000,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
