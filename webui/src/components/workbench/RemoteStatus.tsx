import { useQuery } from '@tanstack/react-query'
import api from '@/lib/api'
import { useCrawlerStore } from '@/store/crawlerStore'
import { useSession } from '@/lib/session'

export function RemoteStatus() {
  const session = useSession()
  const platform = useCrawlerStore(s => s.config.platform)
  const login = useQuery({queryKey: ['loginState', platform], queryFn: async () => (await api.get<{state: string; message: string; observed_at: string | null}>('/crawler/login-state', {params: {platform}})).data, refetchInterval: 15000})
  return <section className="wb-remote-status" aria-label="访问与执行位置">
    <div><strong>{session.mode === 'remote' ? '远程访问 · 采集电脑执行' : '本机工作台'}</strong><span>{session.email} · {session.role === 'viewer' ? '只读账号' : '操作员'}</span></div>
    {session.mode === 'remote' && <a href="/cdn-cgi/access/logout">退出远程访问</a>}
    <p>{session.mode === 'remote' ? '采集、浏览器登录和数据存储均在指定电脑完成。关闭此网页不会停止已提交的任务。' : '任务由本机后端执行，关闭网页不会主动停止任务。'}</p>
    {session.role === 'viewer' && <p>可查看数据、日志、草稿和素材，以及下载导出；启动、停止、导入和保存操作已禁用。</p>}
    {login.data?.observed_at && <small>上次检查：{new Date(login.data.observed_at).toLocaleString('zh-CN')}</small>}
    <p>{login.isError ? '暂时无法读取平台登录提示，请重试连接；不要据此判断账号已登录。' : login.data?.message || '正在读取本机浏览器登录提示…'}</p>
  </section>
}
