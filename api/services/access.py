"""Cloudflare Access JWT validation and an ASGI boundary for HTTP AND WebSocket.

Only an operator-configured issuer is contacted. No token-supplied URL is used.
Local and remote listeners wrap the same application/executor in one process.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature
from starlette.responses import JSONResponse

from api.services import workbench_store as store


MAX_BODY_BYTES = 16 * 1024 * 1024


class AccessDenied(ValueError):
    pass


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise AccessDenied('invalid encoding')
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


@dataclass(frozen=True)
class AccessSettings:
    issuer: str
    audience: str
    origin: str
    operators: frozenset[str]
    viewers: frozenset[str] = frozenset()

    def __post_init__(self):
        if not re.fullmatch(r'https://[a-z0-9-]+\.cloudflareaccess\.com', self.issuer):
            raise ValueError('MC_ACCESS_TEAM 必须是 Cloudflare Access 团队名')
        parsed = urlsplit(self.origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('MC_PUBLIC_ORIGIN 需要不含路径的 HTTPS 源地址')
        if not self.audience or not self.operators or self.operators & self.viewers:
            raise ValueError('必须配置受众、至少一个操作员，角色名单不得重叠')

    @classmethod
    def from_env(cls):
        team = os.environ.get('MC_ACCESS_TEAM', '')
        names = lambda key: frozenset(x.strip().lower() for x in os.environ.get(key, '').split(',') if x.strip())
        return cls(f'https://{team}.cloudflareaccess.com', os.environ.get('MC_ACCESS_AUD', ''),
                   os.environ.get('MC_PUBLIC_ORIGIN', '').rstrip('/'), names('MC_OPERATORS'), names('MC_VIEWERS'))


class AccessVerifier:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client
        self.keys = {}
        self.refreshed = 0
        self.attempted = -math.inf
        self.lock = asyncio.Lock()

    async def key(self, kid):
        async with self.lock:
            now = time.monotonic()
            stale = now - self.refreshed > 300
            if (kid not in self.keys or stale) and now - self.attempted >= 10:
                self.attempted = now
                async def fetch(client):
                    response = await client.get(self.settings.issuer + '/cdn-cgi/access/certs')
                    response.raise_for_status()
                    if len(response.content) > 256000:
                        raise AccessDenied('invalid certificates')
                    return response.json()
                try:
                    if self.client:
                        data = await fetch(self.client)
                    else:
                        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                            data = await fetch(client)
                    keys = {}
                    for jwk in data['keys'][:20]:
                        if jwk.get('kty') == 'RSA' and jwk.get('alg', 'RS256') == 'RS256' and jwk.get('use', 'sig') == 'sig':
                            n, e = int.from_bytes(decode(jwk['n']), 'big'), int.from_bytes(decode(jwk['e']), 'big')
                            if n.bit_length() >= 2048:
                                keys[jwk['kid']] = rsa.RSAPublicNumbers(e, n).public_key()
                    if not keys:
                        raise AccessDenied('empty keys')
                    self.keys, self.refreshed = keys, now
                except Exception as exc:
                    raise AccessDenied('identity provider unavailable') from exc
            if kid not in self.keys or time.monotonic() - self.refreshed > 300:
                raise AccessDenied('unknown or stale signing key')
            return self.keys[kid]

    async def verify(self, token):
        try:
            if len(token) > 16000:
                raise AccessDenied('invalid token')
            header, body, signature = token.split('.')
            metadata = json.loads(decode(header))
            claims = json.loads(decode(body))
            if metadata.get('alg') != 'RS256' or not isinstance(metadata.get('kid'), str) or metadata.get('crit'):
                raise AccessDenied('unsupported algorithm')
            key = await self.key(metadata['kid'])
            key.verify(decode(signature), (header + '.' + body).encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
            now = time.time()
            exp = claims.get('exp')
            if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp) or exp <= now:
                raise AccessDenied('expired token')
            for field in ('nbf', 'iat'):
                if field in claims and (isinstance(claims[field], bool) or not isinstance(claims[field], (int, float)) or not math.isfinite(claims[field]) or claims[field] > now + 30):
                    raise AccessDenied('invalid token time')
            audience = claims.get('aud')
            if isinstance(audience, str):
                audience = [audience]
            if not isinstance(audience, list) or self.settings.audience not in audience or claims.get('iss') != self.settings.issuer:
                raise AccessDenied('wrong audience or issuer')
            if claims.get('type') not in (None, 'app') or not isinstance(claims.get('sub'), str) or not claims['sub']:
                raise AccessDenied('invalid subject')
            email = claims.get('email', '').lower()
            role = 'operator' if email in self.settings.operators else 'viewer' if email in self.settings.viewers else None
            if role is None:
                raise AccessDenied('identity not allowed')
            return dict(sub=claims['sub'], email=email, role=role, expires_at=int(exp), mode='remote')
        except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature) as exc:
            raise AccessDenied('authentication failed') from exc


class Boundary:
    def __init__(self, app, settings=None, *, local_origin='http://127.0.0.1:8080', verifier=None):
        self.app = app
        self.settings = settings
        self.local_origin = local_origin
        self.verifier = verifier or (AccessVerifier(settings) if settings else None)
        self.csrf_key = secrets.token_bytes(32)

    def csrf(self, actor):
        expires = min(actor['expires_at'], int(time.time()) + 3600)
        data = f"{actor['sub']}:{expires}"
        return str(expires) + '.' + hmac.new(self.csrf_key, data.encode(), hashlib.sha256).hexdigest()

    def valid_csrf(self, value, actor):
        try:
            expiry, digest = value.split('.')
            if int(expiry) <= time.time() or int(expiry) > actor['expires_at']:
                return False
            expected = hmac.new(self.csrf_key, f"{actor['sub']}:{expiry}".encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(digest, expected)
        except (ValueError, TypeError):
            return False

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {}
        for key, value in scope.get('headers', []):
            name = key.decode('latin1').lower()
            if name in headers and name in ('host', 'origin', 'cf-access-jwt-assertion', 'x-csrf-token'):
                return await self.reject(scope, receive, send, 400, '请求头重复')
            headers[name] = value.decode('latin1')
        origin = self.settings.origin if self.settings else self.local_origin
        if headers.get('host') != urlsplit(origin).netloc:
            return await self.reject(scope, receive, send, 403, '访问域名不匹配')
        try:
            actor = await self.verifier.verify(headers.get('cf-access-jwt-assertion', '')) if self.settings else dict(sub='local', email='本机操作员', role='operator', expires_at=int(time.time())+86400, mode='local')
        except AccessDenied:
            return await self.reject(scope, receive, send, 401, '身份验证已失效，请通过受保护的访问地址重新登录')
        scope.setdefault('state', {})['identity'] = actor
        websocket = scope['type'] == 'websocket'
        write = not websocket and scope['method'] not in ('GET', 'HEAD', 'OPTIONS')
        if write:
            try:
                scope['state']['audit_id']=store.audit(actor['sub'],actor['email'],scope['method'],scope['path'],0)
            except Exception:
                return await self.reject(scope,receive,send,503,'无法记录操作审计，写入已暂停')
        if websocket or write:
            if headers.get('origin') != origin:
                return await self.reject(scope, receive, send, 403, '请求来源校验失败')
        if write and actor['role'] != 'operator':
            return await self.reject(scope, receive, send, 403, '只读账号不能执行此操作')
        if write and not self.valid_csrf(headers.get('x-csrf-token', ''), actor):
            return await self.reject(scope, receive, send, 403, '会话校验失效，请刷新后重试', code='csrf_expired')
        if not websocket and scope['path'] == '/api/session' and scope['method'] == 'GET':
            return await JSONResponse({**actor, 'csrf_token': self.csrf(actor)}, headers={'Cache-Control': 'no-store'})(scope, receive, send)
        if not websocket:
            try:
                length=int(headers.get('content-length','0'))
                if length<0:
                    raise ValueError()
            except ValueError:
                return await self.reject(scope,receive,send,400,'无效请求长度')
            if length>MAX_BODY_BYTES:
                return await self.reject(scope,receive,send,413,'请求体不能超过 16 MB')
        consumed=0
        async def bounded_receive():
            nonlocal consumed
            message=await receive()
            if message['type']=='http.request':
                consumed+=len(message.get('body',b''))
                if consumed>MAX_BODY_BYTES:
                    from starlette.exceptions import HTTPException
                    raise HTTPException(413,'请求体不能超过 16 MB')
            return message
        status = 500
        async def protected_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                message['headers'] = [(k,v) for k,v in message.get('headers',[]) if k.lower() not in (b'cache-control', b'content-security-policy')]
                message['headers'] += [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer'), (b'content-security-policy', b"frame-ancestors 'none'; base-uri 'self'; object-src 'none'")]
            await send(message)
        try:
            if websocket and self.settings:
                try:
                    await asyncio.wait_for(self.app(scope, bounded_receive, protected_send), timeout=max(0,actor['expires_at']-time.time()))
                except asyncio.TimeoutError:
                    await send({'type':'websocket.close','code':4401})
            else:
                await self.app(scope, bounded_receive, protected_send)
        finally:
            if write:
                # Path only, never request body, query values, cookies or JWT.
                store.finish_audit(scope['state']['audit_id'],status)

    async def reject(self, scope, receive, send, status, detail, **extra):
        if scope.get('state',{}).get('audit_id'):
            store.finish_audit(scope['state']['audit_id'],status)
        if scope['type'] == 'websocket':
            await send({'type':'websocket.close','code':4401 if status==401 else 4403})
        else:
            await JSONResponse({'detail':detail, **extra}, status_code=status, headers={'Cache-Control':'no-store'})(scope, receive, send)
