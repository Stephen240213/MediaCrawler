"""Real RSA signatures; identity-provider responses are local mocked fixtures."""
import base64
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.services.access import AccessSettings, AccessVerifier, AccessDenied, Boundary
from api.services import workbench_store as store


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


@pytest.fixture
def signing():
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=key.public_key().public_numbers()
    jwk={'kid':'test-key','kty':'RSA','alg':'RS256','n':b64(public.n.to_bytes(256,'big')),'e':b64(public.e.to_bytes(3,'big'))}
    settings=AccessSettings('https://test-team.cloudflareaccess.com','audience','https://crawler.test',frozenset({'operator@test.invalid'}),frozenset({'viewer@test.invalid'}))
    def token(**changes):
        claims={'iss':settings.issuer,'aud':['audience'],'sub':'operator-sub','email':'operator@test.invalid','exp':int(time.time())+300,'iat':int(time.time())-1,'type':'app'}
        claims.update(changes)
        header=b64(json.dumps({'alg':'RS256','kid':'test-key'}).encode())
        body=b64(json.dumps(claims).encode()); unsigned=(header+'.'+body).encode()
        return unsigned.decode()+'.'+b64(key.sign(unsigned,padding.PKCS1v15(),hashes.SHA256()))
    return key,jwk,settings,token


def verifier(signing):
    _,jwk,settings,_=signing
    def response(request):
        assert str(request.url)==settings.issuer+'/cdn-cgi/access/certs'
        return httpx.Response(200,json={'keys':[jwk]})
    return AccessVerifier(settings,httpx.AsyncClient(transport=httpx.MockTransport(response)))


@pytest.mark.asyncio
async def test_valid_and_restricted_identity(signing):
    verify=verifier(signing);token=signing[3]
    assert (await verify.verify(token()))['role']=='operator'
    assert (await verify.verify(token(email='viewer@test.invalid')))['role']=='viewer'
    for changes in [{'iss':'https://evil.test'},{'aud':['other']},{'exp':0},{'nbf':time.time()+1000},{'email':'stranger@test.invalid'},{'exp':True},{'sub':''},{'type':'service'}]:
        with pytest.raises(AccessDenied): await verify.verify(token(**changes))
    original=token()
    with pytest.raises(AccessDenied): await verify.verify(original[:-10]+'AAAAAAAAAA')
    for malformed in ['', 'none.none.none', b64(b'{"alg":"none"}')+'.e30.AA']:
        with pytest.raises(AccessDenied): await verify.verify(malformed)
    await verify.client.aclose()


@pytest.mark.asyncio
async def test_no_key_url_override_and_stale_cache_fail_closed(signing):
    verify=verifier(signing); token=signing[3]
    await verify.verify(token())
    verify.refreshed-=1000
    with pytest.raises(AccessDenied): await verify.verify(token())
    await verify.client.aclose()


@pytest.fixture
def protected(signing):
    app=FastAPI()
    @app.get('/')
    def home(): return {'page':'protected'}
    @app.post('/api/write')
    def write(): return {'saved':True}
    @app.websocket('/api/ws/logs')
    async def ws(socket:WebSocket):
        await socket.accept();await socket.send_json({'ok':True});await socket.receive_text()
    boundary=Boundary(app,signing[2],verifier=verifier(signing))
    with TestClient(boundary,base_url='https://crawler.test') as client:
        yield client,boundary


def auth(signing,**claims):
    return {'Cf-Access-Jwt-Assertion':signing[3](**claims)}


def test_entire_site_protected_and_no_loopback_bypass(protected,signing):
    client,_=protected
    for path in ['/','/api/session','/docs','/api/write','/assets/test.js']:
        assert client.get(path).status_code==401
    assert client.get('/',headers={'Cf-Access-Authenticated-User-Email':'operator@test.invalid'}).status_code==401
    assert client.get('/',headers={**auth(signing),'Host':'evil.test'}).status_code==403
    response=client.get('/',headers=auth(signing))
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'


def test_csrf_origin_roles_and_audit(protected,signing):
    client,_=protected
    headers=auth(signing)
    session=client.get('/api/session',headers=headers).json()
    assert session['role']=='operator'
    assert client.post('/api/write',headers=headers).status_code==403
    assert client.post('/api/write',headers={**headers,'Origin':'https://evil.test','X-CSRF-Token':session['csrf_token']}).status_code==403
    assert client.post('/api/write',headers={**headers,'Origin':'https://crawler.test'}).json()['code']=='csrf_expired'
    assert client.post('/api/write',headers={**headers,'Origin':'https://crawler.test','X-CSRF-Token':session['csrf_token']}).json()['saved']
    viewer=auth(signing,sub='viewer-sub',email='viewer@test.invalid')
    viewer_csrf=client.get('/api/session',headers=viewer).json()['csrf_token']
    assert client.post('/api/write',headers={**viewer,'Origin':'https://crawler.test','X-CSRF-Token':viewer_csrf}).status_code==403
    # An operator's token is bound to that subject and cannot become another's.
    assert client.post('/api/write',headers={**auth(signing,sub='another-sub'),'Origin':'https://crawler.test','X-CSRF-Token':session['csrf_token']}).status_code==403
    with store.connect() as db:
        events=db.execute('SELECT actor,identity,path,status FROM audit_events').fetchall()
    assert any(r==('operator-sub','operator@test.invalid','/api/write',200) for r in events)
    assert any(r[-1]==403 for r in events)
    assert signing[3]() not in str(events)


def test_websocket_requires_jwt_and_origin(protected,signing):
    client,_=protected
    for headers in [{},{**auth(signing),'Origin':'https://evil.test'}]:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('wss://crawler.test/api/ws/logs',headers=headers): pass
    with client.websocket_connect('wss://crawler.test/api/ws/logs',headers={**auth(signing),'Origin':'https://crawler.test'}) as ws:
        assert ws.receive_json()=={'ok':True}
        ws.send_text('done')


def test_local_guard_is_separate_and_does_not_trust_forwarded_headers():
    app=FastAPI()
    @app.get('/')
    def home():return {'local':True}
    @app.post('/api/write')
    def write():return {'saved':True}
    with TestClient(Boundary(app),base_url='http://127.0.0.1:8080') as client:
        assert client.get('/').status_code==200
        assert client.get('/',headers={'Host':'evil.test','X-Forwarded-Host':'127.0.0.1:8080'}).status_code==403
        assert client.post('/api/write',headers={'Origin':'https://evil.test'}).status_code==403
        csrf=client.get('/api/session').json()['csrf_token']
        assert client.post('/api/write',headers={'Origin':'http://127.0.0.1:8080','X-CSRF-Token':csrf}).status_code==200


def test_configuration_fails_closed():
    with pytest.raises(ValueError):AccessSettings('https://evil.test','aud','https://crawler.test',frozenset({'a'}))
    with pytest.raises(ValueError):AccessSettings('https://team.cloudflareaccess.com','aud','http://crawler.test',frozenset({'a'}))


def test_body_limit_and_validation_do_not_echo_credentials(protected,signing,monkeypatch):
    from api.services import access
    client,_=protected
    headers=auth(signing); csrf=client.get('/api/session',headers=headers).json()['csrf_token']
    monkeypatch.setattr(access,'MAX_BODY_BYTES',64)
    r=client.post('/api/write',headers={**headers,'Origin':'https://crawler.test','X-CSRF-Token':csrf},content=b'x'*65)
    assert r.status_code==413
    from api.main import app
    with TestClient(app) as local:
        r=local.post('/api/crawler/start',json={'platform':'invalid','cookies':'do-not-echo'})
        assert r.status_code==422
        assert 'do-not-echo' not in r.text


def test_audit_failure_prevents_mutation(protected,signing,monkeypatch):
    client,_=protected
    def fail(*_):raise OSError('disk full')
    headers=auth(signing);csrf=client.get('/api/session',headers=headers).json()['csrf_token']
    monkeypatch.setattr(store,'audit',fail)
    assert client.post('/api/write',headers={**headers,'Origin':'https://crawler.test','X-CSRF-Token':csrf}).status_code==503


def test_local_and_remote_share_executor_and_stop_scope(signing,monkeypatch):
    import sys
    import api.services as services
    from api.main import app
    from api.routers import crawler as routes
    from api.services.crawler_manager import CrawlerManager
    manager=CrawlerManager()
    monkeypatch.setattr(services,'crawler_manager',manager)
    monkeypatch.setattr(routes,'crawler_manager',manager)
    monkeypatch.setattr(manager,'_build_command',lambda _: [sys.executable,'-u','-c','import time;time.sleep(20)'])
    with TestClient(app) as local, TestClient(Boundary(app,signing[2],verifier=verifier(signing)),base_url='https://crawler.test') as remote:
        started=local.post('/api/crawler/start',headers={'Idempotency-Key':'same-request-key-0001'},json={'platform':'xhs','keywords':'fixture'})
        assert started.status_code==200,started.text
        task_id=started.json()['task_id']
        headers=auth(signing)
        assert remote.get('/api/crawler/status',headers=headers).json()['task']['id']==task_id
        csrf=remote.get('/api/session',headers=headers).json()['csrf_token']
        writes={**headers,'Origin':'https://crawler.test','X-CSRF-Token':csrf}
        assert remote.post('/api/crawler/stop',headers=writes,json={'task_id':'old-task'}).status_code==409
        assert remote.post('/api/crawler/stop',headers=writes,json={'task_id':task_id}).status_code==200
        assert local.get('/api/crawler/status').json()['status']=='idle'


def test_websocket_closes_when_identity_expires(protected,signing):
    client,_=protected
    headers={**auth(signing,exp=int(time.time())+2),'Origin':'https://crawler.test'}
    with client.websocket_connect('wss://crawler.test/api/ws/logs',headers=headers) as ws:
        assert ws.receive_json()=={'ok':True}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code==4401


def test_new_operations_require_operator_and_csrf(signing, tmp_path, monkeypatch):
    from api.main import app
    from api.services import analytics
    monkeypatch.setattr(store, 'DB_PATH', tmp_path/'state.db')
    monkeypatch.setitem(analytics.sqlite_db_config,'db_path',str(tmp_path/'collection.db'))
    store.save_document('task',{'id':'terminal','outcome':'completed'})
    with TestClient(Boundary(app,signing[2],verifier=verifier(signing)), base_url='https://crawler.test') as client:
        viewer=auth(signing,sub='viewer-sub',email='viewer@test.invalid')
        viewer_csrf=client.get('/api/session',headers=viewer).json()['csrf_token']
        for method,path in [('delete','/api/workbench/tasks/terminal'),('post','/api/analytics/note-download?platform=xhs&content_id=a')]:
            assert getattr(client,method)(path,headers={**viewer,'Origin':'https://crawler.test','X-CSRF-Token':viewer_csrf}).status_code==403
        operator=auth(signing)
        assert client.delete('/api/workbench/tasks/terminal',headers=operator).status_code==403
        csrf=client.get('/api/session',headers=operator).json()['csrf_token']
        assert client.delete('/api/workbench/tasks/terminal',headers={**operator,'Origin':'https://crawler.test','X-CSRF-Token':csrf}).status_code==200


def test_remix_mutations_keep_operator_and_csrf_boundary(signing,tmp_path,monkeypatch):
    from api.main import app
    from api.services import analytics
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'workbench.db')
    monkeypatch.setitem(analytics.sqlite_db_config,'db_path',str(tmp_path/'collection.db'))
    endpoints=[('post','/api/remix/jobs'),('post','/api/remix/uploads'),('put','/api/remix/uploads/'+'a'*32+'?offset=0'),
               ('post','/api/remix/jobs/'+'a'*32+'/cancel'),('post','/api/remix/jobs/'+'a'*32+'/retry'),('post','/api/remix/jobs/'+'a'*32+'/draft'),
               ('post','/api/remix/jobs/'+'a'*32+'/productions')]
    with TestClient(Boundary(app,signing[2],verifier=verifier(signing)),base_url='https://crawler.test') as client:
        viewer=auth(signing,sub='viewer-sub',email='viewer@test.invalid')
        csrf=client.get('/api/session',headers=viewer).json()['csrf_token']
        for method,path in endpoints:
            assert getattr(client,method)(path,headers={**viewer,'Origin':'https://crawler.test','X-CSRF-Token':csrf}).status_code==403
            assert getattr(client,method)(path,headers=auth(signing)).status_code==403
        operator=auth(signing);csrf=client.get('/api/session',headers=operator).json()['csrf_token']
        response=client.post('/api/remix/uploads',headers={**operator,'Origin':'https://crawler.test','X-CSRF-Token':csrf},json={'filename':'test.mp4','size':10,'request_key':'remix-upload-123456'})
        assert response.status_code==200
        with store.connect() as db:
            assert db.execute("SELECT count(*) FROM audit_events WHERE path='/api/remix/uploads' AND status=200").fetchone()[0]==1
