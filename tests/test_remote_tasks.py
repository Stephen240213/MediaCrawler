import asyncio
import json
import os
import signal
import sqlite3
import sys
from pathlib import Path

import pytest
from api.schemas import CrawlerStartRequest
from api.services.crawler_manager import CrawlerManager
from api.services import workbench_store as store
from api.services.lease import Lease, available
from api.services.redaction import redact


@pytest.mark.asyncio
async def test_persistent_idempotency_and_scoped_stop(monkeypatch):
    manager=CrawlerManager()
    monkeypatch.setattr(manager,'_build_command',lambda _: [sys.executable,'-u','-c','import time; print("begin",flush=True);time.sleep(1);print("finished",flush=True)'])
    config=CrawlerStartRequest(platform='xhs',keywords='fixture')
    assert await manager.start(config,'request-key-0001','subject')
    task=manager.task_id
    assert await manager.start(config,'request-key-0001','subject')
    assert manager.last_start_result=={'task_id':task,'replayed':True}
    with pytest.raises(ValueError): await manager.start(config.model_copy(update={'keywords':'changed'}),'request-key-0001','subject')
    with pytest.raises(ValueError):await manager.stop('stale-task')
    assert manager.process.poll() is None
    assert not await manager.start(config,'request-key-0002','subject')
    await asyncio.wait_for(manager._read_task,10)
    other=CrawlerManager();other.recover()
    assert other.task_id==task
    assert await other.start(config,'request-key-0001','subject')
    assert other.process is None
    logs=store.task_logs(task)
    assert any('finished' in item['message'] for item in logs)
    assert all(row['id']>logs[0]['id'] for row in store.task_logs(task,logs[0]['id']))


@pytest.mark.asyncio
async def test_task_and_children_stopped_together(monkeypatch,tmp_path):
    if os.name=='nt':pytest.skip('POSIX process-group verification')
    marker=tmp_path/'child-ready'
    script=f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',\"from pathlib import Path;import time;Path({str(marker)!r}).write_text('ready');time.sleep(30)\"]);time.sleep(30)"
    manager=CrawlerManager();monkeypatch.setattr(manager,'_build_command',lambda _:[sys.executable,'-u','-c',script])
    assert await manager.start(CrawlerStartRequest(platform='xhs'))
    for _ in range(100):
        if marker.exists():break
        await asyncio.sleep(.03)
    assert marker.exists()
    assert not available(store.DB_PATH.parent/'execution.lock')
    assert await manager.stop(manager.task_id)
    for _ in range(100):
        if available(store.DB_PATH.parent/'execution.lock'):break
        await asyncio.sleep(.03)
    assert available(store.DB_PATH.parent/'execution.lock')
    assert store.get_document('task',manager.task_id)['outcome']=='cancelled'


def test_recovery_persists_interruption_and_blocks_orphan(monkeypatch):
    store.save_document('task',{'id':'old','outcome':'running','started_at':'2026-01-01T00:00:00Z'})
    with Lease(store.DB_PATH.parent/'execution.lock'):
        manager=CrawlerManager();manager.recover()
        assert manager.status=='error'
        assert store.get_document('task','old')['outcome']=='interrupted'
    assert available(store.DB_PATH.parent/'execution.lock')


def test_redaction_and_cookie_not_in_command():
    config=CrawlerStartRequest(platform='xhs',cookies='session=secret')
    assert 'secret' not in str(CrawlerManager()._build_command(config))
    value=redact('Cookie: a=1; b=2\nhttps://site.test?a=1&xsec_token=private\nAuthorization: Bearer credential\n{"token":"hidden-value"}')
    for secret in ['a=1; b=2','private','credential','hidden-value']:
        assert secret not in value


def test_online_backup_does_not_create_missing_source(tmp_path):
    from api.backup import backup_database
    source=tmp_path/'collection.db';dest=tmp_path/'backup.sqlite3'
    assert not backup_database(source,dest)
    assert not source.exists() and not dest.exists()
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE records(id INTEGER)')
        db.execute('INSERT INTO records VALUES (1)')
        db.commit()
        assert backup_database(source,dest)
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT * FROM records').fetchall()==[(1,)]
    with pytest.raises(ValueError):backup_database(source,dest)


def test_log_cursors_do_not_skip_large_gaps():
    with store.connect() as db:
        store.remote_schema(db)
        db.executemany('INSERT INTO task_logs(task_id,timestamp,level,message) VALUES (?,?,?,?)',[('task','2026-01-01T00:00:00Z','info',str(i)) for i in range(1001)])
    assert [r['id'] for r in store.task_logs('task',after=1)]==list(range(2,502))
    assert [r['id'] for r in store.task_logs('task',before=501)]==list(range(1,501))
    assert len(store.task_logs('task'))==500


@pytest.mark.asyncio
async def test_login_reporting_is_passive_and_scoped(monkeypatch,capsys):
    from tools.login_status import checked_login
    from unittest.mock import AsyncMock
    check=AsyncMock(return_value=True)
    monkeypatch.delenv('MEDIACRAWLER_TASK_ID',raising=False)
    assert await checked_login(check(),'xhs')
    assert not capsys.readouterr().out
    monkeypatch.setenv('MEDIACRAWLER_TASK_ID','fixture-task')
    assert await checked_login(check(),'xhs')
    event=capsys.readouterr().out.strip()
    assert event.startswith('MC_LOGIN_STATE ')
    manager=CrawlerManager();manager.task_id='fixture-task';manager.task={'id':'fixture-task','platform':'xhs','outcome':'running'}
    manager._create_log_entry(event)
    assert store.get_document('task','fixture-task')['login_state']=='authenticated'
    manager._create_log_entry('MC_LOGIN_STATE {"platform":"dy","state":"authenticated"}')
    assert manager.task['platform']=='xhs'
