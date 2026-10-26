"""Local saved-source research; frozen batches, shared semantic caches and revisions.

The existing single executor consumes these runs. Reading a scope never performs
collection or a model call. Statistics never enter the neutral material cache.
"""
import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
from . import store, providers, research_provider as model
from .research_schemas import CATEGORIES
from api.services import analytics

MAX_MEMBERS=200
MAX_COMMENTS=100


def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value): return hashlib.sha256(encode(value).encode()).hexdigest()


def signature():
    c=providers.config()
    return dict(model=c['model'],endpoint=c['base_url'],policy=model.VERSION)


def cache_key(kind, value, config): return 'research:'+digest([kind,value,config])


def cached(key):
    with store.connect() as db: row=db.execute('SELECT body FROM model_cache WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else None


def cached_many(keys):
    keys=list(set(keys));result={}
    with store.connect() as db:
        for offset in range(0,len(keys),400):
            batch=keys[offset:offset+400]
            for row in db.execute('SELECT key,body FROM model_cache WHERE key IN ('+','.join('?' for _ in batch)+')',batch):
                result[row['key']]=json.loads(row['body'])
    return result


def put_cache(key,value):
    with store.connect() as db: db.execute('INSERT OR REPLACE INTO model_cache VALUES(?,?)',(key,encode(value)))


def put_caches(values):
    with store.connect() as db: db.executemany('INSERT OR REPLACE INTO model_cache VALUES(?,?)',[(k,encode(v)) for k,v in values])


def get(space_id):
    with store.connect() as db: row=db.execute('SELECT body FROM research_spaces WHERE id=?',(space_id,)).fetchone()
    return json.loads(row[0]) if row else None


def listing():
    with store.connect() as db: rows=db.execute('SELECT body FROM research_spaces ORDER BY rowid DESC LIMIT 500').fetchall()
    return [json.loads(row[0]) for row in rows]


def save(payload,actor,space_id=None):
    data=payload.model_dump();data['name']=data['name'].strip()
    if data['kind']=='account':
        with analytics.read_db() as (source_db,_):
            cols={row[1] for row in source_db.execute('PRAGMA table_info(xhs_note)')}
            source_db.create_function('research_identity',2,_account_id,deterministic=True)
            creator='n.creator_hash' if 'creator_hash' in cols else 'NULL'
            user='n.user_id' if 'user_id' in cols else 'NULL'
            valid='note_id' in cols and source_db.execute("SELECT 1 FROM xhs_note n JOIN analytics_content c ON c.platform='xhs' AND c.content_id=n.note_id WHERE research_identity("+creator+','+user+')=? LIMIT 1',(data['account_id'],)).fetchone()
        if not valid: raise ValueError('发布者缺少可核验的稳定ID，不能创建账号研究')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if space_id:
            row=db.execute('SELECT body FROM research_spaces WHERE id=?',(space_id,)).fetchone()
            if not row: raise ValueError('研究范围不存在')
            old=json.loads(row[0])
            if old['version']!=data['version']: raise ValueError('研究规则已更新，请重新读取后核对')
            if old['kind']!=data['kind']: raise ValueError('研究维度不可修改，请新建研究')
            if db.execute("SELECT 1 FROM research_runs WHERE space_id=? AND status IN ('queued','running')",(space_id,)).fetchone():
                raise ValueError('本范围有执行中的批次，请完成后修改规则')
        else:
            if data['version']!=0: raise ValueError('新研究版本应为0')
            if db.execute('SELECT count(*) FROM research_spaces').fetchone()[0]>=500: raise ValueError('研究范围最多500个')
            space_id=uuid4().hex
        data.update(id=space_id,actor=actor,version=data['version']+1,updated_at=store.now())
        db.execute('INSERT OR REPLACE INTO research_spaces VALUES(?,?)',(space_id,encode(data)))
    return data


def _account_id(creator,user):
    creator=str(creator or '').strip().lower();user=str(user or '').strip()
    if re.fullmatch(r'(?:[a-f0-9]{16}|[a-f0-9]{64})',creator): return digest(['xhs','creator_hash',creator])
    return digest(['xhs',user]) if user.lower() not in ('','none','null','undefined','0') else ''


def _catalog(db,space=None,search=''):
    cols={r[1] for r in db.execute('PRAGMA table_info(xhs_note)')}
    wanted=[k for k in ('note_id','desc','creator_hash','user_id') if k in cols]
    has_keywords=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='source_keyword_memberships'").fetchone())
    where=["c.platform='xhs'"];args=[]
    body_expr="coalesce((SELECT n.desc FROM xhs_note n WHERE n.note_id=c.content_id LIMIT 1),'')" if {'note_id','desc'}<=cols else "''"
    text_expr="coalesce(c.title,'')||char(10)||"+body_expr
    if search:
        where.append("(instr(coalesce(c.title,''),?)>0 OR instr(coalesce(c.author,''),?)>0 OR c.content_id=?)");args.extend([search]*3)
    if space:
        if space['kind']=='single': where.append('c.content_id=?');args.append(space['content_id'])
        if space['kind']=='account':
            if 'note_id' in cols:
                creator='n.creator_hash' if 'creator_hash' in cols else 'NULL'
                user='n.user_id' if 'user_id' in cols else 'NULL'
                db.create_function('research_identity',2,_account_id,deterministic=True)
                where.append('EXISTS(SELECT 1 FROM xhs_note n WHERE n.note_id=c.content_id AND research_identity('+creator+','+user+')=?)');args.append(space['account_id'])
            else: where.append('0')
        for key,operator in (('start','>='),('end','<=')):
            if space[key]: where.append("date(c.published_at,'+8 hours')"+operator+'?');args.append(space[key])
        if space['exclude_ids']:
            where.append('c.content_id NOT IN ('+','.join('?' for _ in space['exclude_ids'])+')');args.extend(space['exclude_ids'])
        for word in space['excludes']:
            where.append('instr('+text_expr+',?)=0');args.append(word)
        rules=[];rule_args=[]
        if space['kind']=='topic' and space['keywords']:
            marks=','.join('?' for _ in space['keywords'])
            keyword='c.keyword IN ('+marks+')';rule_args.extend(space['keywords'])
            if has_keywords:
                keyword+=' OR EXISTS(SELECT 1 FROM source_keyword_memberships k WHERE k.platform=c.platform AND k.content_id=c.content_id AND k.keyword IN ('+marks+'))';rule_args.extend(space['keywords'])
            rules.append('('+keyword+')')
        if space['contains']:
            rules.append('('+' OR '.join('instr('+text_expr+',?)>0' for _ in space['contains'])+')');rule_args.extend(space['contains'])
        if rules:
            rule=' AND '.join(rules)
            if space['include_ids']:
                rule='('+rule+') OR c.content_id IN ('+','.join('?' for _ in space['include_ids'])+')';rule_args.extend(space['include_ids'])
            where.append('('+rule+')');args.extend(rule_args)
        elif space['kind']=='topic' and space['include_ids']:
            where.append('c.content_id IN ('+','.join('?' for _ in space['include_ids'])+')');args.extend(space['include_ids'])
    rows=db.execute('SELECT c.* FROM analytics_content c WHERE '+' AND '.join(where)+' ORDER BY c.content_id LIMIT 5001',args).fetchall()
    if space and len(rows)>5000: raise ValueError('候选超过5000条，请通过来源关键词、包含词或时间缩小研究范围；未扫描全部正文，也未调用模型')
    rows=rows[:5000];native={};memberships={}
    ids=[r['content_id'] for r in rows]
    for offset in range(0,len(ids),400):
        chunk=ids[offset:offset+400];marks=','.join('?' for _ in chunk)
        if 'note_id' in wanted:
            native.update({str(r['note_id']):dict(r) for r in db.execute('SELECT '+','.join('"'+k+'"' for k in wanted)+' FROM xhs_note WHERE note_id IN ('+marks+') ORDER BY rowid',chunk)})
        if has_keywords:
            for row in db.execute("SELECT content_id,keyword FROM source_keyword_memberships WHERE platform='xhs' AND content_id IN ("+marks+')',chunk):
                memberships.setdefault(row['content_id'],set()).add(row['keyword'])
    result=[]
    for row in rows:
        item=dict(row);raw=native.get(item['content_id'],{})
        user=str(raw.get('user_id') or '').strip()
        creator=str(raw.get('creator_hash') or '').strip().lower()
        # analytics.author_id can be a nickname hash: never use it as identity.
        if re.fullmatch(r'(?:[a-f0-9]{16}|[a-f0-9]{64})',creator):
            item.update(account_id=digest(['xhs','creator_hash',creator]),account_basis='匿名发布者稳定标识')
        else:
            item.update(account_id=digest(['xhs',user]) if user.lower() not in ('','none','null','undefined','0') else '',account_basis='历史资料发布者ID' if user else '未归属')
        body=str(raw.get('desc') or '')
        item.update(body=body[:12000],full_body=body,body_chars=len(body),body_available=bool(body),
                    keywords=sorted(memberships.get(item['content_id'],set())|({item['keyword']} if item.get('keyword') else set())))
        result.append(item)
    return result


def discovery(search=''):
    with analytics.read_db() as (db,_):
        db.execute('BEGIN');items=_catalog(db,search=search)
        total=db.execute("SELECT count(*) FROM analytics_content WHERE platform='xhs'").fetchone()[0]
    accounts={}
    for item in items:
        if item['account_id']:
            a=accounts.setdefault(item['account_id'],dict(id=item['account_id'],name=item.get('author') or '未命名发布者',platform='xhs',basis=item['account_basis'],count=0))
            a['count']+=1
    return dict(accounts=list(accounts.values()),unknown_accounts=sum(not s['account_id'] for s in items),
                keywords=sorted({k for s in items for k in s['keywords']}),total=total,scanned=len(items),selector_limited=len(items)>=5000,
                sources=[dict(content_id=s['content_id'],title=s['title'] or s['content_id'],account_id=s['account_id']) for s in items])


def _matches(space,s):
    cid=s['content_id']
    if cid in space['exclude_ids']: return False
    if space['kind']=='single' and cid!=space['content_id']: return False
    if space['kind']=='account' and s['account_id']!=space['account_id']: return False
    if space['start'] or space['end']:
        try: date=datetime.fromisoformat(s.get('published_at') or '').astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        except ValueError: date=''
        if not date or (space['start'] and date<space['start']) or (space['end'] and date>space['end']): return False
    text=(s.get('title') or '')+'\n'+s['full_body']
    if any(w in text for w in space['excludes']): return False
    if cid in space['include_ids']: return True
    if space['kind']=='topic' and space['keywords'] and not set(space['keywords'])&set(s['keywords']): return False
    if space['contains'] and not any(w in text for w in space['contains']): return False
    return True


def _snapshot(space):
    with analytics.read_db() as (db,_):
        db.execute('BEGIN');items=[s for s in _catalog(db,space) if _matches(space,s)]
        if len(items)>MAX_MEMBERS: raise ValueError('单个研究范围最多200条，请用关键词、时间或排除规则缩小范围')
        for s in items:
            s.pop('full_body',None)
            cid=s['content_id'];s['source_key']='xhs:'+cid
            s['version_id']=digest([s['source_key'],s.get('title'),s['body']])
            prefix='s:'+s['version_id']
            s['evidence']=[dict(id=prefix+':title',text=s.get('title') or cid,kind='title')]
            if s['body']: s['evidence'].append(dict(id=prefix+':body',text=s['body'],kind='body'))
            s['saved_comments']=db.execute("SELECT count(*) FROM analytics_comment WHERE platform='xhs' AND content_id=?",(cid,)).fetchone()[0]
            s['comment_evidence']=[]
            for row in db.execute("SELECT comment_id,body FROM analytics_comment WHERE platform='xhs' AND content_id=? ORDER BY comment_id LIMIT ?",(cid,MAX_COMMENTS)):
                body=str(row['body'] or '')
                s['comment_evidence'].append(dict(id='c:'+digest([cid,row['comment_id'],body]),text=body[:500],kind='comment',truncated=len(body)>500))
            s['comments_hash']=digest(s['comment_evidence'])
            s['metrics']={k:s.get(k) for k in ('likes','favorites','comments','shares')}
    return items


def revisions(space_id):
    """Only the latest full snapshot; history lists read lightweight headers."""
    with store.connect() as db: rows=db.execute('SELECT body FROM research_revisions WHERE space_id=? ORDER BY rowid DESC LIMIT 1',(space_id,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def revision_headers(space_id):
    with store.connect() as db:
        rows=db.execute("""SELECT id,json_extract(body,'$.created_at') created_at,
            json_extract(body,'$.status') status,json_extract(body,'$.stats') stats
            FROM research_revisions WHERE space_id=? ORDER BY rowid DESC LIMIT 100""",(space_id,)).fetchall()
    return [dict(r,stats=json.loads(r['stats'])) for r in rows]


def revision(revision_id):
    with store.connect() as db: row=db.execute('SELECT body FROM research_revisions WHERE id=?',(revision_id,)).fetchone()
    return json.loads(row[0]) if row else None


def plan(space_id,full=False):
    space=get(space_id)
    if not space: raise ValueError('研究范围不存在')
    sources=_snapshot(space);config=signature();history=revisions(space_id)
    previous=history[0] if history else None
    old={s['source_key']:s for s in (previous or {}).get('members',[])}
    keys=[]
    for s in sources:
        keys.append(cache_key('material',s['version_id'],config))
        keys.extend(cache_key('comment',e['id'],config) for e in s['comment_evidence'])
    hits=cached_many(keys);counts=Counter();items=[]
    for s in sources:
        s['material_key']=cache_key('material',s['version_id'],config)
        s['pending_comments']=[e for e in s['comment_evidence'] if cache_key('comment',e['id'],config) not in hits]
        s['material_pending']=s['material_key'] not in hits
        before=old.get(s['source_key'])
        reason='new' if not before else 'body_changed' if before['version_id']!=s['version_id'] else 'comments_changed' if before['comments_hash']!=s['comments_hash'] else 'metrics_changed' if before['metrics']!=s['metrics'] else 'reused'
        counts[reason]+=1;counts['material_pending']+=int(s['material_pending']);counts['material_reused']+=int(not s['material_pending'])
        counts['comment_pending']+=len(s['pending_comments'])
        items.append(dict(content_id=s['content_id'],title=s['title'],reason=reason,material_pending=s['material_pending'],comment_pending=len(s['pending_comments']),saved_comments=s['saved_comments'],sample_comments=len(s['comment_evidence']),body_available=s['body_available'],body_truncated=s['body_chars']>12000))
    removed=sorted(set(old)-{s['source_key'] for s in sources});counts['removed']=len(removed)
    # Fixed hash shards keep insertions from re-paging every category. Each
    # occupied source shard can contribute to at most six category syntheses.
    upper=counts['material_pending']+sum(bool(s['pending_comments']) for s in sources)+len({digest(s['source_key'])[0] for s in sources})*len(CATEGORIES)
    same_inputs=previous and previous['config']=={k:v for k,v in config.items() if k!='endpoint'} and len(old)==len(sources) and all(
        (before:=old.get(s['source_key'])) and all(before.get(k)==s.get(k) for k in ('version_id','comments_hash','account_id','published_at')) for s in sources)
    if same_inputs and not counts['material_pending'] and not counts['comment_pending'] and previous['status']=='completed': upper=0
    token=digest([space,config,sources,(previous or {}).get('id')])
    value=dict(space=space,plan_token=token,counts=dict(counts),items=items,removed=removed,total=len(sources),
               max_calls=upper,config={k:v for k,v in config.items() if k!='endpoint'},
               scope='仅本机已保存的小红书正文与每条最多100条评论（每条最多500字）。不采集、不转写、不分析画面；缺失正文仅分析标题。',
               cost='按实际模型调用计费，最多处理所设调用上限；无变化分组复用，互动变化只刷新统计。超限和失败项留待下一轮。')
    if full: value.update(sources=sources,provider=config)
    return value


def run_get(run_id):
    with store.connect() as db: row=db.execute('SELECT body FROM research_runs WHERE id=?',(run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def update_run(run_id,**values):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT body FROM research_runs WHERE id=?',(run_id,)).fetchone()
        run=json.loads(row[0]);run.update(values,updated_at=store.now())
        db.execute('UPDATE research_runs SET status=?,body=? WHERE id=?',(run['status'],encode(run),run_id))
    return run


def submit(space_id,payload,actor):
    fingerprint=digest([space_id,payload.model_dump(exclude={'request_key'})])
    with store.connect() as db: old=db.execute('SELECT fingerprint,body FROM research_runs WHERE actor=? AND request_key=?',(actor,payload.request_key)).fetchone()
    if old:
        if old['fingerprint']!=fingerprint: raise ValueError('重复请求键对应不同参数')
        return public_run(json.loads(old['body']))
    if not payload.consent: raise ValueError('请确认本批正文、评论和结构化结果将发送到已配置模型，可能产生调用费用')
    p=plan(space_id,full=True)
    if p['space']['archived']: raise ValueError('研究范围已归档')
    if p['plan_token']!=payload.plan_token: raise ValueError('资料或规则已变化，请重新预览本批范围后提交')
    if not providers.status()['model_ready']: raise ValueError('分析模型尚未配置')
    run=dict(id=uuid4().hex,space_id=space_id,actor=actor,status='queued',created_at=store.now(),updated_at=store.now(),
             plan={k:v for k,v in p.items() if k not in ('sources','provider')},max_calls=payload.max_calls,calls=[],items=[],revision_id=None,error=None)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        replay=db.execute('SELECT fingerprint,body FROM research_runs WHERE actor=? AND request_key=?',(actor,payload.request_key)).fetchone()
        if replay:
            if replay['fingerprint']!=fingerprint: raise ValueError('重复请求键对应不同参数')
            return public_run(json.loads(replay['body']))
        if db.execute("SELECT 1 FROM research_runs WHERE space_id=? AND status IN ('queued','running')",(space_id,)).fetchone(): raise ValueError('此范围已有执行中的批次')
        if db.execute("SELECT (SELECT count(*) FROM jobs WHERE status IN ('queued','running'))+(SELECT count(*) FROM research_runs WHERE status IN ('queued','running'))").fetchone()[0]>=5: raise ValueError('分析队列已满（最多5个）')
        current=db.execute('SELECT body FROM research_spaces WHERE id=?',(space_id,)).fetchone()
        if not current or json.loads(current[0])['version']!=p['space']['version']: raise ValueError('规则已变化，请重新预览')
        db.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?)',(run['id'],space_id,actor,payload.request_key,fingerprint,run['status'],encode(run)))
        db.execute('INSERT INTO research_run_inputs VALUES(?,?)',(run['id'],encode(p)))
        for s in p['sources']:
            db.execute('INSERT OR IGNORE INTO research_source_versions VALUES(?,?)',(s['version_id'],encode(dict(source_key=s['source_key'],evidence=s['evidence']))))
    return public_run(run)


def public_run(run):
    return {k:v for k,v in run.items() if k!='plan'}|dict(plan={k:v for k,v in run['plan'].items() if k not in ('sources','provider')})


class BudgetReached(Exception): pass


async def _call(run_id,kind,evidence,key):
    hit=cached(key)
    if hit: return hit['result']
    run=run_get(run_id)
    if run['status']=='cancelled': raise asyncio.CancelledError()
    if len(run['calls'])>=run['max_calls']: raise BudgetReached()
    call=dict(kind=kind,key=key,status='started',started_at=store.now())
    calls=run['calls']+[call];update_run(run_id,calls=calls)
    try:
        result,usage,model_name=await model.analyze(kind,evidence)
        result=model.validate(kind,result,evidence)
        put_cache(key,dict(result=result,usage=usage,model=model_name))
        calls[-1].update(status='completed',usage=usage,model=model_name)
        update_run(run_id,calls=calls)
        return result
    except asyncio.CancelledError:
        calls[-1]['status']='unknown';update_run(run_id,calls=calls);raise
    except Exception:
        calls[-1]['status']='failed_or_unknown';update_run(run_id,calls=calls);raise


def _asset(source,asset):
    version='' if all(e.startswith('c:') for e in asset['evidence_ids']) else source['version_id']
    return dict(id=digest([source['source_key'],version,asset]),source_key=source['source_key'],version_id=source['version_id'],
                title=source['title'],status='候选，来源声明未核实',**asset)


async def execute(run_id):
    run=run_get(run_id)
    with store.connect() as db: p=json.loads(db.execute('SELECT body FROM research_run_inputs WHERE id=?',(run_id,)).fetchone()[0])
    config=p['provider']
    if signature()!=config:
        update_run(run_id,status='failed',error='模型配置已变化，请重新预览后建立新批次');return
    update_run(run_id,status='running')
    results=[];assets=[];evidence={};member_data=[]
    for source in p['sources']:
        cid=source['content_id'];item=dict(content_id=cid,title=source['title'],status='completed',error=None)
        try:
            material=await _call(run_id,'material',source['evidence'],source['material_key'])
            comment_hits=cached_many(cache_key('comment',e['id'],config) for e in source['comment_evidence'])
            missing=[e for e in source['comment_evidence'] if cache_key('comment',e['id'],config) not in comment_hits]
            if missing:
                comments=await _call(run_id,'comments',missing,cache_key('comments_batch',[e['id'] for e in missing],config))
                put_caches((cache_key('comment',c['evidence_id'],config),dict(result=c)) for c in comments['contributions'])
        except BudgetReached: item.update(status='pending',error='达到本批模型调用上限，未自动继续')
        except Exception as exc:
            item.update(status='failed',error=str(exc)[:300] if isinstance(exc,providers.AnalysisError) else '此项分析失败，未保存无效结果')
        # Successful body understanding is usable even if comments are pending.
        hit=cached(source['material_key'])
        if hit:
            material=hit['result'];member_data.append(dict(source=source,material=material))
            assets.extend(_asset(source,a) for a in material['assets'])
            # Keep expression observations available even when the model extracts no claims.
            assets.append(_asset(source,dict(kind='方法候选',category='表达方法',text=(material['opening']+'；'+material['structure'])[:800] or material['summary'][:800],evidence_ids=[source['evidence'][0]['id']])))
            evidence.update({e['id']:dict(e,source_key=source['source_key']) for e in source['evidence']})
        comment_covered=0
        comment_hits=cached_many(cache_key('comment',e['id'],config) for e in source['comment_evidence'])
        for e in source['comment_evidence']:
            c=comment_hits.get(cache_key('comment',e['id'],config))
            if c:
                comment_covered+=1;evidence[e['id']]=dict(e,source_key=source['source_key'])
                if c['result']['need']:
                    assets.append(_asset(source,dict(kind='读者问题',category=c['result']['category'],text=c['result']['need'],evidence_ids=[e['id']])))
        item.update(body_covered=bool(hit),comments_covered=comment_covered,comments_sampled=len(source['comment_evidence']))
        results.append(item);update_run(run_id,items=results)
    groups=[]
    buckets=sorted({(a['category'],digest(a['source_key'])[0]) for a in assets})
    for category,bucket in buckets:
        group_assets=[a for a in assets if a['category']==category and digest(a['source_key'])[0]==bucket]
        # Stable categories avoid re-paging all groups when one source arrives.
        # Bounded synthesis includes exact assets + their source quotes, never only old summaries.
        units=[];chars=0;included=[];by_evidence={}
        for a in sorted(group_assets,key=lambda a:a['id']):
            for eid in a['evidence_ids']:
                if eid in evidence: by_evidence.setdefault(eid,[]).append(a)
        for eid,related in sorted(by_evidence.items()):
            unit=dict(id=eid,text=encode(dict(assets=[dict(text=a['text'],kind=a['kind']) for a in related],quote=evidence[eid]['text'])))
            size=len(encode(unit))
            if chars+size>36000: continue
            chars+=size;units.append(unit);included.extend(a for a in related if a not in included)
        if p['space']['kind']=='account':
            for data in member_data:
                s=data['source'];m=data['material']
                if digest(s['source_key'])[0]!=bucket: continue
                text=encode(dict(title=s['title'],published_at=s['published_at'],opening=m['opening'],structure=m['structure']))
                if chars+len(text)>40000: break
                units.append(dict(id=s['evidence'][0]['id'],text=text));chars+=len(text)
        # Merge repeated evidence ids into one unit without losing asset text.
        merged={}
        for u in units: merged.setdefault(u['id'],[]).append(u['text'])
        units=[dict(id=k,text='\n'.join(v)) for k,v in sorted(merged.items())]
        key=cache_key('synthesis',[p['space']['kind'],category,bucket,units],config)
        group=dict(id=category+':'+bucket,category=category,bucket=bucket,asset_count=len(group_assets),synthesized_assets=len(included),
                   source_count=len({a['source_key'] for a in group_assets}),comment_count=len({eid for a in group_assets for eid in a['evidence_ids'] if eid.startswith('c:')}),
                   status='completed',cache_hit=bool(cached(key)),result=None)
        try: group['result']=await _call(run_id,p['space']['kind'],units,key)
        except BudgetReached: group.update(status='pending',error='达到调用上限，综合待下一轮')
        except Exception as exc: group.update(status='failed',error=str(exc)[:300] if isinstance(exc,providers.AnalysisError) else '分组综合失败')
        if group['status']=='completed' and len(included)<len(group_assets):
            group.update(status='partial',error='分组达到输入上限，仅综合标示数量的资产。请缩小范围或时间窗口；未纳入资产仍可在类目中查阅。')
        groups.append(group)
    members=[dict(source_key=s['source_key'],version_id=s['version_id'],comments_hash=s['comments_hash'],metrics=s['metrics'],account_id=s['account_id'],title=s['title'],published_at=s['published_at']) for s in p['sources']]
    stats=dict(members=len(members),body_covered=sum(i['body_covered'] for i in results),comments_saved=sum(s['saved_comments'] for s in p['sources']),
               comments_sampled=sum(len(s['comment_evidence']) for s in p['sources']),comments_covered=sum(i['comments_covered'] for i in results),
               comments_note_coverage=sum(bool(i['comments_covered']) for i in results),unknown_accounts=sum(not s['account_id'] for s in p['sources']))
    stats['metrics']={k:dict(known=sum(s['metrics'][k] is not None for s in p['sources']),total=sum(s['metrics'][k] or 0 for s in p['sources'])) for k in ('likes','favorites','comments','shares')}
    # An unchanged membership/report reuses the exact previous immutable revision.
    materials=[dict(source_key=d['source']['source_key'],title=d['source']['title'],body_available=d['source']['body_available'],
                    body_truncated=d['source']['body_chars']>12000,evidence_ids=[e['id'] for e in d['source']['evidence']],**d['material']) for d in member_data]
    content=dict(space={k:v for k,v in p['space'].items() if k not in ('actor','updated_at')},members=members,stats=stats,assets=assets,groups=groups,evidence=list(evidence.values()),materials=materials,items=results,config=p['config'])
    semantic=dict(content,groups=[{k:v for k,v in g.items() if k!='cache_hit'} for g in groups])
    revision_hash=digest(semantic);previous=revisions(run['space_id'])
    old=next((r for r in previous if r['fingerprint']==revision_hash),None)
    status='completed' if all(i['status']=='completed' for i in results) and all(g['status']=='completed' for g in groups) else 'partial'
    rev=old or dict(id=uuid4().hex,space_id=run['space_id'],created_at=store.now(),run_id=run_id,fingerprint=revision_hash,status=status,**content)
    if not old:
        with store.connect() as db: db.execute('INSERT INTO research_revisions VALUES(?,?,?)',(rev['id'],run['space_id'],encode(rev)))
    update_run(run_id,status=status,revision_id=rev['id'],finished_at=store.now())


def recover():
    with store.connect() as db:
        rows=db.execute("SELECT id,body FROM research_runs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            run=json.loads(row['body'])
            for call in run['calls']:
                if call['status']=='started': call['status']='unknown'
            run.update(status='interrupted',error='服务重启，批次已中断。已完成缓存保留；未完成调用可能已计费，请预览新批次后手动继续。')
            db.execute('UPDATE research_runs SET status=?,body=? WHERE id=?',('interrupted',encode(run),row['id']))


def detail(space_id):
    space=get(space_id)
    if not space: raise ValueError('研究范围不存在')
    with store.connect() as db: runs=[public_run(json.loads(r[0])) for r in db.execute('SELECT body FROM research_runs WHERE space_id=? ORDER BY rowid DESC LIMIT 20',(space_id,))]
    history=revisions(space_id)
    return dict(space=space,runs=runs,latest=history[0] if history else None,
                revisions=revision_headers(space_id))


def assets(space_id,search='',category=''):
    history=revisions(space_id)
    if not history: return []
    with store.connect() as db: tags={r['id']:dict(tags=json.loads(r['body']),version=r['version']) for r in db.execute('SELECT * FROM research_asset_tags')}
    result=[]
    for a in history[0]['assets']:
        a=dict(a,**tags.get(a['id'],dict(tags=[],version=0)))
        if search and search not in a['text'] and search not in a['title']: continue
        if category and category!=a['category'] and category not in a['tags']: continue
        result.append(a)
    return result


def tag_asset(space_id,asset_id,payload):
    if not any(a['id']==asset_id for a in assets(space_id)): raise ValueError('当前范围没有此资产')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT version FROM research_asset_tags WHERE id=?',(asset_id,)).fetchone()
        if (row[0] if row else 0)!=payload.version: raise ValueError('类目标签已更新，请重新读取')
        db.execute('INSERT OR REPLACE INTO research_asset_tags VALUES(?,?,?)',(asset_id,payload.version+1,encode(payload.tags)))
    return dict(id=asset_id,tags=payload.tags,version=payload.version+1)


def project_context(project_id):
    """Explicit opt-in only; pin exact revisions and carry quotes, not preferences."""
    if not project_id: raise ValueError('使用项目研究需先选择内容项目')
    with store.connect() as db:
        project=db.execute('SELECT body FROM content_projects WHERE id=?',(project_id,)).fetchone()
        if not project or json.loads(project[0])['archived']: raise ValueError('项目不存在或已归档')
        refs=[r[0] for r in db.execute("SELECT resource_id FROM project_links WHERE project_id=? AND kind='research' ORDER BY created_at DESC LIMIT 3",(project_id,))]
    result=[];budget=6000
    for ref in refs:
        report=revision(ref)
        if not report: continue
        evidence={e['id']:e for e in report['evidence']};selected=[]
        for asset in report['assets'][:8]:
            item=dict(kind=asset['kind'],category=asset['category'],text=asset['text'],status=asset['status'],
                      evidence=[dict(id=eid,source_key=evidence[eid]['source_key'],quote=evidence[eid]['text'][:400]) for eid in asset['evidence_ids'][:2] if eid in evidence])
            size=len(encode(item))
            if size>budget: break
            budget-=size;selected.append(item)
        if selected:result.append(dict(revision_id=ref,name=report['space']['name'],status=report['status'],assets=selected,
                                      policy='项目明确引用的研究版本；候选解释与来源声明均待核实，不代表目标账号偏好或已验证事实。仅截取部分资产和原文。'))
    return result
