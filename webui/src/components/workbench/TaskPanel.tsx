import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, Clock3, CheckCircle2, AlertCircle } from 'lucide-react'
import { useCrawlerStatus } from '@/hooks/useCrawler'
import { useCrawlerStore } from '@/store/crawlerStore'
import { workbenchApi, platformNames, modeNames, outcomeNames } from '@/lib/workbench'

export function TaskPanel() {
  const {data, isError} = useCrawlerStatus()
  const logs = useCrawlerStore(s => s.logs)
  const [expanded, setExpanded] = useState(false)
  const task = data?.task
  const running = data?.status === 'running' || data?.status === 'stopping'
  const finished = !!task && !running && task.outcome !== 'running'
  const elapsed = task ? Math.max(0, Math.floor(((task.finished_at ? new Date(task.finished_at).getTime() : Date.now()) - new Date(task.started_at).getTime()) / 1000)) : 0
  let steps = [
    {title: '提交采集参数', detail: task ? `${platformNames[task.platform]} · ${modeNames[task.crawler_type]}` : '等待创建任务', done: !!task},
    {title: '浏览器执行', detail: running ? '登录、采集与保存的实际情况请查看日志' : task ? (task.outcome === 'interrupted' ? '服务中断，请在采集电脑确认是否存在遗留进程' : '进程已结束') : '沿用现有浏览器执行方式', done: finished},
    {title: '任务结果', detail: task ? outcomeNames[task.outcome] : '完成后可前往数据与素材查看', done: finished},
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
      {task?.xhs_filters && <div className="wb-filter-progress"><strong>{progress ? labels[progress.phase] : '准备网页筛选'}</strong><p>当前关键词：{progress?.keyword || '等待浏览器开始搜索'}</p>{progress && <><p>发现 {progress.discovered} · 已保存 {progress.collected} · 详情失败 {progress.failed} · 滚动 {progress.scrolls} 次</p><p>请求：{Object.entries(progress.requested_filters).map(([key,value]) => `${key}：${value}`).join(' · ')}</p><p>已确认：{Object.keys(progress.verified_filters).length ? Object.entries(progress.verified_filters).map(([key,value]) => `${key}：${value}`).join(' · ') : '尚未完成全部筛选校验'}</p>{progress.stop_reason && <p>{reasons[progress.stop_reason] || progress.stop_reason}</p>}</>}</div>}
      <ol className="wb-steps">{steps.map((step, i) => <li key={step.title} className={step.done ? 'done' : running && (task?.xhs_filters ? step.title === labels[progress?.phase || ''] : i === 1) ? 'active' : ''}><span className="wb-step-dot">{step.done ? <CheckCircle2 size={17}/> : i + 1}</span><div><strong>{step.title}</strong><p>{step.detail}</p></div></li>)}</ol>
      {data?.error_message && <p className="wb-error" role="alert"><AlertCircle size={15}/>{data.error_message}</p>}
      <p className="wb-hint">进度依据真实进程状态展示；日志每 5 秒补取，网络断开不会主动停止电脑上的任务。</p>
      <div className="wb-log-heading"><strong>执行日志</strong><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</button></div>
      <div className={`wb-live-logs ${expanded ? 'expanded' : ''}`} aria-label="执行日志">{logs.length ? logs.slice(expanded ? -500 : -8).map(log => <div key={log.id} className={`log-${log.level}`}><time title={log.timestamp}>{log.timestamp.includes('T') ? new Date(log.timestamp).toLocaleTimeString('zh-CN', {hour12: false}) : log.timestamp}</time><span>{log.message}</span></div>) : <p>执行后将在此显示浏览器与采集日志。</p>}</div>
    </div>
  </section>
}

export function TaskHistory() {
  const {data = [], isLoading, isError, refetch} = useQuery({queryKey: ['workbenchTasks'], queryFn: workbenchApi.tasks, refetchInterval: 3000})
  return <section className="wb-card"><div className="wb-card-heading"><h2>任务记录</h2><button className="wb-button" onClick={() => refetch()}>刷新</button></div><div className="wb-table-wrap">
    {isError ? <p role="alert" className="wb-empty">任务记录加载失败，请刷新重试。</p> : !data.length ? <div className="wb-empty"><Clock3/><h3>{isLoading ? '正在读取…' : '还没有任务记录'}</h3><p>每次采集的目标、起止时间和结果会保存在本机。</p></div> : <table className="wb-table"><thead><tr><th>任务</th><th>采集目标</th><th>开始时间</th><th>结果</th><th>退出码</th></tr></thead><tbody>{data.map(task => <tr key={task.id}><td>{platformNames[task.platform]}<small>{modeNames[task.crawler_type]}</small></td><td className="wb-target-cell" title={task.target}>{task.target || '—'}{task.xhs_filters && <small>网页高级筛选 · 已保存 {task.progress?.collected ?? 0} 条</small>}</td><td>{new Date(task.started_at).toLocaleString('zh-CN')}</td><td><span className={`wb-badge ${task.outcome === 'failed' ? 'red' : task.outcome === 'running' ? 'purple' : ''}`}>{outcomeNames[task.outcome]}</span></td><td>{task.exit_code ?? '—'}</td></tr>)}</tbody></table>}
  </div></section>
}
