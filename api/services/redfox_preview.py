"""Readable, paginated MCP previews. No imports, provider calls or model calls."""
from .redfox_payload import payloads, check_result
from .redfox_ingest import records, mode, transform


def preview_export(envelope, platform, offset=0, limit=20):
    meta = dict(task_id=envelope.get('task_id',''), tool=envelope.get('tool',''), state='content', error=None)
    try:
        result = check_result(envelope.get('result',{}))
    except ValueError as exc:
        return dict(data=[],total=0,mcp={**meta,'state':'error','error':str(exc)})
    values=payloads(result);kind=mode(meta['tool'])
    if kind=='receipt':
        return dict(data=[],total=0,mcp={**meta,'state':'raw_only'})
    rows=records(values[0],kind=kind) if values else []
    output=[]
    for row in rows[offset:offset+limit]:
        try:
            item,body,_=transform(row,platform,kind,envelope.get('context') or {},envelope.get('collected_at'))
            output.append({**item,'body':body[:1500] if kind=='content' else item.get('body','')})
        except ValueError as exc:
            output.append({'preview_error':str(exc)})
    return dict(data=output,total=len(rows),mcp=meta)
