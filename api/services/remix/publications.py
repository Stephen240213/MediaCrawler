"""Manual publication receipts and observation snapshots. No platform/network calls."""
import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from statistics import median
from urllib.parse import urlsplit,urlunsplit
from uuid import uuid4
from . import store, profiles

METRICS=('views','likes','favorites','comments','shares','inquiries','conversions')
HOSTS={'xhs':('xiaohongshu.com','www.xiaohongshu.com','xhslink.com'),
       'douyin':('www.douyin.com','douyin.com','v.douyin.com'),
       'bilibili':('www.bilibili.com','bilibili.com','b23.tv')}


def timestamp(value):
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError()
        dt=dt.astimezone(timezone.utc)
        if dt>datetime.now(timezone.utc):raise ValueError()
        return dt.isoformat()
    except (ValueError,TypeError,AttributeError):raise ValueError('请填写带时区且不晚于当前时间的日期') from None


def public_url(value,platform):
    try:
        p=urlsplit(value)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443) or not p.path.strip('/'):
            raise ValueError()
        if any(c.isspace() or ord(c)<32 for c in value):raise ValueError()
        if platform in HOSTS and p.hostname not in HOSTS[platform]:raise ValueError()
        if p.hostname=='localhost' or p.hostname.endswith(('.local','.localhost')):raise ValueError()
        try:address=ipaddress.ip_address(p.hostname)
        except ValueError:address=None
        if address is not None:raise ValueError()
        host={'xiaohongshu.com':'www.xiaohongshu.com','douyin.com':'www.douyin.com','bilibili.com':'www.bilibili.com'}.get(p.hostname,p.hostname)
        return urlunsplit(('https',host,p.path.rstrip('/'),'',''))
    except (ValueError,TypeError):raise ValueError('作品链接需为对应平台的 HTTPS 公开地址，不含账号密码或内网地址') from None


def listing(job_id=None):
    with store.connect() as db:
        rows=db.execute('SELECT body FROM publications'+(' WHERE job_id=?' if job_id else '')+' ORDER BY rowid DESC LIMIT 1000', (job_id,) if job_id else ()).fetchall()
    return [json.loads(r[0]) for r in rows]


def get(publication_id):
    with store.connect() as db:row=db.execute('SELECT body FROM publications WHERE id=?',(publication_id,)).fetchone()
    return json.loads(row[0]) if row else None


def observations(publication_id):
    with store.connect() as db:rows=db.execute('SELECT body FROM publication_observations WHERE publication_id=? ORDER BY rowid DESC LIMIT 500',(publication_id,)).fetchall()
    return sorted([json.loads(r[0]) for r in rows],key=lambda r:r['observed_at'],reverse=True)


def save(job_id,payload,actor,publication_id=None):
    job=store.get(job_id)
    if not job or job['status']!='completed':raise ValueError('请先完成分析并保存实际发布的成稿版本')
    version=next((v for v in store.versions(job_id) if v['id']==payload.version_id),None)
    if not version:raise ValueError('请选择本任务已保存的成稿版本')
    data=payload.model_dump();source=job.get('source') or {};brief=source.get('creative_brief') or job['input'].get('brief',{})
    if data['production_id']:
        from . import production
        artifact=production.get(data['production_id'])
        if not artifact or artifact['job_id']!=job_id or artifact['version_id']!=data['version_id']:
            raise ValueError('制作版本必须属于当前任务及所选成稿')
    profile_id=data['profile_id'] if data['profile_id'] is not None else (source.get('profile')or{}).get('id','')
    profile=profiles.get(profile_id) if profile_id else None
    if profile_id and not profile:raise ValueError('实际发布账号画像不存在')
    if profile and not profile['enabled'] and not publication_id:raise ValueError('实际发布账号画像已停用')
    platform=profile['platform'] if profile else brief.get('platform','xhs')
    data.update(url=public_url(data['url'],platform),published_at=timestamp(data['published_at']),
                platform=platform,profile_id=profile_id,profile_snapshot={k:profile[k] for k in ('id','version','name','platform')} if profile else None,
                framework=version['framework'],title=version['title'],content=version['content'],job_id=job_id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT id,body FROM publications').fetchall():
            existing=json.loads(row['body'])
            if existing['url']==data['url'] and row['id']!=publication_id and existing['job_id']!=job_id:
                raise ValueError('该作品已关联其他任务，请维护原记录，避免重复计数')
        if publication_id:
            row=db.execute('SELECT body FROM publications WHERE id=? AND job_id=?',(publication_id,job_id)).fetchone()
            if not row:raise ValueError('发布记录不存在')
            old=json.loads(row[0])
            if old['version']!=data['version']:raise ValueError('发布记录已更新，请刷新后重试')
            if db.execute('SELECT count(*) FROM publication_observations WHERE publication_id=?',(publication_id,)).fetchone()[0]:
                if any(old.get(k,'')!=data[k] for k in ('version_id','production_id','url','published_at','content_type','topic','profile_id','platform')):
                    raise ValueError('已有观测后不能更改作品、发布时间和分组，避免改变统计口径；可编辑备注')
            if old['profile_id']==profile_id:data['profile_snapshot']=old.get('profile_snapshot')
            data.update(version=old['version']+1,created_at=old['created_at'])
        else:
            rows=db.execute('SELECT body FROM publications WHERE job_id=?',(job_id,)).fetchall()
            for row in rows:
                old=json.loads(row[0])
                if old['url']==data['url']:
                    if all(old.get(k,'')==data[k] for k in ('version_id','production_id','published_at','content_type','topic','notes','profile_id')):return old
                    raise ValueError('该作品已回填，请编辑现有发布记录')
            if db.execute('SELECT count(*) FROM publications').fetchone()[0]>=1000:raise ValueError('发布记录已达1000条上限')
            publication_id=uuid4().hex;data.update(version=1,created_at=store.now())
        data.update(id=publication_id,actor=actor,updated_at=store.now(),status='published_manually')
        db.execute('INSERT OR REPLACE INTO publications VALUES(?,?,?)',(publication_id,job_id,json.dumps(data,ensure_ascii=False)))
    return data


def observe(publication_id,payload,actor):
    publication=get(publication_id)
    if not publication:raise ValueError('发布记录不存在')
    data=payload.model_dump(exclude={'request_key'});data['observed_at']=timestamp(data['observed_at'])
    if data['observed_at']<publication['published_at']:raise ValueError('观测时间不能早于发布时间')
    if all(data[k] is None for k in METRICS):raise ValueError('至少填写一项指标，未知指标请留空')
    for k in ('inquiries','conversions'):
        if data[k] is not None and data['views'] is not None and data[k]>data['views']:
            raise ValueError('咨询或转化数不能大于播放/阅读数，请核对相同统计范围')
    digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        fresh=json.loads(db.execute('SELECT body FROM publications WHERE id=?',(publication_id,)).fetchone()[0])
        if data['observed_at']<fresh['published_at']:raise ValueError('观测时间不能早于发布时间')
        row=db.execute('SELECT fingerprint,body FROM publication_observations WHERE publication_id=? AND request_key=?',(publication_id,payload.request_key)).fetchone()
        if row:
            if row[0]!=digest:raise ValueError('重复提交键对应的观测不同')
            return json.loads(row[1])
        if db.execute('SELECT count(*) FROM publication_observations WHERE publication_id=?',(publication_id,)).fetchone()[0]>=500:raise ValueError('单篇作品观测已达500条上限')
        data.update(id=uuid4().hex,publication_id=publication_id,publication_version=fresh['version'],actor=actor,created_at=store.now(),source='manual')
        db.execute('INSERT INTO publication_observations VALUES(?,?,?,?,?)',(data['id'],publication_id,payload.request_key,digest,json.dumps(data,ensure_ascii=False)))
    return data


def review(publication_id,metric='favorites',window_hours=72,tolerance_hours=24):
    target=get(publication_id)
    if not target:raise ValueError('发布记录不存在')
    if metric not in METRICS:raise ValueError('指标无效')
    def select(p):
        start=datetime.fromisoformat(p['published_at'])
        candidates=[]
        for o in observations(p['id']):
            hours=(datetime.fromisoformat(o['observed_at'])-start).total_seconds()/3600
            if abs(hours-window_hours)<=tolerance_hours:candidates.append((abs(hours-window_hours),-datetime.fromisoformat(o['observed_at']).timestamp(),o,hours))
        if not candidates:return None
        _,_,o,hours=min(candidates,key=lambda x:(x[0],x[1]))
        return o,hours
    if not target['profile_id'] or not target['topic'].strip():
        return dict(target=target,available=False,reason='同类复盘需要明确账号画像与题材标签，未归属记录不作同账号对照',peers=[],metric=metric)
    selected=select(target)
    if not selected:return dict(target=target,available=False,reason='目标作品没有指定内容年龄窗口内的观测，请补录对应时间的数据',peers=[],metric=metric)
    observation,age=selected
    if observation['promoted'] is None:
        return dict(target=target,available=False,reason='目标观测的推广状态未知，请补录确认是否有推广的数据快照后再作对照',peers=[],metric=metric)
    peers=[];excluded=dict(scope=0,time=0,promotion=0,missing=0)
    target_date=datetime.fromisoformat(target['published_at'])
    for p in listing():
        if p['id']==publication_id:continue
        if any(p[k]!=target[k] for k in ('profile_id','platform','topic','content_type')):
            excluded['scope']+=1;continue
        if abs((datetime.fromisoformat(p['published_at'])-target_date).total_seconds())>30*86400:
            excluded['time']+=1;continue
        sample=select(p)
        if not sample:excluded['time']+=1;continue
        o,hours=sample
        if o['promoted']!=observation['promoted']:excluded['promotion']+=1;continue
        if o[metric] is None:excluded['missing']+=1;continue
        peers.append(dict(publication_id=p['id'],title=p['title'],url=p['url'],value=o[metric],observation_id=o['id'],age_hours=round(hours,2),framework=p['framework']))
    values=[p['value'] for p in peers];baseline=median(values) if values else None;value=observation[metric]
    change=(value-baseline)/baseline*100 if value is not None and baseline not in (None,0) else None
    groups=[]
    for framework in sorted({p['framework'] for p in peers}):
        group=[p['value'] for p in peers if p['framework']==framework]
        groups.append(dict(framework=framework,count=len(group),median=median(group),minimum=min(group),maximum=max(group)))
    rates={k:(observation[k]/observation['views'] if observation[k] is not None and observation['views'] else None) for k in ('favorites','inquiries','conversions')}
    return dict(available=True,target=target,metric=metric,observation=observation,age_hours=round(age,2),
        window_hours=window_hours,tolerance_hours=tolerance_hours,peer_count=len(peers),peers=peers,excluded=excluded,
        value=value,baseline_median=baseline,relative_change_pct=change,rates=rates,framework_groups=groups,
        warning='对照少于5篇，仅供描述，不能形成稳定规律。' if len(peers)<5 else '样本为观察性数据，差异不等于改写带来的因果效果。',
        scope='同一画像、平台、题材、形式及投流状态，发布日相差不超过30天；按指定发布后年龄窗口选最近观测。无画像时仅作未归属记录比较，不代表同一真实账号。',
        suggestion='将观察到的差异及反例整理为候选经验，经人工确认后入库；不得直接作为爆款概率。')
