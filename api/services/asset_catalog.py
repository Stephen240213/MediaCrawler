"""Persistent media index. Directory scans occur only on bootstrap or explicit invalidation/refresh."""
import hashlib
import json
from pathlib import Path
import threading
from urllib.parse import quote
from datetime import datetime,timezone
from PIL import Image,ImageOps
from . import workbench_store as store

IMAGES={'.jpg','.jpeg','.png','.webp','.gif'}
VIDEOS={'.mp4','.webm','.mov','.m4v'}
_lock=threading.RLock()


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS asset_catalog(root TEXT,id TEXT,kind TEXT,updated_at REAL,body TEXT NOT NULL,PRIMARY KEY(root,id))')
    db.execute('CREATE INDEX IF NOT EXISTS asset_catalog_order ON asset_catalog(root,kind,updated_at)')
    db.execute('CREATE TABLE IF NOT EXISTS asset_catalog_state(root TEXT PRIMARY KEY,body TEXT NOT NULL)')


def state(root):
    with store.connect() as db:
        schema(db);row=db.execute('SELECT body FROM asset_catalog_state WHERE root=?',(str(root.resolve()),)).fetchone()
    return json.loads(row[0]) if row else None


def invalidate(root):
    with store.connect() as db:
        schema(db);db.execute('DELETE FROM asset_catalog_state WHERE root=?',(str(root.resolve()),))


def info(path,root,previous=None):
    root=root.resolve();path=Path(path)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):raise ValueError('素材路径无效')
    path=path.resolve();relative=path.relative_to(root).as_posix();stat=path.stat()
    if path.suffix.lower() not in IMAGES|VIDEOS:raise ValueError('素材类型无效')
    if previous and previous.get('mtime_ns')==stat.st_mtime_ns and previous['size']==stat.st_size:return previous
    with path.open('rb') as file:sha=hashlib.file_digest(file,'sha256').hexdigest()
    encoded=quote(relative,safe='/')
    value=dict(id=relative,name=path.name,kind='image' if path.suffix.lower() in IMAGES else 'video',size=stat.st_size,
        updated_at=stat.st_mtime,mtime_ns=stat.st_mtime_ns,sha256=sha,source='导入' if relative.startswith('workbench/') else '采集',url='/api/workbench/assets/file/'+encoded)
    if value['kind']=='image':
        try:
            with Image.open(path) as original:
                value.update(width=original.width,height=original.height)
                image=ImageOps.exif_transpose(original);image.thumbnail((480,480));image=image.convert('RGB')
                folder=store.DB_PATH.parent/'asset-thumbnails';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                target=folder/(sha+'.jpg')
                if not target.is_file():
                    temporary=folder/(sha+'.tmp');image.save(temporary,format='JPEG',quality=80);temporary.chmod(0o600);temporary.replace(target)
                value['thumbnail_url']='/api/workbench/assets/thumb/'+encoded
        except (OSError,ValueError,Image.DecompressionBombError):value['thumbnail_unavailable']=True
    return value


def upsert(path,root):
    with _lock:
        value=info(path,root)
        with store.connect() as db:
            schema(db);db.execute('INSERT OR REPLACE INTO asset_catalog VALUES(?,?,?,?,?)',(str(root.resolve()),value['id'],value['kind'],value['updated_at'],json.dumps(value,ensure_ascii=False)))
        return value


def refresh(root):
    with _lock:
        key=str(root.resolve())
        with store.connect() as db:
            schema(db);previous={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,body FROM asset_catalog WHERE root=?',(key,))}
        found=[]
        if root.exists():
            for path in root.rglob('*'):
                if len(found)>=50000:raise ValueError('素材超过50000个，请先归档目录后重新索引')
                if path.suffix.lower() not in IMAGES|VIDEOS or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):continue
                try:found.append(info(path,root,previous.get(path.relative_to(root).as_posix())))
                except (ValueError,OSError):continue
        stamp=dict(indexed_at=datetime.now(timezone.utc).isoformat(),count=len(found))
        with store.connect() as db:
            schema(db);db.execute('DELETE FROM asset_catalog WHERE root=?',(key,))
            db.executemany('INSERT INTO asset_catalog VALUES(?,?,?,?,?)',[(key,v['id'],v['kind'],v['updated_at'],json.dumps(v,ensure_ascii=False)) for v in found])
            db.execute('INSERT OR REPLACE INTO asset_catalog_state VALUES(?,?)',(key,json.dumps(stamp)))
        return stamp


def listing(root,archived=False,page=None,page_size=48,search='',kind='all'):
    stamp=state(root) or refresh(root)
    metadata={item['id']:item for item in store.list_documents('asset')}
    with store.connect() as db:
        schema(db);rows=db.execute('SELECT body FROM asset_catalog WHERE root=?'+(' AND kind=?' if kind!='all' else '')+' ORDER BY updated_at DESC,id',(str(root.resolve()),kind) if kind!='all' else (str(root.resolve()),)).fetchall()
    values=[]
    for row in rows:
        v=json.loads(row[0]);meta=metadata.get(v['id'],{});v.update(name=meta.get('name',v['name']),archived=meta.get('archived',False))
        if v['archived']==archived and search.casefold() in (v['name']+' '+v['id']).casefold():values.append(v)
    return dict(assets=values if page is None else values[(page-1)*page_size:page*page_size],total=len(values),page=page,page_size=page_size,**stamp)


def thumbnail(root,relative):
    path=root/relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('素材不存在')
    with store.connect() as db:
        schema(db);row=db.execute('SELECT body FROM asset_catalog WHERE root=? AND id=?',(str(root.resolve()),relative)).fetchone()
    value=json.loads(row[0]) if row else upsert(path,root)
    if value['kind']!='image':raise ValueError('视频没有图片缩略图')
    if value.get('mtime_ns')!=path.stat().st_mtime_ns:value=upsert(path,root)
    target=store.DB_PATH.parent/'asset-thumbnails'/(value['sha256']+'.jpg')
    if not target.is_file() or target.is_symlink():raise ValueError('缩略图不可用，请刷新索引')
    return target
