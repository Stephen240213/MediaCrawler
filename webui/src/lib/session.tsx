import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

import { loadSession, clearSession, type Session } from './session-client'
export type { Session } from './session-client'
const Context = createContext<Session>({mode: 'local', sub: 'local', email: '本机操作员', role: 'operator', csrf_token: null, expires_at: null})
export const useSession = () => useContext(Context)
export const canOperate = (session: Session) => session.role === 'operator'
export function SessionProvider({children}: {children: ReactNode}) {
  const [session, setSession] = useState<Session | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let active = true
    const refresh = () => {void loadSession(true).then(s => {if (active) {setSession(s); setError(false)}}).catch(() => {if (active) setError(true)})}
    const expired = () => {clearSession(); setError(true)}
    refresh(); const timer = setInterval(refresh, 60000)
    window.addEventListener('mc-session-expired', expired)
    return () => {active = false; clearInterval(timer); window.removeEventListener('mc-session-expired', expired)}
  }, [])
  if (error) return <div className="wb-session-gate" role="alert"><h1>访问会话已失效或服务暂不可用</h1><p>请刷新页面重新验证身份。已提交的任务由采集电脑继续执行，不依赖此页面保持打开。</p><button onClick={() => window.location.reload()}>刷新并重新连接</button></div>
  if (!session) return <p role="status" className="wb-session-gate">正在验证工作台访问身份…</p>
  return <Context.Provider value={session}>{children}</Context.Provider>
}
