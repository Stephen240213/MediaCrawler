"""Online SQLite backup of collection and workbench DBs; never copies live WAL files."""
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
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--env-file',type=Path)
    args=parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():parser.error('配置文件不存在')
        load_dotenv(args.env_file)
    from config.db_config import sqlite_db_config
    from api.services.workbench_store import DB_PATH
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    count=0
    for name,path in [('collection',Path(sqlite_db_config['db_path'])),('workbench',DB_PATH)]:
        target=args.destination/f'{name}-{stamp}.sqlite3'
        if backup_database(path,target):
            count+=1;print(f'{name}: backup verified -> {target}')
        else:print(f'{name}: source not present; skipped')
    if not count:parser.exit(1,'没有可备份的数据库；未创建源数据库。\n')


if __name__=='__main__':main()
