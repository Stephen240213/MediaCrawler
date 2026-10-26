export interface XhsSearchFilters {
  sort_by: string
  note_type: string
  publish_time: string
  search_scope: string
  location: string
  refresh_existing?: boolean
  max_scrolls: number
  timeout_seconds: number
}

export interface CrawlerConfig {
  collection_method?: 'browser' | 'redfox_mcp'
  mcp_tool?: string
  mcp_arguments?: Record<string, string | number>
  mcp_parent_content_id?: string
  platform: string
  login_type: string
  crawler_type: string
  keywords: string
  specified_ids: string  // 详情模式下的帖子/视频ID
  creator_ids: string    // 创作者模式下的创作者ID
  start_page: number
  enable_comments: boolean
  enable_sub_comments: boolean
  enable_media: boolean  // 是否下载媒体（封面/视频/图文图片），支持 xhs/dy/ks/bili/wb
  save_option: string
  cookies: string
  xhs_filters?: XhsSearchFilters
  max_notes_count?: number
  max_comments_count?: number
  headless: boolean
}

export interface CrawlerStatus {
  status: 'idle' | 'running' | 'stopping' | 'error'
  platform: string | null
  crawler_type: string | null
  started_at: string | null
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

export interface Platform {
  value: string
  label: string
  icon: string
}

export interface ConfigOption {
  value: string
  label: string
}
