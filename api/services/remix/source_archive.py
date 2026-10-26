"""Complete, immutable local source packages; model context is complete or rejected."""
import json

from api.services import analytics
from api.services.redfox_sources import expressions
from . import store

CONTEXT_CHARS = 120000
MAX_CONTENTS = 5000
MAX_COMMENTS = 50000
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024


def analytics_snapshot(options):
    where,args=analytics.filters(**options)
    with analytics.read_db() as (db,ready):
        db.execute('BEGIN')
        result=analytics.overview_in_snapshot(db,ready,where,args)
        summary=result['summary']
        if not ready or not summary['content_count']:
            raise ValueError('当前筛选没有可用于创作的数据，请先保存采集数据或调整筛选')
        if summary['content_count']>MAX_CONTENTS or summary['comment_count']>MAX_COMMENTS:
            raise ValueError('完整来源超过本机打包上限，请缩小筛选范围；未截取部分数据，也未调用模型')
        has_remote=db.execute("SELECT 1 FROM sqlite_master WHERE name='redfox_sources'").fetchone()
        remote="(SELECT r.body FROM redfox_sources r WHERE r.platform=c.platform AND r.content_id=c.content_id)" if has_remote else 'NULL'
        body=f"CASE WHEN c.platform='xhs' THEN {expressions(db)['body']} ELSE {remote} END"
        contents=analytics.rows(db,f'SELECT c.*,{body} body FROM analytics_content c WHERE {where} ORDER BY c.platform,c.content_id',args)
        comments=analytics.rows(db,f'''SELECT m.* FROM analytics_comment m JOIN analytics_content c
            ON c.platform=m.platform AND c.content_id=m.content_id WHERE {where}
            ORDER BY m.platform,m.content_id,m.comment_id''',args)
    package=dict(version=1,kind='analytics',filters=options,generated_at=result['generated_at'],
        analysis=result,contents=contents,comments=comments,
        complete=True,counts=dict(contents=len(contents),comments=len(comments)))
    if len(json.dumps(package,ensure_ascii=False).encode())>MAX_ARCHIVE_BYTES:
        raise ValueError('完整来源包超过20MB，请缩小筛选范围；未截取部分数据，也未调用模型')
    return package


def model_records(source):
    package=source.get('dataset_snapshot')
    if source.get('kind')!='analytics' or not package:
        return []
    result=[dict(id='A0001',kind='data_analysis',text=json.dumps(package['analysis'],ensure_ascii=False),archive_path=['dataset_snapshot','analysis'])]
    for i,item in enumerate(package['contents'],1):
        fields={k:item.get(k) for k in ('platform','content_id','title','author','body','published_at','collected_at','likes','favorites','comments','shares','url')}
        result.append(dict(id=f'D{i:04d}',kind='source_excerpt',origin_kind='body',factual_source=True,
            text=json.dumps(fields,ensure_ascii=False),archive_path=['dataset_snapshot','contents',i-1]))
    for i,item in enumerate(package['comments'],1):
        result.append(dict(id=f'C{i:04d}',kind='source_excerpt',origin_kind='comment',factual_source=False,
            text=item.get('body') or '（空评论）',source_content_id=item['content_id'],
            archive_path=['dataset_snapshot','comments',i-1]))
    return result


def archive(job):
    source=job.get('source',{})
    return dict(version=1,job_id=job['id'],title=job['title'],created_at=job['created_at'],
        source=source,evidence=job.get('evidence',[]),report=job.get('report'),
        provenance=source.get('provenance'),
        note='任务保存的完整来源；历史任务未冻结的原始记录无法从当前数据库伪造为历史快照。')


def records(job,kind='',page=1,record_id=''):
    source=job.get('source',{});package=source.get('dataset_snapshot')
    result=[]
    if package:
        result.append(dict(id='A0001',kind='analysis',label='完整数据分析结果',data=package['analysis']))
        result.extend(dict(id=f'D{i:04d}',kind='content',label=v.get('title') or v['content_id'],data=v) for i,v in enumerate(package['contents'],1))
        result.extend(dict(id=f'C{i:04d}',kind='comment',label='评论 '+str(v['comment_id']),data=v) for i,v in enumerate(package['comments'],1))
    if source.get('kind')=='analytics' and not package:
        try:legacy=json.loads(source.get('body',''))
        except ValueError:legacy=source.get('body','')
        result.append(dict(id='legacy-analysis',kind='analysis',label='历史任务保存的完整统计',data=legacy))
    bundle=source.get('reference_bundle')
    if bundle is None and source.get('kind') in ('report','research'):
        try:bundle=json.loads(source.get('body',''))
        except ValueError:bundle=None
    if bundle:
        result.append(dict(id='upstream-report',kind='analysis',label='完整上游分析与原始快照',data=bundle))
    by_id={e.get('id'):e for e in (bundle or {}).get('evidence',[])}
    for e in job.get('evidence',[]):
        if package and e['id'][:1] in ('A','D','C'):continue
        original=by_id.get(e.get('upstream_id'))
        result.append(dict(id=e['id'],kind='evidence',label='引用 '+e['id'],data=original or e))
    if not result:
        result.append(dict(id='original-source',kind='analysis',label='任务保存的来源',data=source))
    if record_id:result=[v for v in result if v['id']==record_id]
    if kind:result=[v for v in result if v['kind']==kind]
    return dict(items=result[(page-1)*20:page*20],total=len(result),page=page,
        dataset_complete=bool(package and package.get('complete')),counts=(package or {}).get('counts'),
        legacy_analytics=source.get('kind')=='analytics' and not package)
