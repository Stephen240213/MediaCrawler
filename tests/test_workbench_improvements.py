"""Requested UI operations use only disposable databases/media and mocked CDN IO."""
import io
import json
import sqlite3
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.routers import workbench
from api.services import analytics, workbench_store as store, note_bundle
from api.services.comment_analysis import analyze, codes
from database import analytics as schema


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setitem(analytics.sqlite_db_config, 'db_path', str(tmp_path/'collection.db'))
    monkeypatch.setattr(store, 'DB_PATH', tmp_path/'tasks.db')
    monkeypatch.setattr(workbench, 'DATA_ROOT', tmp_path/'media')
    schema.migrate(analytics.db_path())
    with sqlite3.connect(analytics.db_path()) as db:
        schema.save(db, schema.normalize({'platform':'xhs','content_id':'note1','title':'旅行/笔记','likes':12}))
    with TestClient(app) as c:
        yield c


def test_delete_retains_data_and_idempotency(client):
    task = {'id':'finished','outcome':'failed','started_at':'2026-01-01'}
    store.reserve_task(task,'actor','request-key','fingerprint')
    store.append_log('finished','now','info','log')
    store.save_document('draft',{'id':'draft','title':'keep'})
    assert client.delete('/api/workbench/tasks/finished').status_code == 200
    assert store.get_document('task','finished') is None
    assert store.task_logs('finished') == []
    assert store.get_document('draft','draft')['title']=='keep'
    assert analytics.overview({})['summary']['content_count']==1
    assert store.reserve_task(task,'actor','request-key','fingerprint')[0]=='replay'
    assert client.delete('/api/workbench/tasks/finished').status_code == 404
    store.save_document('task',{'id':'running','outcome':'running'})
    assert client.delete('/api/workbench/tasks/running').status_code==409


def test_rule_analysis_and_filter(client):
    bodies=[{'body':'请问价格多少？服务不错，值得推荐','n':2}, {'body':'不推荐，太贵，服务差劲','n':1}, {'body':'哈哈哈[偷笑]但是还是','n':1}]
    result=analyze(bodies,5)
    assert result['sample_comments']==4 and result['question_count']==2
    assert dict((p['label'],p['value']) for p in result['themes'])['价格与预算']==3
    assert codes('不推荐，不好看')[1]=='负向线索'
    assert codes('很喜欢但太贵')[1]=='褒贬并存'
    assert not any(p['label'] in {'哈哈哈','偷笑','但是','还是'} for p in result['words'])
    with sqlite3.connect(analytics.db_path()) as db:
        for i,row in enumerate(bodies):
            schema.save(db,schema.normalize(dict(platform='xhs',content_id='note1',comment_id=str(i),body=row['body']),kind='comment'),'comment')
    overview=client.get('/api/analytics/overview').json()
    assert overview['comment_analysis']['total_comments']==3
    r=client.get('/api/analytics/details',params={'kind':'comment','comment_code':'负向线索'}).json()
    assert r['total']==1 and '不推荐' in r['items'][0]['body']
    assert client.get('/api/analytics/details',params={'kind':'comment','comment_code':'提问意图'}).json()['total']==1
    assert client.get('/api/analytics/overview',params={'platform':'dy'}).json()['comment_analysis']['sample_comments']==0


def test_bundle_local_media_and_native_body(client, tmp_path, monkeypatch):
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute('CREATE TABLE xhs_note(note_id TEXT,desc TEXT,image_list TEXT,video_url TEXT,type TEXT)')
        db.execute('INSERT INTO xhs_note VALUES (?,?,?,?,?)',('note1','完整正文','https://sns-img.xhscdn.com/1,https://sns-img.xhscdn.com/2','https://sns-video.xhscdn.com/video','video'))
    folder=workbench.DATA_ROOT/'xhs/media/note1';folder.mkdir(parents=True)
    (folder/'cover.jpg').write_bytes(b'image')
    outside=tmp_path/'private.png';outside.write_bytes(b'private')
    (folder/'leak.png').symlink_to(outside)
    calls=[]
    def fetch(url,budget,deadline):
        calls.append(url)
        return b'video', '.mp4'
    monkeypatch.setattr(note_bundle,'fetch_media',fetch)
    response=client.post('/api/analytics/note-download',params={'platform':'xhs','content_id':'note1'})
    assert response.status_code==200
    assert response.headers['x-bundle-warnings']=='0'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names=archive.namelist()
        assert len({n.split('/')[0] for n in names})==1
        assert all('..' not in n and not n.startswith('/') for n in names)
        assert any(n.endswith('/media/video.mp4') for n in names)
        assert not any('leak' in n for n in names)
        md=next(n for n in names if n.endswith('.md'))
        assert '完整正文' in archive.read(md).decode()
    assert calls==['https://sns-video.xhscdn.com/video']
    assert client.post('/api/analytics/note-download',params={'platform':'xhs','content_id':'missing'}).status_code==404


def test_bundle_missing_media_is_explicit(client):
    response=client.post('/api/analytics/note-download',params={'platform':'xhs','content_id':'note1'})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        report=next(n for n in archive.namelist() if n.endswith('Download Report.txt'))
        assert '没有可用媒体' in archive.read(report).decode()
    assert int(response.headers['x-bundle-warnings'])>=1


@pytest.mark.parametrize('url',['file:///etc/passwd','http://127.0.0.1/x','https://evil.example/x','https://xhscdn.com.evil.example/x','https://user:pass@xhscdn.com/x'])
def test_bundle_url_boundary(url):
    with pytest.raises(ValueError):
        note_bundle.fetch_media(url,100,time.monotonic()+1)


def test_bundle_rejects_private_dns(monkeypatch):
    monkeypatch.setattr(note_bundle.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='公网'):
        note_bundle.fetch_media('https://sns-img.xhscdn.com/x',100,time.monotonic()+1)


def test_bundle_redirect_is_revalidated(monkeypatch):
    monkeypatch.setattr(note_bundle.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('1.1.1.1',443))])
    original=httpx.Client
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    monkeypatch.setattr(note_bundle.httpx,'Client',lambda **k:original(transport=httpx.MockTransport(handler),**k))
    with pytest.raises(ValueError):
        note_bundle.fetch_media('https://sns-img.xhscdn.com/x',100,time.monotonic()+1)
    assert len(seen)==1
    assert seen[0].url.host=='1.1.1.1' and seen[0].headers['host']=='sns-img.xhscdn.com'
    assert seen[0].extensions['sni_hostname']=='sns-img.xhscdn.com'


@pytest.fixture
def cdn_transport(monkeypatch):
    original = httpx.Client
    monkeypatch.setattr(note_bundle.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('8.8.8.8',443))])
    def install(handler):
        monkeypatch.setattr(note_bundle.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    return install


@pytest.mark.parametrize('mime',['application/octet-stream','application/mp4','video/mp4',''])
def test_video_binary_mime_and_request_headers(cdn_transport, mime):
    body=b'\x00\x00\x00\x18ftypmp42' + b'fixture-video'
    def handler(request):
        assert request.url.scheme=='http'  # Signed source protocol must not be rewritten.
        assert request.headers['referer']=='https://www.xiaohongshu.com/'
        assert request.headers['user-agent'].startswith('Mozilla/')
        assert request.headers['accept-encoding']=='identity'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(200,headers={'content-type':mime},content=body)
    cdn_transport(handler)
    content,suffix=note_bundle.fetch_media('http://sns-video.xhscdn.com/extensionless',1000,time.monotonic()+5)
    assert content==body and suffix=='.mp4'


@pytest.mark.parametrize('mime,body',[('application/octet-stream',b'<html>blocked</html>'),('video/mp4',b'<html>blocked</html>'),('application/vnd.apple.mpegurl',b'#EXTM3U'),('video/mp4',b'')])
def test_video_error_response_is_not_saved(cdn_transport,mime,body):
    cdn_transport(lambda request:httpx.Response(200,headers={'content-type':mime},content=body))
    with pytest.raises(ValueError):
        note_bundle.fetch_media('https://sns-video.xhscdn.com/video',1000,time.monotonic()+5)


def test_video_falls_back_without_duplicate_file(client,monkeypatch):
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute('CREATE TABLE xhs_note(note_id TEXT,desc TEXT,video_url TEXT,type TEXT)')
        db.execute('INSERT INTO xhs_note VALUES (?,?,?,?)',('note1','正文','https://sns-video.xhscdn.com/expired,https://sns-video.xhscdn.com/working','video'))
    calls=[]
    def fetch(url,*args):
        calls.append(url)
        if url.endswith('expired'):
            request=httpx.Request('GET',url)
            raise httpx.HTTPStatusError('forbidden',request=request,response=httpx.Response(403,request=request))
        return b'\x00\x00\x00\x18ftypmp42video', '.mp4'
    monkeypatch.setattr(note_bundle,'fetch_media',fetch)
    response=client.post('/api/analytics/note-download',params={'platform':'xhs','content_id':'note1'})
    assert response.headers['x-bundle-videos']=='1'
    assert response.headers['x-bundle-warnings']=='0'
    assert len(calls)==2
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert len([n for n in archive.namelist() if n.endswith('.mp4')])==1


def test_video_failure_reports_specific_reason_without_signed_url(client,monkeypatch):
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute('CREATE TABLE xhs_note(note_id TEXT,desc TEXT,video_url TEXT,type TEXT)')
        db.execute('INSERT INTO xhs_note VALUES (?,?,?,?)',('note1','正文','https://sns-video.xhscdn.com/v?token=private','video'))
    def fetch(url,*args):
        request=httpx.Request('GET',url)
        raise httpx.HTTPStatusError('secret-url',request=request,response=httpx.Response(403,request=request))
    monkeypatch.setattr(note_bundle,'fetch_media',fetch)
    response=client.post('/api/analytics/note-download',params={'platform':'xhs','content_id':'note1'})
    from urllib.parse import unquote
    assert response.headers['x-bundle-videos']=='0'
    report=unquote(response.headers['x-bundle-report'])
    assert 'HTTP 403' in report and '视频未打包' in report
    assert 'private' not in report and 'token=' not in report


def test_fake_ip_dns_resolves_public_address_without_connecting_virtual_ip(cdn_transport,monkeypatch):
    seen=[]
    def handler(request):
        seen.append(request)
        if request.url.path=='/dns-query':
            assert request.url.host=='1.1.1.1'
            assert request.extensions['sni_hostname']=='cloudflare-dns.com'
            return httpx.Response(200,json={'Status':0,'Answer':[{'type':1,'data':'8.8.8.8'}]})
        assert request.url.host=='8.8.8.8'
        return httpx.Response(200,headers={'content-type':'application/octet-stream'},content=b'\x00\x00\x00\x18ftypmp42video')
    cdn_transport(handler)
    monkeypatch.setattr(note_bundle.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('198.18.0.42',443))])
    assert note_bundle.fetch_media('https://sns-video.xhscdn.com/video',1000,time.monotonic()+5)[1]=='.mp4'
    assert len(seen)==2


def test_fake_ip_dns_private_answer_still_rejected(cdn_transport,monkeypatch):
    cdn_transport(lambda request:httpx.Response(200,json={'Status':0,'Answer':[{'type':1,'data':'127.0.0.1'}]}))
    monkeypatch.setattr(note_bundle.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('198.18.0.42',443))])
    with pytest.raises(ValueError,match='公网'):
        note_bundle.fetch_media('https://sns-video.xhscdn.com/video',1000,time.monotonic()+5)


def test_avif_is_not_counted_as_video(cdn_transport):
    cdn_transport(lambda request:httpx.Response(200,headers={'content-type':'application/octet-stream'},content=b'\x00\x00\x00\x18ftypavifimage'))
    assert note_bundle.fetch_media('https://sns-img.xhscdn.com/image',1000,time.monotonic()+5)[1]=='.avif'
