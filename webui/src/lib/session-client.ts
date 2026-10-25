export interface Session {mode: 'local' | 'remote'; sub: string; email: string; role: 'operator' | 'viewer'; csrf_token: string | null; expires_at: number | null}
let cached: Session | null = null
let loaded = 0
let pending: Promise<Session> | null = null
export function clearSession() {cached = null; loaded = 0}
export function loadSession(force = false): Promise<Session> {
  if (!force && cached && Date.now() - loaded < 60000) return Promise.resolve(cached)
  if (pending) return pending
  pending = fetch('/api/session', {credentials: 'same-origin', cache: 'no-store'}).then(async response => {
    if (!response.ok) throw new Error('无法验证访问身份')
    const r = {data: await response.json() as Session}
    if (!['local', 'remote'].includes(r.data?.mode) || !['operator', 'viewer'].includes(r.data?.role)) throw new Error('会话失效，请刷新页面重新登录')
    cached = r.data; loaded = Date.now(); return r.data
  }).finally(() => {pending = null})
  return pending
}
