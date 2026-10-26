import axios from 'axios'
import {loadSession, clearSession} from './session-client'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})


// Session uses fetch so request protection has no circular API dependency.
api.interceptors.request.use(async request => {
  if (['post', 'put', 'patch', 'delete'].includes((request.method || '').toLowerCase())) {
    const session = await loadSession()
    if (session.role !== 'operator') throw new Error('当前账号为只读，不能修改采集电脑上的数据')
    if (session.csrf_token) request.headers.set('X-CSRF-Token', session.csrf_token)
  }
  return request
})
api.interceptors.response.use(response => response, async error => {
  const config = error.config as (typeof error.config & {csrfRetried?: boolean})
  if (error.response?.status === 401) window.dispatchEvent(new Event('mc-session-expired'))
  if (error.response?.status === 403 && error.response?.data?.code === 'csrf_expired' && config && !config.csrfRetried) {
    clearSession(); await loadSession(true); config.csrfRetried = true
    return api.request(config)
  }
  return Promise.reject(error)
})

async function startTask(config: CrawlerConfig) {
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(config))))).map(x => x.toString(16).padStart(2, '0')).join('')
  const storageKey = 'mediacrawler_pending_request'
  let previous: {digest: string; key: string; at: number} | null = null
  try {previous = JSON.parse(sessionStorage.getItem(storageKey) || 'null')} catch { /* malformed local state is discarded */ }
  const pending = previous?.digest === digest && Date.now() - previous.at < 86400000 ? previous : {digest, key: crypto.randomUUID(), at: Date.now()}
  sessionStorage.setItem(storageKey, JSON.stringify(pending))
  const response = await api.post('/crawler/start', config, {headers: {'Idempotency-Key': pending.key}})
  sessionStorage.removeItem(storageKey)
  return response
}

// Types
export interface CrawlerConfig {
  collection_method?: 'browser' | 'redfox_mcp'
  mcp_tool?: string
  mcp_arguments?: Record<string, string | number>
  mcp_parent_content_id?: string
  platform: string
  login_type: string
  crawler_type: string
  keywords: string
  start_page: number
  enable_comments: boolean
  enable_sub_comments: boolean
  enable_media: boolean
  save_option: string
  cookies: string
  headless: boolean
}

export interface CrawlerStatus {
  status: 'idle' | 'running' | 'stopping' | 'error'
  platform: string | null
  crawler_type: string | null
  started_at: string | null
  task?: import('./workbench').Task | null
  error_message: string | null
}

export interface LogEntry {
  task_id?: string
  id: number
  timestamp: string
  level: 'info' | 'warning' | 'error' | 'success' | 'debug'
  message: string
}

export interface DataFile {
  display_name?: string | null
  name: string
  path: string
  size: number
  modified_at: number
  record_count: number | null
  type: string
}

export interface FilePreviewResponse {
  data: Record<string, unknown>[]
  total: number
  columns?: string[]
}

export interface Platform {
  value: string
  label: string
  icon: string
}

export interface ConfigOption {
  value: string
  label: string
}

// API functions
export const crawlerApi = {
  start: startTask,
  stop: (task_id: string) => api.post('/crawler/stop', {task_id}),
  getStatus: () => api.get<CrawlerStatus>('/crawler/status'),
  getLogs: (limit = 100) => api.get<{ logs: LogEntry[] }>('/crawler/logs', { params: { limit } }),
}

export const dataApi = {
  getFiles: (platform?: string, fileType?: string) =>
    api.get<{ files: DataFile[] }>('/data/files', { params: { platform, file_type: fileType } }),
  getFileContent: (path: string, limit = 100) =>
    api.get<FilePreviewResponse>('/data/files/' + path, { params: { preview: true, limit } }),
  getStats: () => api.get('/data/stats'),
  getDownloadUrl: (path: string) => `/api/data/download/${path}`,
}

export const configApi = {
  getPlatforms: () => api.get<{ platforms: Platform[] }>('/config/platforms'),
  getOptions: () =>
    api.get<{
      login_types: ConfigOption[]
      crawler_types: ConfigOption[]
      save_options: ConfigOption[]
    }>('/config/options'),
}

export interface EnvCheckResult {
  success: boolean
  message: string
  output?: string
  error?: string
}

export const envApi = {
  check: () => api.get<EnvCheckResult>('/env/check'),
}

export default api
