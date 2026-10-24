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
  const steps = [
    {title: '提交采集参数', detail: task ? `${platformNames[task.platform]} · ${modeNames[task.crawler_type]}` : '等待创建任务', done: !!task},
    {title: '浏览器执行', detail: running ? '登录、采集与保存的实际情况请查看日志' : task ? '进程已结束' : '沿用现有浏览器执行方式', done: finished},
    {title: '任务结果', detail: task ? outcomeNames[task.outcome] : '完成后可前往数据与素材查看', done: finished},
  ]
  return <section className="wb-card wb-task-panel">
    <div className="wb-card-heading"><Activity size={19}/><h2>任务进度</h2><span className={`wb-badge ${running ? 'purple' : ''}`}>{isError ? '服务离线' : data?.status === 'stopping' ? '正在停止' : running ? '执行中' : task ? outcomeNames[task.outcome] : '待执行'}</span></div>
    <div className="wb-task-body">
      {task && <p className="wb-elapsed"><Clock3 size={14}/> 已运行 {Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒</p>}
      <ol className="wb-steps">{steps.map((step, i) => <li key={step.title} className={step.done ? 'done' : running && i === 1 ? 'active' : ''}><span className="wb-step-dot">{step.done ? <CheckCircle2 size={17}/> : i + 1}</span><div><strong>{step.title}</strong><p>{step.detail}</p></div></li>)}</ol>
      {data?.error_message && <p className="wb-error" role="alert"><AlertCircle size={15}/>{data.error_message}</p>}
      <p className="wb-hint">进度依据真实进程状态展示；平台未提供总量时不估算百分比。</p>
      <div className="wb-log-heading"><strong>执行日志</strong><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</button></div>
      <div className={`wb-live-logs ${expanded ? 'expanded' : ''}`} aria-label="执行日志">{logs.length ? logs.slice(expanded ? -500 : -8).map(log => <div key={log.id} className={`log-${log.level}`}><time>{log.timestamp}</time><span>{log.message}</span></div>) : <p>执行后将在此显示浏览器与采集日志。</p>}</div>
    </div>
  </section>
}

export function TaskHistory() {
  const {data = [], isLoading, isError, refetch} = useQuery({queryKey: ['workbenchTasks'], queryFn: workbenchApi.tasks, refetchInterval: 3000})
  return <section className="wb-card"><div className="wb-card-heading"><h2>任务记录</h2><button className="wb-button" onClick={() => refetch()}>刷新</button></div><div className="wb-table-wrap">
    {isError ? <p role="alert" className="wb-empty">任务记录加载失败，请刷新重试。</p> : !data.length ? <div className="wb-empty"><Clock3/><h3>{isLoading ? '正在读取…' : '还没有任务记录'}</h3><p>每次采集的目标、起止时间和结果会保存在本机。</p></div> : <table className="wb-table"><thead><tr><th>任务</th><th>采集目标</th><th>开始时间</th><th>结果</th><th>退出码</th></tr></thead><tbody>{data.map(task => <tr key={task.id}><td>{platformNames[task.platform]}<small>{modeNames[task.crawler_type]}</small></td><td className="wb-target-cell" title={task.target}>{task.target || '—'}</td><td>{new Date(task.started_at).toLocaleString('zh-CN')}</td><td><span className={`wb-badge ${task.outcome === 'failed' ? 'red' : task.outcome === 'running' ? 'purple' : ''}`}>{outcomeNames[task.outcome]}</span></td><td>{task.exit_code ?? '—'}</td></tr>)}</tbody></table>}
  </div></section>
}
