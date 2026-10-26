"""Draft editing and local media library for the workbench."""
import base64
import binascii
import io
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from PIL import Image, UnidentifiedImageError

from ..services import workbench_store as store
from ..services import asset_catalog

router = APIRouter(prefix='/workbench', tags=['workbench'])
DATA_ROOT = store.ROOT / 'data'
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
VIDEO_SUFFIXES = {'.mp4', '.webm', '.mov', '.m4v'}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def media_path(relative: str) -> Path:
    root = DATA_ROOT.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
        raise HTTPException(404, '素材不存在')
    return path


def asset_info(path):
    relative = path.relative_to(DATA_ROOT.resolve()).as_posix()
    stat = path.stat()
    return {'id': relative, 'name': path.name, 'kind': 'image' if path.suffix.lower() in IMAGE_SUFFIXES else 'video',
            'size': stat.st_size, 'updated_at': stat.st_mtime, 'source': '导入' if relative.startswith('workbench/') else '采集',
            'url': '/api/workbench/assets/file/' + relative}


class AssetUpload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(max_length=14_000_000)


@router.get('/assets')
def list_assets(archived: bool = False,page: int|None=Query(None,ge=1),page_size: int=Query(48,ge=1,le=100),search: str=Query('',max_length=200),kind: str=Query('all',pattern='^(all|image|video)$')):
    return asset_catalog.listing(DATA_ROOT,archived,page,page_size,search,kind)


@router.post('/assets/reindex')
def reindex_assets():
    try:return asset_catalog.refresh(DATA_ROOT)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/assets/thumb/{relative:path}')
def asset_thumbnail(relative: str):
    try:path=asset_catalog.thumbnail(DATA_ROOT,relative)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc
    return FileResponse(path,media_type='image/jpeg',headers={'X-Content-Type-Options':'nosniff'})


@router.post('/assets', status_code=201)
def upload_asset(payload: AssetUpload):
    try:
        data = base64.b64decode(payload.data, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, '图片编码无效')
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(400, '请选择不超过 10 MB 的图片')
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, '图片损坏或格式不受支持')
    suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif'}.get(fmt)
    if not suffix:
        raise HTTPException(400, '支持 JPG、PNG、WebP、GIF 图片')
    folder = DATA_ROOT / 'workbench' / 'assets'
    if not folder.resolve().is_relative_to(DATA_ROOT.resolve()):
        raise HTTPException(400, '素材目录无效')
    folder.mkdir(parents=True, exist_ok=True)
    # A server-generated name prevents client paths from controlling file writes.
    path = folder / (uuid4().hex + suffix)
    path.write_bytes(data)
    item = asset_info(path.resolve())
    item['name'] = Path(payload.name).name
    store.save_document('asset', {'id': item['id'], 'name': item['name']})
    asset_catalog.upsert(path,DATA_ROOT)
    return item


@router.get('/assets/file/{relative:path}')
def read_asset(relative: str):
    path = media_path(relative)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0], headers={'X-Content-Type-Options': 'nosniff'})


class AssetState(BaseModel):
    archived: bool


@router.patch('/assets/state/{relative:path}')
def change_asset_state(relative: str, payload: AssetState):
    path = media_path(relative)
    item = asset_info(path)
    metadata = store.get_document('asset', item['id']) or {'id': item['id'], 'name': item['name']}
    metadata['archived'] = payload.archived
    store.save_document('asset', metadata)
    return metadata


class DraftInput(BaseModel):
    title: str = Field(default='', max_length=200)
    content: str = Field(default='', max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    images: list[str] = Field(default_factory=list, max_length=18)
    platform: str = Field(default='xhs', max_length=30)
    version: int = Field(default=0, ge=0)


@router.get('/drafts')
def list_drafts():
    return {'drafts': sorted(store.list_documents('draft'), key=lambda d: d['updated_at'], reverse=True)}


@router.put('/drafts/{draft_id}')
def save_draft(draft_id: str, payload: DraftInput):
    if len(draft_id) > 64 or not draft_id.replace('-', '').isalnum():
        raise HTTPException(400, '草稿编号无效')
    for relative in payload.images:
        if media_path(relative).suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(400, '草稿预览仅支持图片素材')
    # Check and write within one transaction so multiple tabs cannot overwrite changes.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM documents WHERE kind=? AND id=?', ('draft', draft_id)).fetchone()
        previous = json.loads(row[0]) if row else None
        if payload.version != (previous['version'] if previous else 0):
            raise HTTPException(409, '草稿已在其他页面更新，请重新打开后再编辑；当前修改仍保留在编辑器中')
        document = {**payload.model_dump(), 'id': draft_id, 'version': payload.version + 1,
                    'updated_at': datetime.now(timezone.utc).isoformat()}
        db.execute('INSERT OR REPLACE INTO documents VALUES (?,?,?)', ('draft', draft_id, json.dumps(document, ensure_ascii=False)))
    return document


@router.get('/tasks')
def list_tasks():
    from ..services import crawler_manager
    documents = store.list_documents('task')
    active_id = crawler_manager.task_id
    for item in documents:
        if item['outcome'] == 'running' and item['id'] != active_id:
            item['outcome'] = 'interrupted'
    return {'tasks': sorted(documents, key=lambda t: t['started_at'], reverse=True)[:100]}


@router.delete('/tasks/{task_id}')
def delete_task(task_id: str):
    from ..services import crawler_manager
    if crawler_manager.task_id == task_id and crawler_manager.process and crawler_manager.process.poll() is None:
        raise HTTPException(409, '运行中的任务不能删除，请先停止任务')
    try:
        deleted = store.delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, '任务记录不存在')
    return {'deleted': True}
