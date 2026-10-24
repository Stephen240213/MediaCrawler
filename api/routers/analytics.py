"""Local-only analytics: GET endpoints are strictly read-only."""
import csv
import io
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from api.services import analytics as service
from database.analytics import CONTENT_COLUMNS, migrate

router = APIRouter(prefix='/analytics', tags=['analytics'])


def selection(platform: str = '', keyword: str = Query('', max_length=500), start: str = '', end: str = '',
              basis: Literal['published_at', 'collected_at'] = 'published_at',
              content_id: str = '', author_id: str = '', content_type: str = ''):
    options = dict(platform=platform, keyword=keyword, start=start, end=end, basis=basis,
                   content_id=content_id, author_id=author_id, content_type=content_type)
    try:
        service.filters(**options)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return options


@router.get('/overview')
def overview(options=Depends(selection)):
    try:
        return service.overview(options)
    except sqlite3.Error as exc:
        raise HTTPException(503, '无法读取采集数据库，请检查数据库权限与迁移状态') from exc


@router.get('/details')
def details(options=Depends(selection), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
            kind: Literal['content', 'comment'] = 'content', sort: Literal['recent', 'interactions'] = 'recent', word: str = Query('', max_length=100), comment_date: str = ''):
    try:
        return service.details(options, page, page_size, kind, sort, word, comment_date)
    except ValueError as exc:
        raise HTTPException(422, '评论日期须为 YYYY-MM-DD') from exc


@router.get('/history')
def history(platform: str, content_id: str):
    return service.history(platform, content_id)


@router.post('/initialize')
def initialize():
    try:
        return migrate(service.db_path())
    except (sqlite3.Error, ValueError) as exc:
        raise HTTPException(400, '初始化失败：' + str(exc)) from exc


@router.post('/import')
async def import_data(request: Request, filename: str, platform: str = '',
                      kind: Literal['content', 'comment'] = 'content', dry_run: bool = True):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > service.MAX_BYTES:
            raise HTTPException(413, '文件不能超过 10 MB')
    try:
        # File parsing and SQLite work must not block the ASGI event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(service.import_file, filename, bytes(body), platform, kind, dry_run)
    except (ValueError, UnicodeError, KeyError, TypeError) as exc:
        raise HTTPException(400, '导入失败：' + str(exc)) from exc
    except Exception as exc:
        import zipfile
        if isinstance(exc, (zipfile.BadZipFile, sqlite3.Error)):
            raise HTTPException(400, '文件或数据库无法读取，请检查格式与初始化状态') from exc
        raise


def spreadsheet_safe(value):
    # Avoid spreadsheet formula injection while preserving numeric metrics.
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + value
    return value


@router.get('/export')
def export(options=Depends(selection), format: Literal['csv', 'xlsx'] = 'csv'):
    where, args = service.filters(**options)
    with service.read_db() as (db, _):
        db.execute('BEGIN')
        count = db.execute(f'SELECT count(*) FROM analytics_content c WHERE {where}', args).fetchone()[0]
        if count > service.MAX_ROWS:
            raise HTTPException(413, '单次最多导出 50000 条，请缩小筛选范围')
        cursor = db.execute(f"SELECT {','.join('c.'+c for c in CONTENT_COLUMNS)} FROM analytics_content c WHERE {where} ORDER BY c.platform,c.content_id", args)
        if format == 'csv':
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(CONTENT_COLUMNS)
            for row in cursor:
                writer.writerow([spreadsheet_safe(v) for v in row])
            body = output.getvalue().encode('utf-8-sig')
            media = 'text/csv; charset=utf-8'
        else:
            from openpyxl import Workbook
            wb = Workbook(write_only=True)
            ws = wb.create_sheet('筛选内容')
            ws.append(CONTENT_COLUMNS)
            for row in cursor:
                ws.append([spreadsheet_safe(v) for v in row])
            output = io.BytesIO()
            wb.save(output)
            body = output.getvalue()
            media = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return Response(body, media_type=media, headers={'Content-Disposition': f'attachment; filename="analytics.{format}"'})
