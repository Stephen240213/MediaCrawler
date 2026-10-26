"""Persistent LangGraph nodes. Completed artifacts are reused on manual retry."""
import asyncio
import hashlib
import json
from pathlib import Path
from typing import TypedDict
from urllib.parse import quote

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context

from api.services import analytics
from api.services.lease import Lease
from media_downloader.paths import build_media_dir
from . import store, providers, context, topics, research, task_contracts
from .providers import AnalysisError


class State(TypedDict, total=False):
    job_id: str
    audio: str
    duration: float
    transcript: list[dict]
    evidence: list[dict]
    report: dict
    usage: dict
    model: str
    cache_hit: bool
    recovery_metadata: dict


def source_for(payload):
    if payload.task_kind=='analysis' and payload.source not in ('xhs','upload'):
        raise AnalysisError('独立内容分析请选择已有内容或上传视频；数据统计和研究结果可直接用于写作')
    if payload.task_kind=='writing' and payload.source in ('xhs','upload'):
        raise AnalysisError('写作任务请复用已完成分析，或选择手填材料、数据统计、研究版本')
    if payload.reuse_job_id and payload.task_kind!='writing':raise AnalysisError('仅写作任务支持复用已有来源')
    source=_source_for(payload)
    if payload.task_kind!='legacy':source['task_kind']=payload.task_kind
    return source


def _source_for(payload):
    if payload.source in ('manual', 'report', 'analytics', 'research'):
        from .creation import source_for as creation_source
        return creation_source(payload)
    if payload.source=='upload':
        item=store.upload(payload.upload_id)
        if not item or not item['sha256'] or not store.upload_path(item).is_file():
            raise AnalysisError('请先完整上传视频')
        return context.snapshot(dict(kind='upload',title=payload.title or Path(item['filename']).stem,upload_id=item['id'],
                    body='',sha256=item['sha256'],url=None,video_available=True),payload)
    if not payload.content_id:
        raise AnalysisError('请选择已有的小红书内容')
    with analytics.read_db() as (db,_):
        row=db.execute("SELECT * FROM analytics_content WHERE platform='xhs' AND content_id=?",(payload.content_id,)).fetchone()
        if row is None:
            raise AnalysisError('没有找到这条小红书内容，请先在采集工作台采集')
        note=dict(row)
        from api.services.redfox_sources import expressions
        body_expr=expressions(db)['body']
        body=str(db.execute("SELECT "+body_expr+" FROM analytics_content c WHERE c.platform='xhs' AND c.content_id=?",(payload.content_id,)).fetchone()[0] or '')
        saved_total,comments=context.saved_comments(db,payload.content_id) if payload.include_comments else (0,[])
    return context.snapshot(dict(kind='xhs',title=(note.get('title') or payload.content_id)[:1000],body=body[:providers.MAX_SOURCE_CHARS-1000],
                truncated=len(body)>providers.MAX_SOURCE_CHARS-1000,original_body_chars=len(body),content_id=payload.content_id,author=note.get('author'),
                url='https://www.xiaohongshu.com/explore/'+quote(payload.content_id,safe=''),
                published_at=note.get('published_at'),collected_at=note.get('collected_at'),
                metrics={k:note.get(k) for k in ('likes','favorites','comments','shares')},
                video_available=bool(local_video(payload.content_id))),payload,comments,saved_total)


def local_video(content_id):
    from api.routers.workbench import DATA_ROOT
    root=DATA_ROOT.resolve();folder=build_media_dir(root,'xhs',content_id)
    if not folder.is_dir() or not folder.resolve().is_relative_to(root):
        return None
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() in ('.mp4','.mov','.webm','.m4v') and path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root) and path.stat().st_size>0:
            return path
    return None


def folder_for(job_id):
    folder=store.root()/'jobs'/job_id
    folder.mkdir(parents=True,exist_ok=True)
    return folder


def write_json(path, data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False))
    temporary.replace(path)


def stage(job_id, name):
    store.update(job_id,stage=name)


def snapshot_video(job):
    if job['source']['kind']=='upload':
        item=store.upload(job['source']['upload_id']);source=store.upload_path(item)
    else:
        source=local_video(job['source']['content_id'])
    if not source:
        raise AnalysisError('这条内容没有本机视频。请先开启“下载媒体素材”采集，或直接上传视频；也可新建仅正文分析任务。')
    folder=folder_for(job['id']);target=folder/('video'+source.suffix.lower())
    if target.is_file():
        return target
    if source.stat().st_size>200*1024*1024:
        raise AnalysisError('第一版支持 200 MB 以内的视频')
    temporary=target.with_suffix('.partial')
    with source.open('rb') as src, temporary.open('wb') as dst:
        remaining=200*1024*1024
        while chunk:=src.read(min(1024*1024,remaining+1)):
            remaining-=len(chunk)
            if remaining<0:
                raise AnalysisError('视频文件超过 200 MB')
            dst.write(chunk)
    temporary.replace(target)
    return target


async def media_node(state):
    job=store.get(state['job_id']);stage(job['id'],'media')
    if not job['input']['analyze_video']:
        return {}
    folder=folder_for(job['id']);metadata=folder/'audio-meta.json';audio=folder/'audio.wav'
    if audio.is_file() and metadata.is_file():
        info=json.loads(metadata.read_text())
        return dict(audio=str(audio),duration=info['duration'])
    video=await asyncio.to_thread(snapshot_video,job)
    audio,duration=await providers.prepare_audio(video,folder)
    write_json(metadata,{'duration':duration})
    return dict(audio=str(audio),duration=duration)


async def transcript_node(state):
    job=store.get(state['job_id']);stage(job['id'],'transcribe')
    if not job['input']['analyze_video']:
        return {'transcript':[]}
    folder=folder_for(job['id']);path=folder/'transcript.json'
    if path.is_file():
        segments=providers.validate_segments(json.loads(path.read_text()),state['duration'])
    else:
        segments=await providers.transcribe(Path(state['audio']),state['duration'],folder)
        write_json(path,segments)
    c=providers.config()
    store.update(job['id'],asr_provider=c['asr'],asr_model=providers.asr_model(c),
                 transcript_sha256=hashlib.sha256(json.dumps(segments,sort_keys=True).encode()).hexdigest())
    return {'transcript':segments}


def build_evidence(source, transcript):
    return context.build_evidence(source, transcript)


async def evidence_node(state):
    job=store.get(state['job_id']);stage(job['id'],'evidence')
    evidence=build_evidence(job['source'],state.get('transcript',[]))
    store.update(job['id'],evidence=evidence,comment_topics=topics.build(evidence),transcript=state.get('transcript',[]),duration=state.get('duration'))
    return {'evidence':evidence}


async def reuse_node(state):
    """Writing starts from its frozen package; never rerun media, ASR or analysis."""
    job=store.get(state['job_id']);stage(job['id'],'reuse')
    transcript=job['source'].get('reused_transcript',[])
    duration=job['source'].get('reused_duration')
    return {**await evidence_node(dict(state,transcript=transcript,duration=duration)), 'transcript':transcript,'duration':duration}


async def analysis_node(state):
    return await generate_node(state,'analyze',providers.analyze)


async def writing_node(state):
    return await generate_node(state,'write',providers.write)


async def generate_node(state,stage_name,generate):
    stage(state['job_id'],stage_name)
    job=store.get(state['job_id'])
    evidence=state['evidence']
    if job['source'].get('kind') in ('report','research'):
        # Retry can resume directly here with the previous version's checkpoint evidence.
        # Rebuild from the same frozen source before any model/cache use.
        evidence=build_evidence(job['source'],state.get('transcript',[]))
        store.update(job['id'],evidence=evidence)
    config=providers.config()
    key=hashlib.sha256(json.dumps(dict(evidence=evidence,model=config['model'],endpoint=config['base_url'],
                                     prompt=providers.PROMPT_VERSION),sort_keys=True).encode()).hexdigest()
    cached=store.cached_report(key)
    if cached:
        providers.validate_report(cached['report'],evidence,require_creation=task_contracts.mode(evidence)=='writing')
        return dict(evidence=evidence,report=cached['report'],usage={'total_tokens':0},model=cached['model'],cache_hit=True)
    rejected=folder_for(job['id'])/'rejected-report.json'
    evidence_hash=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
    if rejected.is_file():
        saved=json.loads(rejected.read_text())
        if saved.get('evidence_sha256')==evidence_hash:
            try:
                recovered=providers.validate_report(saved['candidate'],evidence,require_creation=task_contracts.mode(evidence)!='analysis')
            except AnalysisError:
                pass
            else:
                store.update(job['id'],model_diagnostics=None,recovered_response=True)
                return dict(evidence=evidence,report=recovered,usage=saved.get('usage',{}),model=saved.get('model',config['model']),cache_hit=False,
                    recovery_metadata=dict(prompt_version=saved.get('prompt_version'),model_generated_at=saved.get('created_at')))
    try:
        report,usage,model=await generate(evidence)
        providers.validate_report(report,evidence,require_creation=task_contracts.mode(evidence)=='writing')
    except AnalysisError as exc:
        if exc.diagnostics:
            from .artifacts import manifest
            store.update(job['id'],model_diagnostics=exc.diagnostics,artifacts=manifest(exc.candidate,evidence))
        if exc.candidate is not None:
            write_json(rejected,dict(candidate=exc.candidate,diagnostics=exc.diagnostics,
                evidence_sha256=evidence_hash,model=getattr(exc,'model',config['model']),usage=getattr(exc,'usage',{}),prompt_version=providers.PROMPT_VERSION,created_at=store.now()))
            rejected.chmod(0o600)
        raise
    store.cache_report(key,report,model)
    return dict(evidence=evidence,report=report,usage=usage,model=model,cache_hit=False)


async def validate_node(state):
    job=store.get(state['job_id']);stage(job['id'],'validate')
    report=providers.validate_report(state['report'],state['evidence'],require_creation=task_contracts.mode(state['evidence'])=='writing')
    limitations=[]
    if not job['source'].get('body'):
        limitations.append('没有独立正文，内容分析基于标题、所选语音及补充材料。')
    if state.get('transcript'):
        limitations.append('语音识别可能存在错字，请结合视频核对引用。')
    from .evidence_policy import capabilities
    has_comments=any(capabilities(e)&{'comment_text','comment_statistics'} for e in state['evidence'])
    if not has_comments and job['input'].get('include_comments',True):
        limitations.append('本次没有可用评论样本，可补充评论后新建分析。')
    # Visuals and sample scope are displayed separately from actionable missing facts.
    merged=report['limitations']+limitations
    seen=set(); concise=[]
    for item in merged:
        key=''.join(c for c in item if c.isalnum())
        if key not in seen:
            seen.add(key);concise.append(item)
    report['limitations']=concise[:5]
    from .artifacts import manifest
    recovery=state.get('recovery_metadata',{})
    store.update(job['id'],status='completed',stage='completed',report=report,error=None,model_diagnostics=None,artifacts=manifest(report,state['evidence'],True),
                 model=state['model'],usage=state['usage'],cache_hit=state.get('cache_hit',False),prompt_version=recovery.get('prompt_version',providers.PROMPT_VERSION),model_generated_at=recovery.get('model_generated_at'),
                 evidence_sha256=hashlib.sha256(json.dumps(state['evidence'],sort_keys=True).encode()).hexdigest(),finished_at=store.now())
    return {'report':report}


def graph(checkpointer,kind='legacy'):
    builder=StateGraph(State)
    if kind=='writing':
        for name,node in [('reuse',reuse_node),('write',writing_node),('validate',validate_node)]:builder.add_node(name,node)
        for a,b in zip([START,'reuse','write','validate'],['reuse','write','validate',END]):builder.add_edge(a,b)
        return builder.compile(checkpointer=checkpointer)
    for name,node in [('media',media_node),('transcribe',transcript_node),('evidence',evidence_node),('analyze',analysis_node),('validate',validate_node)]:
        builder.add_node(name,node)
    sequence=[START,'media','transcribe','evidence','analyze','validate',END]
    for a,b in zip(sequence,sequence[1:]):
        builder.add_edge(a,b)
    return builder.compile(checkpointer=checkpointer)


async def run(job_id):
    with tracing_context(enabled=False):
        async with AsyncSqliteSaver.from_conn_string(str(store.root()/'checkpoints.sqlite3')) as saver:
            workflow=graph(saver,task_contracts.kind(store.get(job_id)))
            options={'configurable':{'thread_id':job_id},'recursion_limit':12}
            checkpoint=await workflow.aget_state(options)
            initial=None if checkpoint.values else {'job_id':job_id}
            await workflow.ainvoke(initial,options)
            # A crash after the terminal state was saved can leave a completed
            # checkpoint and interrupted metadata; finalize without another LLM call.
            if store.get(job_id)['status']=='running':
                final=await workflow.aget_state(options)
                if final.values.get('report') and not final.next:
                    await validate_node(final.values)


class Manager:
    def __init__(self):
        self.loop=None;self.active=None;self.active_id=None;self.lease=None;self.users=0

    async def start(self):
        if self.loop and not self.loop.done():
            self.users+=1
            return
        self.users=1
        self.lease=Lease(store.root()/'executor.lock').acquire()
        store.recover()
        research.recover()
        self.loop=asyncio.create_task(self.consume())

    async def consume(self):
        try:
            while True:
                with store.connect() as db:
                    row=db.execute("""SELECT id,'job' kind,json_extract(body,'$.created_at') created FROM jobs WHERE status='queued'
                        UNION ALL SELECT id,'research' kind,json_extract(body,'$.created_at') created FROM research_runs WHERE status='queued'
                        ORDER BY created,id LIMIT 1""").fetchone()
                if not row or row['kind']=='research':
                    with store.connect() as db:
                        research_row=db.execute("SELECT id FROM research_runs WHERE id=? AND status='queued'",(row['id'],)).fetchone() if row else None
                    if research_row:
                        self.active_id=research_row['id']
                        self.active=asyncio.create_task(research.execute(self.active_id))
                        try:
                            await self.active
                        except asyncio.CancelledError:
                            current=research.run_get(self.active_id)
                            if current['status']!='cancelled':
                                research.update_run(self.active_id,status='interrupted',error='服务停止，已完成缓存保留。请核对调用记录后手动建立新批次。')
                            if asyncio.current_task().cancelling(): raise
                        except Exception as exc:
                            research.update_run(self.active_id,status='failed',error=str(exc)[:500] if isinstance(exc,AnalysisError) else '研究批次执行失败，已完成缓存保留')
                        finally:
                            self.active=None;self.active_id=None
                        continue
                    await asyncio.sleep(.5);continue
                job_id=row['id'];self.active_id=job_id
                store.update(job_id,status='running',error=None)
                self.active=asyncio.create_task(run(job_id))
                try:
                    await self.active
                except asyncio.CancelledError:
                    job=store.get(job_id)
                    if job['status']=='running':
                        store.update(job_id,status='interrupted',error='服务停止，可手动从检查点重试。')
                    if asyncio.current_task().cancelling():
                        raise
                except Exception as exc:
                    # Never persist raw provider bodies, URLs, keys or prompts as errors.
                    error=str(exc) if isinstance(exc,AnalysisError) else '分析执行失败，请检查服务配置与本机素材后重试。'
                    store.update(job_id,status='failed',error=error[:500])
                finally:
                    self.active=None;self.active_id=None
        finally:
            if self.lease:
                self.lease.close();self.lease=None

    async def stop(self):
        self.users=max(0,self.users-1)
        if self.users:
            return
        if self.loop and self.loop.get_loop() is not asyncio.get_running_loop():
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.stop_owned(),self.loop.get_loop()))
            return
        await self.stop_owned()

    async def stop_owned(self):
        if self.loop:
            self.loop.cancel()
            try:
                await self.loop
            except asyncio.CancelledError:
                pass
            self.loop=None

    async def cancel(self,job_id):
        job=store.get(job_id)
        if not job or job['status'] not in store.ACTIVE:
            raise AnalysisError('只能取消排队或运行中的任务')
        store.update(job_id,status='cancelled',error='用户取消，可手动从检查点重试。')
        if self.active_id==job_id and self.active:
            self.active.get_loop().call_soon_threadsafe(self.active.cancel)

    async def cancel_research(self,run_id):
        run=research.run_get(run_id)
        if not run or run['status'] not in store.ACTIVE: raise AnalysisError('只能取消排队或执行中的研究批次')
        research.update_run(run_id,status='cancelled',error='用户取消，已完成缓存保留；进行中的调用可能已计费。')
        if self.active_id==run_id and self.active:
            self.active.get_loop().call_soon_threadsafe(self.active.cancel)


manager=Manager()


def markdown(job):
    if not job.get('report'):
        raise AnalysisError('报告尚未完成')
    report=job['report'];out=[f"# {job['title']}",report['summary'], '引用：'+', '.join(report['summary_evidence_ids'])]
    profile=(job.get('source')or{}).get('profile')
    if profile:out += ['账号画像：'+profile['name']+' · v'+str(profile['version'])+'（任务快照）']
    topic_table=job.get('comment_topics')
    if not topic_table or topic_table.get('rule_version')!=topics.RULE_VERSION:
        topic_table=topics.build(job.get('evidence',[]))
    if topic_table and topic_table['sample_size']:
        out += ['## 评论议题表',topic_table['scope']]
        for group in topic_table['groups']:
            out += [group['topic']+'：'+str(group['count'])+' 条；'+group['rewrite_action'],'引用：'+', '.join(group['evidence_ids'])]
    if job.get('source',{}).get('url'):
        out += ['原始内容：'+job['source']['url']]
    for section in report['sections']:
        out += [f"## {section['dimension']}"]
        for finding in section['findings']:
            out += ['观察：'+finding['observation'],'推断 / 待验证：'+finding['interpretation'],'引用：'+', '.join(finding['evidence_ids'])]
    if report.get('comment_analysis'):
        out+=['## 评论样本分析']
        for finding in report['comment_analysis']:
            basis={'comment_statistics':'本机评论统计','raw_comments':'原始评论样本','mixed':'评论统计与原始样本'}.get(finding.get('basis'),'原始评论样本')
            out += ['依据：'+basis,finding['observation'],finding['interpretation'],'引用：'+', '.join(finding['evidence_ids'])]
    if report.get('rewrites'):
        out += ['## 改写框架',report['recommended_framework'],report['framework_reason']]
        for index,item in enumerate(report['rewrites']):
            out += [f"## 改写方案 {index+1}：{item['angle']}", '框架：'+item['framework']]
            for option in item['title_options']:
                out += ['### '+option['title'],option['reason']]
            out += [item['body'],'封面文案：'+item['cover_text']]
            if item.get('hook'):out += ['开头口播：'+item['hook']]
            out += ['### 分段脚本',*item.get('script',[]),'### 配图或镜头建议',*item['visuals'],
                    '### 待核实',*item['fact_checks'],'### 修改说明',*item['change_notes'],
                    '来源：'+', '.join(item['evidence_ids'])]
    adopted=next((v for v in store.versions(job['id']) if v['adopted']),None)
    if adopted:
        out += ['## 当前采用版本',adopted['title'],adopted['content'],'修改反馈：'+adopted['feedback']]
    out+=['## 原创选题与提纲']
    for idea in report['ideas']:
        out += ['### '+idea['title'],*['- '+s for s in idea['outline']],'启发来源：'+', '.join(idea['evidence_ids'])]
    out += ['## 分析局限',*['- '+s for s in report['limitations']],'## 引用原文']
    for evidence in job.get('evidence',[]):
        timestamp=f" [{evidence['start']:.1f}–{evidence['end']:.1f}s]" if evidence['kind']=='transcript' else ''
        out += [f"### {evidence['id']}{timestamp}",evidence['text']]
    out += ['## 生成信息',f"模型：{job.get('model')}；提示词：{job.get('prompt_version')}；生成时间：{job.get('model_generated_at') or job.get('finished_at')}",
            f"输入证据 SHA256：{job.get('evidence_sha256')}",f"转写：{job.get('asr_provider','未使用')} / {job.get('asr_model','未使用')}"]
    if job.get('recovered_response'):
        out += [f"本机重新校验恢复：{job.get('finished_at')}；未再次调用模型。"]
    return '\n\n'.join(out)+'\n'
