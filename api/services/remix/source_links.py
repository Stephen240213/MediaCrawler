"""Resolve saved XHS navigation parameters locally, outside model evidence and exports."""
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from api.services import analytics


def _token(value):
    return (value if isinstance(value,str) and 0<len(value)<=4096
            and value.lower() not in ('none','null','undefined')
            and not any(c.isspace() or ord(c)<32 or ord(c)==127 for c in value) else '')


def note_url(content_id, candidates=(), token=''):
    canonical='https://www.xiaohongshu.com/explore/'+quote(content_id,safe='')
    for value in candidates:
        if not isinstance(value,str) or len(value)>8192 or any(ord(c)<32 for c in value):
            continue
        try:
            parsed=urlsplit(value)
            if (parsed.scheme!='https' or parsed.hostname not in ('www.xiaohongshu.com','xiaohongshu.com')
                    or parsed.username or parsed.password or parsed.port not in (None,443)):
                continue
            if unquote(parsed.path).rstrip('/') not in ('/explore/'+content_id,'/discovery/item/'+content_id):
                continue
            query=parse_qs(parsed.query.replace('+','%2B'),keep_blank_values=True,max_num_fields=50)
            tokens=query.get('xsec_token',[])
            signed=_token(tokens[0]) if len(tokens)==1 else ''
            if not signed:
                continue
            source=query.get('xsec_source',['pc_search'])
            origin=source[0] if len(source)==1 and _token(source[0]) and len(source[0])<=100 else 'pc_search'
            return canonical+'?'+urlencode(dict(xsec_token=signed,xsec_source=origin))
        except (ValueError,TypeError):
            continue
    signed=_token(token)
    return canonical+('?' + urlencode(dict(xsec_token=signed,xsec_source='pc_search')) if signed else '')


def original_url(job):
    source=job.get('source') or {}
    for _ in range(12):
        if source.get('kind')!='report':break
        source=(source.get('reference_bundle') or {}).get('source_data') or {}
    if source.get('kind')!='xhs':
        return None
    content_id=source.get('content_id') or job.get('input',{}).get('content_id')
    if not isinstance(content_id,str) or not content_id or len(content_id)>200:
        return None
    native={};note={}
    with analytics.read_db() as (db,_):
        row=db.execute("SELECT url FROM analytics_content WHERE platform='xhs' AND content_id=?",(content_id,)).fetchone()
        if row:note=dict(row)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='xhs_note' AND type='table'").fetchone():
            row=db.execute('SELECT * FROM xhs_note WHERE note_id=? LIMIT 1',(content_id,)).fetchone()
            if row:native=dict(row)
    # Read the latest collected link, including for historical reports. Do not
    # freeze navigation tokens in a report, evidence snapshot, or model cache.
    return note_url(content_id,(native.get('note_url'),note.get('url'),source.get('url')),native.get('xsec_token'))
