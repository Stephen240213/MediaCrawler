import test from 'node:test'
import assert from 'node:assert/strict'
import relay from './index.mjs'
const env = {PUBLIC_ORIGIN: 'https://mediacrawler.example.workers.dev', UPSTREAM_ORIGIN: 'https://random-tunnel.trycloudflare.com'}
const auth = {'Cf-Access-Jwt-Assertion': 'signed-assertion-verified-by-origin'}

test('anonymous, missing configuration and invalid hosts fail closed', async () => {
  assert.equal((await relay.fetch(new Request(env.PUBLIC_ORIGIN), env)).status, 401)
  assert.equal((await relay.fetch(new Request(env.PUBLIC_ORIGIN, {headers: auth}), {})).status, 503)
  for (const origin of ['http://random-tunnel.trycloudflare.com','https://evil.example.com','https://user:password@random-tunnel.trycloudflare.com','https://random-tunnel.trycloudflare.com/private']) {
    assert.equal((await relay.fetch(new Request(env.PUBLIC_ORIGIN, {headers: auth}), {...env, UPSTREAM_ORIGIN: origin})).status, 503)
  }
  assert.equal((await relay.fetch(new Request('https://preview.example.workers.dev', {headers: auth}), env)).status, 403)
})

test('fixed relay streams data and preserves security headers without forwarding cookies', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async request => {
    assert.equal(request.url, env.UPSTREAM_ORIGIN + '/api/analytics/import?filename=history.json')
    assert.equal(request.headers.get('Cf-Access-Jwt-Assertion'), auth['Cf-Access-Jwt-Assertion'])
    assert.equal(request.headers.get('Origin'), env.PUBLIC_ORIGIN)
    assert.equal(request.headers.get('X-CSRF-Token'), 'csrf')
    assert.equal(request.headers.get('Cookie'), null)
    assert.equal(request.headers.get('Authorization'), null)
    assert.equal(request.redirect, 'manual')
    assert.equal(await request.text(), 'fixture-data')
    return new Response('fixture-response', {headers: {'Content-Type': 'application/json'}})
  }
  try {
    const response = await relay.fetch(new Request(env.PUBLIC_ORIGIN + '/api/analytics/import?filename=history.json', {method: 'POST', headers: {...auth, Origin: env.PUBLIC_ORIGIN, 'X-CSRF-Token': 'csrf', Cookie: 'CF_Authorization=private', Authorization: 'Bearer private'}, body: 'fixture-data'}), env)
    assert.equal(await response.text(), 'fixture-response')
    assert.equal(response.headers.get('Cache-Control'), 'no-store')
  } finally {globalThis.fetch = original}
})

test('does not reinterpret a double-slash path as an arbitrary target', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async request => {assert.equal(new URL(request.url).origin, env.UPSTREAM_ORIGIN); return new Response('ok')}
  try {assert.equal((await relay.fetch(new Request(env.PUBLIC_ORIGIN + '//evil.example.com/path', {headers: auth}), env)).status, 200)} finally {globalThis.fetch = original}
})

test('WebSocket responses are preserved, offline errors and large uploads are bounded', async () => {
  const original = globalThis.fetch
  const upgraded = {status: 101, webSocket: {}}
  try {
    globalThis.fetch = async () => upgraded
    assert.equal(await relay.fetch(new Request(env.PUBLIC_ORIGIN + '/api/ws/logs', {headers: {...auth, Upgrade: 'websocket', Origin: env.PUBLIC_ORIGIN}}), env), upgraded)
    globalThis.fetch = async () => {throw new Error('private internal error')}
    const response = await relay.fetch(new Request(env.PUBLIC_ORIGIN, {headers: auth}), env)
    assert.equal(response.status, 502)
    assert.ok(!(await response.text()).includes('private internal error'))
    assert.equal((await relay.fetch(new Request(env.PUBLIC_ORIGIN, {method: 'POST', headers: {...auth, 'Content-Length': String(17 * 1024 * 1024)}, body: 'x'}), env)).status, 413)
  } finally {globalThis.fetch = original}
})
