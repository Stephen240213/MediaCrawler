"""Online SQLite backups and verified offline workspace backup/restore."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def backup_database(source, destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_file():return False
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise ValueError('备份目标已存在，不覆盖已有备份')
    # Exclusive create first, before SQLite opens it.
    destination.touch(exist_ok=False,mode=0o600)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=30) as db, sqlite3.connect(destination) as target:
            db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('备份完整性检查失败')
        return True
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--full',action='store_true',help='Offline complete workspace backup; stop the workbench first')
    parser.add_argument('--verify',type=Path,help='Verify an existing complete backup')
    parser.add_argument('--restore',type=Path,help='Restore a verified complete backup into the empty destination')
    args=parser.parse_args()
    if args.verify:
        print(verify_workspace(args.verify));return
    if not args.destination:parser.error('--destination is required for backup or restore')
    if args.restore:
        restore_workspace(args.restore,args.destination);print('Restore verified. Private environment/configuration must be supplied separately.');return
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():parser.error('配置文件不存在')
        load_dotenv(args.env_file)
    from config.db_config import sqlite_db_config
    from api.services.workbench_store import DB_PATH
    if args.full:
        from api.routers.workbench import DATA_ROOT
        print(backup_workspace(args.destination,Path(sqlite_db_config['db_path']),DB_PATH,DATA_ROOT));return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    count=0
    for name,path in [('collection',Path(sqlite_db_config['db_path'])),('workbench',DB_PATH)]:
        target=args.destination/f'{name}-{stamp}.sqlite3'
        if backup_database(path,target):
            count+=1;print(f'{name}: backup verified -> {target}')
        else:print(f'{name}: source not present; skipped')
    if not count:parser.exit(1,'没有可备份的数据库；未创建源数据库。\n')





def _sha(path):
    import hashlib
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def _safe_file(root,relative):
    from pathlib import PurePosixPath
    p=PurePosixPath(relative)
    if not relative or p.is_absolute() or '..' in p.parts or '\\' in relative:raise ValueError('备份清单包含无效路径')
    target=root.joinpath(*p.parts)
    if any(parent.is_symlink() for parent in [target,*target.parents] if parent==root or parent.is_relative_to(root)):
        raise ValueError('备份不接受符号链接')
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():raise ValueError('备份文件不存在或越界')
    return target


def verify_workspace(directory):
    import json
    directory=Path(directory).resolve()
    manifest_path=_safe_file(directory,'manifest.json')
    if manifest_path.stat().st_size>20*1024*1024:raise ValueError('备份清单过大')
    manifest=json.loads(manifest_path.read_text())
    if manifest.get('format')!='mediacrawler-workspace-v1' or not isinstance(manifest.get('files'),list):raise ValueError('备份格式无效')
    seen=set()
    for entry in manifest['files']:
        name=entry['path']
        if name in seen:raise ValueError('备份清单路径重复')
        seen.add(name);path=_safe_file(directory,name)
        if path.stat().st_size!=entry['size'] or _sha(path)!=entry['sha256']:raise ValueError('备份摘要不匹配：'+name)
        if name.endswith('.sqlite3'):
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('备份数据库损坏')
    if not any(e['path'].endswith('.sqlite3') for e in manifest['files']):raise ValueError('备份没有数据库')
    _verify_references(directory,manifest)
    return dict(verified=True,files=len(manifest['files']),created_at=manifest['created_at'])


def _verify_references(directory,manifest):
    import json
    entries={e['path']:e for e in manifest['files']}
    def require(name,sha=None,size=None):
        if name not in entries:raise ValueError('备份缺少被引用的素材或制作文件：'+name)
        if sha and entries[name]['sha256']!=sha:raise ValueError('制作或上传文件与数据库摘要不一致：'+name)
        if size is not None and entries[name]['size']!=size:raise ValueError('制作文件大小与数据库不一致')
    workbench=directory/'runtime/workbench.sqlite3'
    if workbench.is_file():
        with sqlite3.connect(workbench.as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='documents'").fetchone():
                for (raw,) in db.execute("SELECT body FROM documents WHERE kind='draft'"):
                    for image in json.loads(raw).get('images',[]):require('data/'+image)
    analysis=directory/'runtime/content-remix/analysis.sqlite3'
    if analysis.is_file():
        with sqlite3.connect(analysis.as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='production_revisions'").fetchone():
                for (raw,) in db.execute('SELECT body FROM production_revisions'):
                    revision=json.loads(raw)
                    for artifact in revision['artifacts']:
                        require('runtime/content-remix/production/'+revision['id']+'/'+artifact['name'],artifact['sha256'],artifact['size'])
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='uploads'").fetchone():
                for upload_id,filename,sha in db.execute('SELECT id,filename,sha256 FROM uploads WHERE sha256 IS NOT NULL'):
                    require('runtime/content-remix/uploads/'+upload_id+'/source'+Path(filename).suffix.lower(),sha)


def backup_workspace(destination,collection,workbench,data_root):
    """Complete offline snapshot; never includes credentials, browser profiles or models."""
    from contextlib import ExitStack
    from uuid import uuid4
    import json,shutil,os
    from api.services.lease import Lease
    destination=Path(destination).resolve();runtime=Path(workbench).resolve().parent;data_root=Path(data_root).resolve()
    # Avoid backing up into one of the folders being copied.
    if destination.is_relative_to(data_root) or destination.is_relative_to(runtime/'content-remix'/'production'):
        raise ValueError('备份目标不能位于原素材或制作目录内')
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    temporary=destination/('.building-'+uuid4().hex);target=destination/('workspace-'+stamp)
    with ExitStack() as stack:
        try:
            stack.enter_context(Lease(runtime/'content-remix'/'executor.lock'))
            stack.enter_context(Lease(runtime/'execution.lock'))
        except RuntimeError:raise ValueError('完整备份需要先停止工作台和采集任务；原有单库在线备份仍可单独使用') from None
        temporary.mkdir(mode=0o700)
        try:
            databases=[(Path(collection),'collection.sqlite3'),(Path(workbench),'runtime/workbench.sqlite3'),
                (runtime/'content-remix'/'analysis.sqlite3','runtime/content-remix/analysis.sqlite3'),
                (runtime/'content-remix'/'checkpoints.sqlite3','runtime/content-remix/checkpoints.sqlite3')]
            for source,name in databases:
                if source.is_symlink():raise ValueError('数据库不能为符号链接')
                backup_database(source,temporary/name)
            allowed={'.jpg','.jpeg','.png','.webp','.gif','.mp4','.mov','.webm','.m4v','.wav','.mp3','.m4a','.json','.srt','.vtt','.txt'}
            trees=[(data_root,'data',allowed-{'.json','.txt'}),
                *((runtime/'content-remix'/part,'runtime/content-remix/'+part,allowed) for part in ('uploads','jobs','production')),
                (runtime/'asset-thumbnails','runtime/asset-thumbnails',{'.jpg'})]
            excluded={'browser_data','browser_profiles','profiles','cookies','models','.git','.aws','.codex','backups'}
            for root,prefix,suffixes in trees:
                if not root.exists():continue
                if root.is_symlink():raise ValueError('工作目录不能为符号链接')
                for source in root.rglob('*'):
                    relative=source.relative_to(root)
                    if any(p in excluded or p.startswith('.') for p in relative.parts):continue
                    if source.is_symlink() or not source.is_file() or source.suffix.lower() not in suffixes:continue
                    if not source.resolve().is_relative_to(root.resolve()):raise ValueError('备份源越界')
                    dst=temporary/prefix/relative;dst.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    shutil.copyfile(source,dst);dst.chmod(0o600)
            entries=[dict(path=p.relative_to(temporary).as_posix(),size=p.stat().st_size,sha256=_sha(p)) for p in sorted(temporary.rglob('*')) if p.is_file()]
            manifest=dict(format='mediacrawler-workspace-v1',created_at=datetime.now(timezone.utc).isoformat(),files=entries,
                excludes=['credentials','environment configuration','browser profiles','cookies','models'],
                restore_layout='collection.sqlite3 + runtime/ + data/; supply private configuration separately')
            file=temporary/'manifest.json';file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2));file.chmod(0o600)
            verify_workspace(temporary);temporary.rename(target)
        except Exception:
            shutil.rmtree(temporary,ignore_errors=True);raise
    return str(target)


def restore_workspace(source,destination):
    import json,shutil
    source=Path(source).resolve();destination=Path(destination).absolute()
    if destination.is_symlink() or destination.exists():raise ValueError('恢复目标必须是尚不存在的新目录，不覆盖现有工作区')
    verify_workspace(source)
    manifest=json.loads((source/'manifest.json').read_text())
    destination.mkdir(parents=True,mode=0o700)
    try:
        for entry in manifest['files']:
            original=_safe_file(source,entry['path']);target=destination/entry['path'];target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copyfile(original,target);target.chmod(0o600)
        shutil.copyfile(source/'manifest.json',destination/'manifest.json');(destination/'manifest.json').chmod(0o600)
        verify_workspace(destination)
    except Exception:
        shutil.rmtree(destination);raise
    return str(destination)


if __name__=='__main__':main()
