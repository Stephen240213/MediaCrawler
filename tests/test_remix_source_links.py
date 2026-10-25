"""Resolve original links locally; never visit a real platform or model."""
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

import pytest

from api.services import analytics
from api.services.remix import providers, source_links
from tests.test_content_remix import client, isolated, report, submit

NOTE='fixture-note'
BASE='https://www.xiaohongshu.com/explore/'+NOTE


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    async def model(evidence):return report(evidence),{},'fixture'
    monkeypatch.setattr(providers,'analyze',model)


@pytest.mark.parametrize('url',[
    BASE+'?xsec_token=fixture-token%3D&xsec_source=pc_search',
    'https://www.xiaohongshu.com/discovery/item/'+NOTE+'?xsec_token=fixture-token%3D&xsec_source=pc_feed',
    'https://xiaohongshu.com/explore/'+NOTE+'?xsec_token=fixture-token%3D&other=tracking',
])
def test_signed_original_link_preserved_and_tracking_removed(url):
    target=source_links.note_url(NOTE,[url])
    parsed=urlsplit(target)
    assert parsed.hostname=='www.xiaohongshu.com' and parsed.path=='/explore/'+NOTE
    query=parse_qs(parsed.query)
    assert query['xsec_token']==['fixture-token=']
    assert set(query)=={'xsec_token','xsec_source'}


def test_raw_plus_and_urlencoded_token_roundtrip():
    for token in ['fixture+token/=', 'fixture%2Btoken%2F%3D']:
        target=source_links.note_url(NOTE,[BASE+'?xsec_token='+token])
        assert parse_qs(urlsplit(target).query)['xsec_token']==['fixture+token/=']


@pytest.mark.parametrize('url',[
    'https://evil.test/explore/'+NOTE+'?xsec_token=bad',
    'https://www.xiaohongshu.com.evil.test/explore/'+NOTE+'?xsec_token=bad',
    'https://user:pass@www.xiaohongshu.com/explore/'+NOTE+'?xsec_token=bad',
    'http://www.xiaohongshu.com/explore/'+NOTE+'?xsec_token=bad',
    'javascript:alert(1)',
    'https://www.xiaohongshu.com/explore/other-note?xsec_token=bad',
    BASE+'?xsec_token=None',
    BASE+'?xsec_token=a&xsec_token=b',
    BASE+'\r\n?xsec_token=bad',
])
def test_untrusted_redirect_and_wrong_note_rejected(url):
    assert source_links.note_url(NOTE,[url])==BASE


def test_native_token_fallback_and_no_token():
    target=source_links.note_url(NOTE,[BASE],token='fixture-token=')
    assert parse_qs(urlsplit(target).query)['xsec_token']==['fixture-token=']
    assert source_links.note_url(NOTE)==BASE


def test_historical_job_resolves_current_collected_url_without_reanalysis(client):
    job=submit(client)
    assert job['source']['url']==BASE
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute("UPDATE analytics_content SET url=? WHERE content_id=?",(BASE+'?xsec_token=current-fixture&xsec_source=pc_search',NOTE))
    response=client.get('/api/remix/jobs/'+job['id']+'/source-link',follow_redirects=False)
    assert response.status_code==302
    assert parse_qs(urlsplit(response.headers['location']).query)['xsec_token']==['current-fixture']
    assert response.headers['cache-control']=='no-store' and response.headers['referrer-policy']=='no-referrer'
    saved=client.get('/api/remix/jobs/'+job['id']).json()
    assert 'current-fixture' not in json.dumps(saved)
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute("UPDATE analytics_content SET url=? WHERE content_id=?",(BASE+'?xsec_token=newer-fixture&xsec_source=pc_feed',NOTE))
    refreshed=client.get('/api/remix/jobs/'+job['id']+'/source-link',follow_redirects=False)
    assert parse_qs(urlsplit(refreshed.headers['location']).query)['xsec_token']==['newer-fixture']


def test_native_url_preferred_and_native_token_fallback(client):
    job=submit(client)
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute('ALTER TABLE xhs_note ADD COLUMN note_url TEXT')
        db.execute('ALTER TABLE xhs_note ADD COLUMN xsec_token TEXT')
        db.execute('UPDATE xhs_note SET note_url=?,xsec_token=? WHERE note_id=?',(BASE,'native-fixture',NOTE))
    response=client.get('/api/remix/jobs/'+job['id']+'/source-link',follow_redirects=False)
    assert parse_qs(urlsplit(response.headers['location']).query)['xsec_token']==['native-fixture']
    with sqlite3.connect(analytics.db_path()) as db:
        db.execute('UPDATE xhs_note SET note_url=? WHERE note_id=?',(BASE+'?xsec_token=signed-native&xsec_source=pc_feed',NOTE))
    response=client.get('/api/remix/jobs/'+job['id']+'/source-link',follow_redirects=False)
    assert parse_qs(urlsplit(response.headers['location']).query)['xsec_token']==['signed-native']


def test_unknown_job_link_returns_404(client):
    assert client.get('/api/remix/jobs/'+'a'*32+'/source-link',follow_redirects=False).status_code==404
