import json
import sqlite3
from pathlib import Path
import pytest
from api.backup import backup_workspace,verify_workspace,restore_workspace
from api.services.lease import Lease


def database(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE example(id INTEGER)');db.execute('INSERT INTO example VALUES(42)')


def fixture(tmp_path):
    root=tmp_path/'source';runtime=root/'runtime';data=root/'data'
    for path in [root/'collection.db',runtime/'workbench.sqlite3',runtime/'content-remix'/'analysis.sqlite3',runtime/'content-remix'/'checkpoints.sqlite3']:database(path)
    for name in ['data/workbench/a.png','runtime/content-remix/production/abc/cover.png','runtime/content-remix/jobs/abc/transcript.json']:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
    for name in ['data/cookies/private.png','data/browser_data/private.png','runtime/.env','runtime/content-remix/models/weights.png']:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'private fixture')
    return root,runtime,data


def test_full_backup_restore_and_private_exclusions(tmp_path):
    root,runtime,data=fixture(tmp_path)
    backup=Path(backup_workspace(tmp_path/'backups',root/'collection.db',runtime/'workbench.sqlite3',data))
    assert verify_workspace(backup)['verified']
    manifest=json.loads((backup/'manifest.json').read_text());names=[e['path'] for e in manifest['files']]
    assert len([n for n in names if n.endswith('.sqlite3')])==4
    assert not any('cookies' in n or 'browser_data' in n or '.env' in n or 'models' in n for n in names)
    target=tmp_path/'restored';restore_workspace(backup,target)
    assert verify_workspace(target)['files']==len(names)
    with sqlite3.connect(target/'runtime/content-remix/analysis.sqlite3') as db:assert db.execute('SELECT id FROM example').fetchone()[0]==42
    with pytest.raises(ValueError,match='覆盖'):restore_workspace(backup,target)
    (backup/'data/workbench/a.png').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='摘要'):restore_workspace(backup,tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_backup_requires_idle_executor(tmp_path):
    root,runtime,data=fixture(tmp_path)
    with Lease(runtime/'content-remix/executor.lock'):
        with pytest.raises(ValueError,match='停止工作台'):backup_workspace(tmp_path/'backups',root/'collection.db',runtime/'workbench.sqlite3',data)


def test_manifest_path_traversal_and_symlink_are_rejected(tmp_path):
    root,runtime,data=fixture(tmp_path)
    backup=Path(backup_workspace(tmp_path/'backups',root/'collection.db',runtime/'workbench.sqlite3',data))
    manifest=backup/'manifest.json';body=json.loads(manifest.read_text());body['files'][0]['path']='../escape'
    manifest.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='路径'):verify_workspace(backup)


def test_backup_rejects_missing_referenced_media(tmp_path):
    root,runtime,data=fixture(tmp_path)
    with sqlite3.connect(runtime/'workbench.sqlite3') as db:
        db.execute('CREATE TABLE documents(kind TEXT,id TEXT,body TEXT)')
        db.execute('INSERT INTO documents VALUES(?,?,?)',('draft','draft-1',json.dumps({'images':['missing.png']})))
    with pytest.raises(ValueError,match='被引用'):backup_workspace(tmp_path/'backups',root/'collection.db',runtime/'workbench.sqlite3',data)
    assert not list((tmp_path/'backups').glob('workspace-*'))
