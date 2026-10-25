/**
 * Fixed upstream relay for an Access-protected workers.dev host.
 * JWT verification is ALSO mandatory at the Mac's protected listener.
 * No arbitrary URL proxy, token storage, database, or credentials are used here.
 */
// Setup hints only: these claims are NOT used to authenticate or authorize.
// The Mac must still verify the signature against its configured issuer/AUD.
// Never return the assertion, email, cookies, or arbitrary token claims.
function setupHints(assertion) {
  try {
    const part = assertion.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(part.padEnd(Math.ceil(part.length / 4) * 4, '=')), c => c.charCodeAt(0))))
    const audiences = typeof payload.aud === 'string' ? [payload.aud] : payload.aud
    if (typeof payload.iss !== 'string' || payload.iss.length > 300 || !Array.isArray(audiences)) return {}
    return {
      access_issuer: payload.iss,
      access_audiences: audiences.filter(v => typeof v === 'string' && v.length <= 300).slice(0, 10),
    }
  } catch {return {}}
}

export default {
  async fetch(request, env) {
    const fail = (status, detail) => Response.json({detail}, {status, headers: {'Cache-Control': 'no-store'}})
    // Access must be enabled for the entire Worker. Missing assertions fail
    // closed even while the account/application is still being configured.
    const assertion = request.headers.get('Cf-Access-Jwt-Assertion')
    if (!assertion || assertion.length > 16000) return fail(401, '请先通过 Cloudflare Access 登录')
    let target, publicOrigin
    try {
      const upstream = new URL(env.UPSTREAM_ORIGIN)
      publicOrigin = new URL(env.PUBLIC_ORIGIN)
      if (upstream.protocol !== 'https:' || !/^[a-z0-9-]+\.trycloudflare\.com$/.test(upstream.hostname) || upstream.port || upstream.username || upstream.password || upstream.pathname !== '/' || upstream.search || upstream.hash) throw new Error('upstream')
      if (publicOrigin.protocol !== 'https:' || publicOrigin.port || publicOrigin.username || publicOrigin.password || publicOrigin.pathname !== '/' || publicOrigin.search || publicOrigin.hash || !/^[a-z0-9.-]+\.workers\.dev$/.test(publicOrigin.hostname)) throw new Error('origin')
      const incoming = new URL(request.url)
      if (incoming.origin !== publicOrigin.origin) return fail(403, '访问域名不匹配')
      // Assignment preserves the fixed upstream host, even for //path inputs.
      target = new URL(upstream.origin)
      target.pathname = incoming.pathname
      target.search = incoming.search
    } catch {
      return Response.json({detail: '远程入口尚未完成配置', setup: setupHints(assertion)}, {status: 503, headers: {'Cache-Control': 'no-store'}})
    }
    const headers = new Headers(request.headers)
    headers.delete('Host') // Tunnel routing must use its own hostname.
    headers.delete('Cookie') // Access cookies need not reach the Mac.
    headers.delete('Authorization')
    headers.delete('X-Forwarded-Host')
    headers.delete('X-Forwarded-For')
    const length = headers.get('Content-Length')
    if (length && (!/^\d+$/.test(length) || Number(length) > 16 * 1024 * 1024)) return fail(413, '请求体不能超过 16 MB')
    try {
      // Stream uploads/downloads; never parse or store content at the Worker.
      // The Mac independently checks JWT signatures, roles, Origin and CSRF.
      const response = await fetch(new Request(target, {
        method: request.method, headers,
        body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
        redirect: 'manual', duplex: 'half',
      }))
      if (response.status === 101) return response // Preserve WebSocket upgrade.
      const outgoing = new Response(response.body, response)
      outgoing.headers.set('Cache-Control', 'no-store')
      return outgoing
    } catch {
      return fail(502, '采集电脑暂时离线，请检查本机工作台和 Tunnel')
    }
  },
}
