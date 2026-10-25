import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowUpRight, Database, FilePenLine, FolderOpen, LayoutDashboard, Monitor, Sparkles, Images, ShieldCheck } from 'lucide-react'
import { useCrawlerStore } from '@/store/crawlerStore'
import { useCrawlerLogs } from '@/hooks/useCrawler'
import { useLogWebSocket } from '@/hooks/useWebSocket'
import api from '@/lib/api'
import { type Asset } from '@/lib/workbench'
import { Collector, collectionTools } from './Collector'
import { TaskPanel, TaskHistory } from './TaskPanel'
import { AssetLibrary } from './AssetLibrary'
import { DraftStudio } from './DraftStudio'
import { DataExplorer } from '@/components/data/DataExplorer'
import './workbench.css'
import { RemoteStatus } from './RemoteStatus'
import { Analytics } from './Analytics'

type Page = 'collect' | 'drafts' | 'assets' | 'data' | 'tasks' | 'analytics'
const pages = [
  {id: 'collect' as const, label: '采集工作台', icon: LayoutDashboard},
  {id: 'drafts' as const, label: '草稿预览', icon: FilePenLine},
  {id: 'assets' as const, label: '素材管理', icon: Images},
  {id: 'data' as const, label: '数据文件', icon: Database},
  {id: 'analytics' as const, label: '数据分析', icon: Activity},
  {id: 'tasks' as const, label: '任务记录', icon: Activity},
]
const localTools = [
  {page: 'drafts' as const, title: '草稿编辑', description: '编辑文案与预览配图', icon: FilePenLine},
  {page: 'assets' as const, title: '素材整理', description: '导入、预览和选择素材', icon: FolderOpen},
]
export function Workbench({onLicense}: {onLicense: () => void}) {
  const [page, setPage] = useState<Page>('collect')
  const [assetRequest, setAssetRequest] = useState<{id: string; sequence: number} | null>(null)
  const update = useCrawlerStore(s => s.updateConfig)
  const mode = useCrawlerStore(s => s.config.crawler_type)
  const status = useCrawlerStore(s => s.status)
  const {isSuccess: online, isError} = useQuery({queryKey: ['workbenchHealth'], queryFn: () => api.get('/health'), refetchInterval: 10000})
  useLogWebSocket()
  useCrawlerLogs()
  const useAsset = (asset: Asset) => {setAssetRequest({id: asset.id, sequence: Date.now()}); setPage('drafts')}
  const active = pages.find(p => p.id === page)!
  return <div className="workbench">
    <aside className="wb-sidebar"><a className="wb-brand" href="#" onClick={e => {e.preventDefault();setPage('collect')}}><span className="wb-brand-mark">M</span><span>MediaCrawler<small>本机内容工作台</small></span></a>
      <div className="wb-nav-label">工作空间</div><nav aria-label="工作台导航">{pages.map(item => <button key={item.id} aria-current={page === item.id ? 'page' : undefined} className={page === item.id ? 'active' : ''} onClick={() => setPage(item.id)}><item.icon size={18}/>{item.label}{page === item.id && <span className="wb-nav-dot"/>}</button>)}</nav>
      <div className="wb-sidebar-bottom"><div><ShieldCheck size={17}/><span>本机存储<small>数据与草稿保存在电脑上</small></span></div><button onClick={onLicense}>查看项目使用条款 <ArrowUpRight size={13}/></button></div>
    </aside>
    <div className="wb-main"><header className="wb-topbar"><span>工作空间 <span className="wb-slash">/</span> <strong>{active.label}</strong></span><span className={`wb-service ${online ? '' : 'offline'}`}><i/>{isError ? '本机服务离线' : online ? '本机服务在线' : '正在连接服务'}<span>127.0.0.1</span></span></header>
      <main className="wb-content"><div className="wb-page-heading"><div><p className="wb-eyebrow">YOUR LOCAL WORKSPACE</p><h1>{active.label}</h1><p>{page === 'analytics' ? '基于本机 SQLite 采集数据，查看趋势、互动与评论。' : page === 'collect' ? '从内容发现到灵感整理，让每一步清晰可见。' : page === 'drafts' ? '把灵感整理为草稿，一边编辑，一边预览。' : page === 'assets' ? '管理采集媒体与本机图片，随时用于草稿。' : page === 'tasks' ? '查看真实执行结果，回溯每次采集。' : '预览与导出已保存的采集数据。'}</p></div><span className="wb-local-label"><Monitor size={16}/>本机工作台</span></div>
      <RemoteStatus/>
      {page === 'collect' && <>
        <section className="wb-hero"><span className="wb-hero-icon"><Sparkles size={27}/></span><div><h2>发现内容，沉淀灵感</h2><p>搜索内容、采集评论，将图片与想法整理成自己的草稿。</p><span>内容采集 <i/> 本机素材 <i/> 草稿预览</span></div><button className="wb-button primary" onClick={() => setPage('drafts')}><FilePenLine size={16}/>打开草稿 <ArrowUpRight size={15}/></button></section>
        <div className="wb-workspace-tabs"><span className="selected">采集工具</span><button onClick={() => setPage('data')}>数据与结果 <ArrowUpRight size={13}/></button><span className="wb-tabs-status">{status === 'running' ? '任务执行中' : status === 'stopping' ? '任务停止中' : '选择工具开始采集'}</span></div>
        <div className="wb-collection-layout"><aside className="wb-card wb-tool-directory"><div className="wb-card-heading"><h2>工具目录</h2><span className="wb-badge">{collectionTools.length + localTools.length}</span></div><p className="wb-category-label">内容采集 <span>{collectionTools.length}</span></p>{collectionTools.map(tool => <button key={tool.id} className={`wb-tool-item ${mode === tool.id ? 'selected' : ''}`} disabled={status === 'running' || status === 'stopping'} onClick={() => update({crawler_type: tool.id})}><tool.icon size={18}/><span><strong>{tool.title}</strong><small>{tool.description}</small></span></button>)}<p className="wb-category-label">本地创作 <span>{localTools.length}</span></p>{localTools.map(tool => <button className="wb-tool-item" key={tool.page} onClick={() => setPage(tool.page)}><tool.icon size={18}/><span><strong>{tool.title}</strong><small>{tool.description}</small></span></button>)}<div className="wb-directory-note"><Monitor size={16}/><p>使用现有浏览器执行<br/>首次采集请在浏览器中登录</p></div></aside><Collector/><TaskPanel/></div>
      </>}
      <div hidden={page !== 'drafts'}><DraftStudio assetRequest={assetRequest}/></div>
      {page === 'assets' && <AssetLibrary onUse={useAsset}/>}
      {page === 'data' && <section className="wb-card wb-data-panel"><DataExplorer/></section>}
      {page === 'analytics' && <Analytics/>}
      {page === 'tasks' && <><TaskHistory/><div className="wb-history-current"><TaskPanel/></div></>}
      <footer className="wb-footer">MediaCrawler · 本机内容工作台<span>内容采集 · 素材整理 · 草稿预览</span></footer>
      </main>
    </div>
  </div>
}
