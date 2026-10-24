"""Workbench persistence, file boundaries and real subprocess lifecycle regression checks."""
import asyncio
import base64
import io
import sys

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.main import app
from api.routers import workbench, data as data_router
from api.services import workbench_store as store
from api.services.crawler_manager import CrawlerManager
from api.schemas import CrawlerStartRequest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'DB_PATH', tmp_path / 'state.sqlite3')
    monkeypatch.setattr(workbench, 'DATA_ROOT', tmp_path / 'data')
    monkeypatch.setattr(data_router, 'DATA_DIR', tmp_path / 'data')
    with TestClient(app) as client:
        yield client


def upload_image(client):
    image = io.BytesIO()
    Image.new('RGB', (20, 20), 'purple').save(image, format='PNG')
    response = client.post('/api/workbench/assets', json={'name': '灵感.png', 'data': base64.b64encode(image.getvalue()).decode()})
    assert response.status_code == 201
    return response.json()


def test_asset_upload_preview_and_name(client):
    asset = upload_image(client)
    response = client.get(asset['url'])
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    listed = client.get('/api/workbench/assets').json()['assets']
    assert listed[0]['name'] == '灵感.png'
    assert listed[0]['id'] == asset['id']


@pytest.mark.parametrize('encoded', ['%%%bad', base64.b64encode(b'not an image').decode()])
def test_invalid_upload_is_rejected(client, encoded):
    assert client.post('/api/workbench/assets', json={'name': '../bad.png', 'data': encoded}).status_code == 400


def test_assets_do_not_read_symlinks_outside_data(client, tmp_path):
    asset = upload_image(client)
    outside = tmp_path / 'private.png'
    outside.write_bytes(b'private')
    link = workbench.DATA_ROOT / 'escaped.png'
    link.symlink_to(outside)
    assert client.get('/api/workbench/assets/file/escaped.png').status_code == 404
    assert all(a['id'] != 'escaped.png' for a in client.get('/api/workbench/assets').json()['assets'])
    assert client.get('/api/workbench/assets/file/%2e%2e/private.png').status_code == 404
    assert client.get(asset['url']).status_code == 200


def test_draft_roundtrip_and_conflict_preserve_saved_version(client):
    asset = upload_image(client)
    draft = {'title': '周末灵感', 'content': '一段文案', 'images': [asset['id']], 'tags': ['旅行'], 'version': 0}
    first = client.put('/api/workbench/drafts/roundtrip', json=draft)
    assert first.status_code == 200
    assert first.json()['version'] == 1
    assert client.put('/api/workbench/drafts/roundtrip', json={**draft, 'title': 'stale'}).status_code == 409
    assert client.get('/api/workbench/drafts').json()['drafts'][0]['title'] == '周末灵感'
    updated = client.put('/api/workbench/drafts/roundtrip', json={**draft, 'title': '更新', 'version': 1})
    assert updated.json()['version'] == 2
    assert store.get_document('draft', 'roundtrip')['images'] == [asset['id']]


def test_draft_rejects_missing_asset(client):
    assert client.put('/api/workbench/drafts/test', json={'images': ['missing.png']}).status_code == 404


def test_jsonl_preview_counts_and_limit(client):
    workbench.DATA_ROOT.mkdir(parents=True)
    (workbench.DATA_ROOT / 'notes.jsonl').write_text('{"title":"one"}\n\n{"title":"two"}\n')
    files = client.get('/api/data/files').json()['files']
    assert files[0]['record_count'] == 2
    result = client.get('/api/data/files/notes.jsonl?limit=1').json()
    assert result == {'data': [{'title': 'one'}], 'total': 2}
    assert client.get('/api/data/files/notes.jsonl?limit=-1').status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('code,expected,status', [(0, 'completed', 'idle'), (3, 'failed', 'error')])
async def test_task_records_actual_exit_and_redacts_cookie(client, monkeypatch, code, expected, status):
    manager = CrawlerManager()
    monkeypatch.setattr(manager, '_build_command', lambda config: [sys.executable, '-u', '-c', f'print("session=secret");raise SystemExit({code})'])
    assert await manager.start(CrawlerStartRequest(platform='xhs', keywords='test', cookies='session=secret'))
    await asyncio.wait_for(manager._read_task, 10)
    assert manager.status == status
    task = store.get_document('task', manager.task_id)
    assert task['outcome'] == expected
    assert task['exit_code'] == code
    assert 'secret' not in str(task)
    assert all('session=secret' not in log.message for log in manager.logs)


@pytest.mark.asyncio
async def test_stop_is_recorded_as_cancelled(client, monkeypatch):
    manager = CrawlerManager()
    monkeypatch.setattr(manager, '_build_command', lambda config: [sys.executable, '-u', '-c', 'import time;time.sleep(30)'])
    assert await manager.start(CrawlerStartRequest(platform='xhs', keywords='test'))
    assert await manager.stop()
    assert manager.process.poll() is not None
    assert store.get_document('task', manager.task_id)['outcome'] == 'cancelled'


def test_history_marks_old_running_task_interrupted(client):
    store.save_document('task', {'id': 'previous-run', 'started_at': '2026-10-24T20:00:00', 'outcome': 'running'})
    assert client.get('/api/workbench/tasks').json()['tasks'][0]['outcome'] == 'interrupted'


def test_asset_archive_is_reversible_and_keeps_draft_preview(client):
    asset = upload_image(client)
    client.put('/api/workbench/drafts/asset-reference', json={'images': [asset['id']]})
    assert client.patch('/api/workbench/assets/state/' + asset['id'], json={'archived': True}).status_code == 200
    assert client.get('/api/workbench/assets').json()['assets'] == []
    archived = client.get('/api/workbench/assets?archived=true').json()['assets']
    assert archived[0]['id'] == asset['id']
    assert client.get(asset['url']).status_code == 200
    assert client.patch('/api/workbench/assets/state/' + asset['id'], json={'archived': False}).status_code == 200
    assert len(client.get('/api/workbench/assets').json()['assets']) == 1
