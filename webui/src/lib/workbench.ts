import api from './api'

export interface Asset {
  id: string; name: string; kind: 'image' | 'video'; size: number; updated_at: number
  source: string; url: string
}
export interface Draft {
  id: string; title: string; content: string; tags: string[]; images: string[]
  platform: string; version: number; updated_at?: string
}
export interface Task {
  id: string; platform: string; crawler_type: string; started_at: string
  finished_at: string | null; outcome: 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  exit_code: number | null; target: string
}
export const platformNames: Record<string, string> = {
  xhs: '小红书', dy: '抖音', ks: '快手', bili: '哔哩哔哩', wb: '微博', tieba: '百度贴吧', zhihu: '知乎',
}
export const modeNames: Record<string, string> = { search: '关键词搜索', detail: '指定内容采集', creator: '创作者采集' }
export const outcomeNames: Record<string, string> = {
  running: '执行中', completed: '已完成', failed: '执行失败', cancelled: '已停止', interrupted: '服务中断',
}
export const workbenchApi = {
  assets: async (archived = false) => (await api.get<{assets: Asset[]}>('/workbench/assets', {params: {archived}})).data.assets,
  archive: async (id: string, archived: boolean) => api.patch('/workbench/assets/state/' + id.split('/').map(encodeURIComponent).join('/'), {archived}),
  upload: async (name: string, data: string) => (await api.post<Asset>('/workbench/assets', {name, data})).data,
  drafts: async () => (await api.get<{drafts: Draft[]}>('/workbench/drafts')).data.drafts,
  save: async (draft: Draft) => (await api.put<Draft>(`/workbench/drafts/${draft.id}`, draft)).data,
  tasks: async () => (await api.get<{tasks: Task[]}>('/workbench/tasks')).data.tasks,
}
export const assetUrl = (id: string) => '/api/workbench/assets/file/' + id.split('/').map(encodeURIComponent).join('/')
export const formatSize = (n: number) => n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`
export function errorMessage(error: unknown) {
  const e = error as {response?: {data?: {detail?: unknown}}; message?: string}
  const detail = e?.response?.data?.detail
  return typeof detail === 'string' ? detail : e?.message || '操作失败，请重试'
}
