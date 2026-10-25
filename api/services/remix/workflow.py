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
from . import store, providers
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


def source_for(payload):
    if payload.source=='upload':
        item=store.upload(payload.upload_id)
        if not item or not item['sha256'] or not store.upload_path(item).is_file():
            raise AnalysisError('请先完整上传视频')
        return dict(kind='upload',title=payload.title or Path(item['filename']).stem,upload_id=item['id'],
                    body='',sha256=item['sha256'],url=None,video_available=True)
    if not payload.content_id:
        raise AnalysisError('请选择已有的小红书内容')
    with analytics.read_db() as (db,_):
        row=db.execute("SELECT * FROM analytics_content WHERE platform='xhs' AND content_id=?",(payload.content_id,)).fetchone()
        if row is None:
            raise AnalysisError('没有找到这条小红书内容，请先在采集工作台采集')
        note=dict(row);body=''
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='xhs_note' AND type='table'").fetchone():
            native=db.execute('SELECT * FROM xhs_note WHERE note_id=? LIMIT 1',(payload.content_id,)).fetchone()
            if native:
                body=str(dict(native).get('desc') or '')
    return dict(kind='xhs',title=(note.get('title') or payload.content_id)[:1000],body=body[:providers.MAX_SOURCE_CHARS-1000],
                truncated=len(body)>providers.MAX_SOURCE_CHARS-1000,content_id=payload.content_id,author=note.get('author'),
                url='https://www.xiaohongshu.com/explore/'+quote(payload.content_id,safe=''),
                published_at=note.get('published_at'),collected_at=note.get('collected_at'),
                metrics={k:note.get(k) for k in ('likes','favorites','comments','shares')},
                video_available=bool(local_video(payload.content_id)))


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
    evidence=[]
    def append(kind,text,**extra):
        evidence.append(dict(id=f'E{len(evidence)+1:04d}',kind=kind,text=text,**extra))
    append('title',source['title'][:1000])
    body=source.get('body','')
    for start in range(0,len(body),600):
        append('body',body[start:start+600])
    for segment in transcript:
        append('transcript',segment['text'],start=segment['start'],end=segment['end'])
    if sum(len(e['text']) for e in evidence)>providers.MAX_SOURCE_CHARS:
        raise AnalysisError('正文与转写合计超过 30000 字，请使用更短素材或单独上传视频')
    return evidence


async def evidence_node(state):
    job=store.get(state['job_id']);stage(job['id'],'evidence')
    evidence=build_evidence(job['source'],state.get('transcript',[]))
    store.update(job['id'],evidence=evidence,transcript=state.get('transcript',[]),duration=state.get('duration'))
    return {'evidence':evidence}


async def analysis_node(state):
    stage(state['job_id'],'analyze')
    config=providers.config()
    key=hashlib.sha256(json.dumps(dict(evidence=state['evidence'],model=config['model'],endpoint=config['base_url'],
                                     prompt=providers.PROMPT_VERSION),sort_keys=True).encode()).hexdigest()
    cached=store.cached_report(key)
    if cached:
        providers.validate_report(cached['report'],state['evidence'])
        return dict(report=cached['report'],usage={'total_tokens':0},model=cached['model'],cache_hit=True)
    report,usage,model=await providers.analyze(state['evidence'])
    providers.validate_report(report,state['evidence'])
    store.cache_report(key,report,model)
    return dict(report=report,usage=usage,model=model,cache_hit=False)


async def validate_node(state):
    job=store.get(state['job_id']);stage(job['id'],'validate')
    report=providers.validate_report(state['report'],state['evidence'])
    limitations=['单条内容分析不能证明爆款因果，创作建议为待验证假设。','第一版未分析封面、画面及评论，不能据此推断视觉效果或观众反馈。']
    if not job['source'].get('body'):
        limitations.append('源数据没有独立正文。摘要与拆解仅基于现有标题和所选视频转写。')
    if job['source'].get('truncated'):
        limitations.append('正文因输入长度上限被截断，仅分析保存的前段内容。')
    if state.get('transcript'):
        limitations.append('语音识别可能存在错字，引用时间戳来自转写结果，需结合原视频核查。')
    report['limitations']=list(dict.fromkeys(report['limitations']+limitations))
    store.update(job['id'],status='completed',stage='completed',report=report,error=None,
                 model=state['model'],usage=state['usage'],cache_hit=state.get('cache_hit',False),prompt_version=providers.PROMPT_VERSION,
                 evidence_sha256=hashlib.sha256(json.dumps(state['evidence'],sort_keys=True).encode()).hexdigest(),finished_at=store.now())
    return {'report':report}


def graph(checkpointer):
    builder=StateGraph(State)
    for name,node in [('media',media_node),('transcribe',transcript_node),('evidence',evidence_node),('analyze',analysis_node),('validate',validate_node)]:
        builder.add_node(name,node)
    sequence=[START,'media','transcribe','evidence','analyze','validate',END]
    for a,b in zip(sequence,sequence[1:]):
        builder.add_edge(a,b)
    return builder.compile(checkpointer=checkpointer)


async def run(job_id):
    with tracing_context(enabled=False):
        async with AsyncSqliteSaver.from_conn_string(str(store.root()/'checkpoints.sqlite3')) as saver:
            workflow=graph(saver)
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
        self.loop=asyncio.create_task(self.consume())

    async def consume(self):
        try:
            while True:
                with store.connect() as db:
                    row=db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
                if not row:
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


manager=Manager()


def markdown(job):
    if not job.get('report'):
        raise AnalysisError('报告尚未完成')
    report=job['report'];out=[f"# {job['title']}",report['summary'], '引用：'+', '.join(report['summary_evidence_ids'])]
    if job.get('source',{}).get('url'):
        out += ['原始内容：'+job['source']['url']]
    for section in report['sections']:
        out += [f"## {section['dimension']}"]
        for finding in section['findings']:
            out += ['观察：'+finding['observation'],'推断 / 待验证：'+finding['interpretation'],'引用：'+', '.join(finding['evidence_ids'])]
    out+=['## 原创选题与提纲']
    for idea in report['ideas']:
        out += ['### '+idea['title'],*['- '+s for s in idea['outline']],'启发来源：'+', '.join(idea['evidence_ids'])]
    out += ['## 分析局限',*['- '+s for s in report['limitations']],'## 引用原文']
    for evidence in job.get('evidence',[]):
        timestamp=f" [{evidence['start']:.1f}–{evidence['end']:.1f}s]" if evidence['kind']=='transcript' else ''
        out += [f"### {evidence['id']}{timestamp}",evidence['text']]
    out += ['## 生成信息',f"模型：{job.get('model')}；提示词：{job.get('prompt_version')}；生成时间：{job.get('finished_at')}",
            f"输入证据 SHA256：{job.get('evidence_sha256')}",f"转写：{job.get('asr_provider','未使用')} / {job.get('asr_model','未使用')}"]
    return '\n\n'.join(out)+'\n'
