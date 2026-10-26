import { useSession, canOperate } from '@/lib/session'
import api from '@/lib/api'
import { errorMessage,type Task } from '@/lib/workbench'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Clock3, CheckCircle2, AlertCircle } from 'lucide-react'
import { useCrawlerStatus } from '@/hooks/useCrawler'
import { useCrawlerStore } from '@/store/crawlerStore'
import { workbenchApi, platformNames, modeNames, outcomeNames } from '@/lib/workbench'

const taskPlatforms: Record<string, string> = {...platformNames, twitter:'X / Twitter', youtube:'YouTube'}
const taskMode = (task: Task) => task.collection_method === 'redfox_mcp' ? `RedFox MCP · ${task.mcp_tool || ''}` : modeNames[task.crawler_type]

export function TaskPanel() {
  const {data, isError} = useCrawlerStatus()
  const logs = useCrawlerStore(s => s.logs)
  const [expanded, setExpanded] = useState(false)
  const task = data?.task
  const running = data?.status === 'running' || data?.status === 'stopping'
  const finished = !!task && !running && task.outcome !== 'running'
  const elapsed = task ? Math.max(0, Math.floor(((task.finished_at ? new Date(task.finished_at).getTime() : Date.now()) - new Date(task.started_at).getTime()) / 1000)) : 0
  let steps = [
    {title: '提交采集参数', detail: task ? `${taskPlatforms[task.platform]} · ${taskMode(task)}` : '等待创建任务', done: !!task},
    {title: task?.collection_method === 'redfox_mcp' ? 'MCP 服务执行' : '浏览器执行', detail: running ? '采集与保存的实际情况请查看日志' : task ? (task.outcome === 'interrupted' ? '服务中断，请在采集电脑确认是否存在遗留进程' : '进程已结束') : '沿用现有浏览器执行方式', done: finished},
    {title: '任务结果', detail: task ? (task.collection_method === 'redfox_mcp' && task.outcome === 'completed' ? '本次工具调用与 JSON 保存完成；远程异步任务请另行查询' : outcomeNames[task.outcome]) : '完成后可前往数据与素材查看', done: finished},
  ]
  const progress = task?.progress
  const labels: Record<string,string> = {opening: '打开搜索页', searching: '输入关键词', filtering: '应用筛选', verifying: '校验筛选', collecting: '收集搜索结果', details: '采集详情与评论', saving: '保存结果', finished: '完成', failed: '执行中断'}
  const reasons: Record<string,string> = {content_limit: '达到内容数量上限', platform_end: '平台返回结果结束', page_end: '网页显示结果结束', scroll_limit: '达到滚动次数上限，结果可能不完整', time_limit: '达到时间上限', cancelled: '用户停止任务'}
  if (task?.xhs_filters) {
    const phases = ['opening', 'searching', 'filtering', 'verifying', 'collecting', 'details', 'saving']
    const current = phases.indexOf(progress?.phase || '')
    steps = phases.map((phase,index) => ({title: labels[phase], detail: phase === progress?.phase ? (progress.stop_reason || labels[phase]) : '', done: task.outcome === 'completed' || (current >= 0 && index < current)}))
  }
  return <section className="wb-card wb-task-panel">
    <div className="wb-card-heading"><Activity size={19}/><h2>任务进度</h2><span className={`wb-badge ${running ? 'purple' : ''}`}>{isError ? '服务离线' : data?.status === 'stopping' ? '正在停止' : running ? '执行中' : task ? outcomeNames[task.outcome] : '待执行'}</span></div>
    <div className="wb-task-body">
      {task && <p className="wb-elapsed"><Clock3 size={14}/> 已运行 {Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒</p>}
      {task?.xhs_filters && <div className="wb-filter-progress"><strong>{progress ? labels[progress.phase] : '准备网页筛选'}</strong><p>当前关键词：{progress?.keyword || '等待浏览器开始搜索'}</p>{progress && <><p>发现 {progress.discovered} · 已处理 {progress.collected} · 新增 {progress.new??progress.collected} · 复用 {progress.reused??0} · 刷新 {progress.refreshed??0} · 详情失败 {progress.failed} · 滚动 {progress.scrolls} 次</p><p>请求：{Object.entries(progress.requested_filters).map(([key,value]) => `${key}：${value}`).join(' · ')}</p><p>已确认：{Object.keys(progress.verified_filters).length ? Object.entries(progress.verified_filters).map(([key,value]) => `${key}：${value}`).join(' · ') : '尚未完成全部筛选校验'}</p>{task.effective_options&&<p>生效参数：每词最多 {task.effective_options.max_notes_count} 条 · 起始批次 {task.effective_options.start_page} · 滚动上限 {task.xhs_filters.max_scrolls} 次 · 已跳过 {progress.skipped_batches??0} 批</p>}{progress.stop_reason && <p>{reasons[progress.stop_reason] || progress.stop_reason}</p>}</>}</div>}
      {task?.ingestion&&<div className="wb-filter-progress"><strong>MCP 同步结果</strong><p>内容 {task.ingestion.contents} 条 · 评论 {task.ingestion.comments} 条 · 拒绝 {task.ingestion.rejected||0} 条</p>{task.ingestion.warnings?.map((warning,i)=><p key={i}>{warning}</p>)}</div>}
      <ol className="wb-steps">{steps.map((step, i) => <li key={step.title} className={step.done ? 'done' : running && (task?.xhs_filters ? step.title === labels[progress?.phase || ''] : i === 1) ? 'active' : ''}><span className="wb-step-dot">{step.done ? <CheckCircle2 size={17}/> : i + 1}</span><div><strong>{step.title}</strong><p>{step.detail}</p></div></li>)}</ol>
      {data?.error_message && <p className="wb-error" role="alert"><AlertCircle size={15}/>{data.error_message}</p>}
      <p className="wb-hint">进度依据真实进程状态展示；日志每 5 秒补取，网络断开不会主动停止电脑上的任务。</p>
      <div className="wb-log-heading"><strong>执行日志</strong><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</button></div>
      <div className={`wb-live-logs ${expanded ? 'expanded' : ''}`} aria-label="执行日志">{logs.length ? logs.slice(expanded ? -500 : -8).map(log => <div key={log.id} className={`log-${log.level}`}><time title={log.timestamp}>{log.timestamp.includes('T') ? new Date(log.timestamp).toLocaleTimeString('zh-CN', {hour12: false}) : log.timestamp}</time><span>{log.message}</span></div>) : <p>执行后将在此显示采集日志。</p>}</div>
    </div>
  </section>
}

export function TaskHistory({onDataset,onResearch,onFiles}:{onFiles?:()=>void;onDataset?:(id:string)=>void;onResearch?:(task:Task)=>void}) {
  const writable = canOperate(useSession())
  const [deleting, setDeleting] = useState('')
  const [error, setError] = useState('')
  const remove = async (id: string) => {
    if (!window.confirm('删除这条任务记录及其日志？已采集的数据、草稿和素材会保留。')) return
    setDeleting(id); setError('')
    try {await api.delete('/workbench/tasks/' + encodeURIComponent(id)); await refetch()} catch(e) {setError(errorMessage(e))} finally {setDeleting('')}
  }
  const {data = [], isLoading, isError, refetch} = useQuery({queryKey: ['workbenchTasks'], queryFn: workbenchApi.tasks, refetchInterval: 3000})
  return <section className="wb-card"><div className="wb-card-heading"><h2>任务记录</h2><button className="wb-button" onClick={() => refetch()}>刷新</button></div><div className="wb-table-wrap">
    {error && <p role="alert" className="wb-error">{error}</p>}{isError ? <p role="alert" className="wb-empty">任务记录加载失败，请刷新重试。</p> : !data.length ? <div className="wb-empty"><Clock3/><h3>{isLoading ? '正在读取…' : '还没有任务记录'}</h3><p>每次采集的目标、起止时间和结果会保存在本机。</p></div> : <table className="wb-table"><thead><tr><th>任务</th><th>采集目标</th><th>开始时间</th><th>结果</th><th>退出码</th><th>操作</th></tr></thead><tbody>{data.map(task => <tr key={task.id}><td>{taskPlatforms[task.platform]}<small>{taskMode(task)}</small></td><td className="wb-target-cell" title={task.target}>{task.target || '—'}<small>批次 {task.id.slice(0,8)}{task.ingestion&&<small>MCP 同步：内容 {task.ingestion.contents} · 评论 {task.ingestion.comments} · {task.ingestion.status==='error'?'失败':task.ingestion.status==='raw_only'?'仅原始结果':task.ingestion.status==='unrecognized'?'待核对格式':'已处理'}</small>}{task.effective_options&&task.collection_method!=='redfox_mcp'?` · 上限 ${task.effective_options.max_notes_count} · 起始 ${task.effective_options.start_page}`:''}</small>{task.xhs_filters && <small>{task.dataset_counts?`本次 ${task.dataset_counts.total} 条 · 新增 ${task.dataset_counts.new||0} · 复用 ${task.dataset_counts.reused||0} · 刷新 ${task.dataset_counts.refreshed||0}`:`历史保存计数 ${task.progress?.collected??0} 条 · 成员范围需核对`}</small>}</td><td>{new Date(task.started_at).toLocaleString('zh-CN')}</td><td><span className={`wb-badge ${task.outcome === 'failed' ? 'red' : task.outcome === 'running' ? 'purple' : ''}`}>{outcomeNames[task.outcome]}</span></td><td>{task.exit_code ?? '—'}</td><td><div className="wb-task-actions">{onFiles&&task.collection_method==='redfox_mcp'&&<button className="wb-button" onClick={onFiles}>查看结果文件</button>}{onDataset&&(task.collection_method!=='redfox_mcp'||task.membership_version===1)&&<button className="wb-button" onClick={()=>onDataset(task.id)}>查看资料</button>}{onResearch&&task.platform==='xhs'&&(task.collection_method!=='redfox_mcp'||task.membership_version===1)&&<button className="wb-button" disabled={!writable||task.outcome==='running'} onClick={()=>onResearch(task)}>内容分析</button>}<button className="wb-button" disabled={!writable || !!deleting || task.outcome === 'running'} onClick={() => void remove(task.id)}>{deleting === task.id ? '删除中…' : '删除'}</button></div></td></tr>)}</tbody></table>}
  </div></section>
}
