import {useEffect, useRef, useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import api from '@/lib/api'
import {canOperate, useSession} from '@/lib/session'
import {errorMessage} from '@/lib/workbench'
import {BriefForm, defaultBrief, EvidenceCoverage, KnowledgePanel, RewriteResults, type Rewrite} from './remix-creation'
import {ProfilePanel, CommentTopics, PublicationPanel} from './remix-learning'
import {ProductionPanel} from './remix-production'
import {useContentProjects} from './ContentProjects'
import {ResearchWorkbench} from './ResearchWorkbench'
import './remix.css'

type Config = {provider_host?: string; model: string; model_ready: boolean; issues: string[]; asr: string; asr_model: string; asr_ready: boolean; ffmpeg_ready: boolean; cloud_audio: boolean; max_seconds: number; chunk_bytes: number}
type Source = {content_id: string; title: string; author: string; content_type?: string; video_available: boolean; saved_comments?: number}
type Evidence = {id: string; kind: string; text: string; start?: number; end?: number}
type Finding = {observation: string; interpretation: string; evidence_ids: string[]}
type Report = {summary: string; summary_evidence_ids: string[]; sections: {dimension: string; findings: Finding[]}[]; ideas: {title: string; outline: string[]; evidence_ids: string[]}[]; limitations: string[]; comment_analysis?: Finding[]; rewrites?: Rewrite[]; recommended_framework?: string; framework_reason?: string}
type Job = {id: string; title: string; status: string; stage: string; created_at: string; updated_at: string; error?: string; report?: Report; source?: {profile?:{id:string;name:string;version:number};creative_brief?:typeof defaultBrief;url?: string; author?: string; published_at?: string; metrics?: Record<string,number|null>; collected_at?: string}; input?: {profile_id?:string;source:'xhs'|'upload';content_id?:string;upload_id?:string;title?:string;analyze_video: boolean;brief?: typeof defaultBrief}; evidence?: Evidence[]; duration?: number; cache_hit?: boolean; model?: string; prompt_version?: string; usage?: {total_tokens?: number}}
type Upload = {id: string; received: number; size: number; sha256?: string}
const states: Record<string,string> = {queued:'排队中',running:'分析中',completed:'已完成',failed:'失败',interrupted:'已中断',cancelled:'已取消'}
const stages: Record<string,string> = {queued:'等待执行',media:'准备音频',transcribe:'语音转写',evidence:'整理引用',analyze:'AI 拆解',validate:'校验报告',completed:'报告已完成'}
const active = (job?: Job) => job && ['running','queued'].includes(job.status)
const time = (seconds: number) => `${Math.floor(seconds/60)}:${Math.floor(seconds%60).toString().padStart(2,'0')}`

export function ContentRemixAgent({openRequest}:{openRequest?:{id:string;projectId:string;sequence:number}|null}) {
  const [dimension,setDimension]=useState<'single'|'account'|'topic'>('single')
  const [shared,setShared]=useState(false)
  useEffect(()=>{if(openRequest){setDimension('single');setShared(false)}},[openRequest?.sequence])
  return <><section className="wb-card remix-card research-nav" aria-label="研究维度"><h2>AI 内容研究与创作</h2><div className="an-actions">{([['single','单条内容'],['account','单账号'],['topic','专题与类目资产']] as const).map(([value,label])=><button key={value} className="wb-button" aria-pressed={dimension===value} onClick={()=>setDimension(value)}>{label}</button>)}</div><p className="wb-hint">三种范围共享中性素材理解。研究账号按真实发布者识别；创作账号画像在按需改写时使用。</p>{dimension==='single'&&<div className="an-actions"><button className="wb-button" aria-pressed={!shared} onClick={()=>setShared(false)}>深度拆解与按需改写</button><button className="wb-button" aria-pressed={shared} onClick={()=>setShared(true)}>共享素材研究（不生成改写）</button></div>}</section>{dimension==='single'&&!shared?<SingleContentAnalysis openRequest={openRequest}/>:<ResearchWorkbench key={dimension} kind={dimension}/>}</>
}

function SingleContentAnalysis({openRequest}:{openRequest?:{id:string;projectId:string;sequence:number}|null}) {
  const writable=canOperate(useSession());const cache=useQueryClient()
  const [mode,setMode]=useState<'xhs'|'upload'>('xhs')
  const [search,setSearch]=useState('');const [sourcePage,setSourcePage]=useState(1)
  const [contentId,setContentId]=useState('');const [sourceVideo,setSourceVideo]=useState(false)
  const [file,setFile]=useState<File|null>(null);const [video,setVideo]=useState(false)
  const [brief,setBrief]=useState({...defaultBrief})
  const [profileId,setProfileId]=useState('');const [includeGlobal,setIncludeGlobal]=useState(false)
  const [comments,setComments]=useState(true);const [pastedComments,setPastedComments]=useState('')
  const [consent,setConsent]=useState(false);const [busy,setBusy]=useState(false)
  const [uploadPercent,setUploadPercent]=useState<number|null>(null)
  const [useResearch,setUseResearch]=useState(false);const [projectId,setProjectId]=useState(openRequest?.projectId||'');const projects=useContentProjects()
  const [notice,setNotice]=useState('');const [selected,setSelected]=useState(openRequest?.id||'');const [jobsPage,setJobsPage]=useState(1)
  const [citation,setCitation]=useState<Evidence|null>(null)
  const requestKey=useRef<{fingerprint:string;key:string}|null>(null)
  const uploadKey=useRef<string|null>(null)
  const videoElement=useRef<HTMLVideoElement|null>(null)
  const config=useQuery({queryKey:['remix-config'],queryFn:async()=>(await api.get<Config>('/remix/config')).data,retry:false})
  const sources=useQuery({queryKey:['remix-sources',search,sourcePage],queryFn:async()=>(await api.get<{items:Source[];total:number}>('/remix/sources',{params:{search,page:sourcePage}})).data,retry:false})
  const jobs=useQuery({queryKey:['remix-jobs',jobsPage],queryFn:async()=>(await api.get<{items:Job[];total:number}>('/remix/jobs',{params:{page:jobsPage}})).data,refetchInterval:query=>query.state.data?.items.some(j=>['running','queued'].includes(j.status))?2000:false,retry:false})
  const detail=useQuery({queryKey:['remix-job',selected],queryFn:async()=>(await api.get<Job>('/remix/jobs/'+selected)).data,enabled:!!selected,refetchInterval:query=>query.state.data&&['running','queued'].includes(query.state.data.status)?2000:false,retry:false})
  const job=detail.data
  const changeMode=(value:'xhs'|'upload')=>{setMode(value);setVideo(value==='upload');setConsent(false);setNotice('')}
  const upload=async()=>{
    if(!file) throw new Error('请选择视频')
    if(file.size>200*1024*1024) throw new Error('视频不能超过 200 MB')
    if(!uploadKey.current) uploadKey.current=crypto.randomUUID()
    let item=(await api.post<Upload>('/remix/uploads',{filename:file.name,size:file.size,request_key:uploadKey.current})).data
    const chunk=4*1024*1024
    // Replay the last stored chunk to ensure a resumed selection still matches.
    if(item.received>0){const offset=Math.max(0,item.received-chunk);await api.put('/remix/uploads/'+item.id,file.slice(offset,item.received),{params:{offset},headers:{'Content-Type':'application/octet-stream'}})}
    while(item.received<file.size){
      const offset=item.received
      item=(await api.put<Upload>('/remix/uploads/'+item.id,file.slice(offset,Math.min(offset+chunk,file.size)),{params:{offset},headers:{'Content-Type':'application/octet-stream'},timeout:120000})).data
      setUploadPercent(Math.round(item.received/file.size*100))
    }
    return item.id
  }
  const submit=async()=>{
    setBusy(true);setNotice('');setUploadPercent(null)
    try{
      const uploadId=mode==='upload'?await upload():''
      const input={project_id:projectId,include_project_research:useResearch,source:mode,content_id:mode==='xhs'?contentId:'',upload_id:uploadId,title:mode==='upload'?file?.name.replace(/\.[^.]+$/,'')||'':'',analyze_video:video,include_comments:comments,pasted_comments:pastedComments,brief,profile_id:profileId,include_global_knowledge:includeGlobal,consent}
      const fingerprint=JSON.stringify(input)
      if(requestKey.current?.fingerprint!==fingerprint) requestKey.current={fingerprint,key:crypto.randomUUID()}
      const response=await api.post<Job>('/remix/jobs',{...input,request_key:requestKey.current.key})
      requestKey.current=null;setSelected(response.data.id);setCitation(null);setJobsPage(1)
      setNotice('已提交到电脑后端，关闭网页不会中断分析。');await cache.invalidateQueries({queryKey:['remix-jobs']})
    }catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}
  }
  const reanalyze=async()=>{
    if(!job?.input||!consent)return
    setBusy(true);setNotice('')
    try{
      const input={...job.input,project_id:projectId,include_project_research:useResearch,brief,profile_id:profileId,include_global_knowledge:includeGlobal,include_comments:comments,pasted_comments:pastedComments,consent}
      const fingerprint=JSON.stringify(input)
      if(requestKey.current?.fingerprint!==fingerprint)requestKey.current={fingerprint,key:crypto.randomUUID()}
      const response=await api.post<Job>('/remix/jobs',{...input,request_key:requestKey.current.key})
      requestKey.current=null;setSelected(response.data.id);setCitation(null);setJobsPage(1)
      setNotice('已按当前需求新建分析，原报告保留。');await cache.invalidateQueries({queryKey:['remix-jobs']})
    }catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}
  }
  const action=async(kind:'retry'|'cancel')=>{
    if(!job)return
    setBusy(true);setNotice('')
    try{await api.post(`/remix/jobs/${job.id}/${kind}`);await cache.invalidateQueries({queryKey:['remix-job',job.id]});await cache.invalidateQueries({queryKey:['remix-jobs']})}catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}
  }
  const exportReport=async()=>{
    if(!job)return
    try{const response=await api.get(`/remix/jobs/${job.id}/report.md`,{responseType:'blob'});const url=URL.createObjectURL(response.data);const a=document.createElement('a');a.href=url;a.download='ContentRemix-'+job.id+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){setNotice(errorMessage(e))}
  }
  const saveDraft=async(index:number)=>{
    if(!job)return
    try{await api.post(`/remix/jobs/${job.id}/draft`,null,{params:{idea:index}});setNotice('已保存到草稿预览，可在导航中打开编辑；重复保存不会覆盖已编辑草稿。');await cache.invalidateQueries({queryKey:['workbenchDrafts']})}catch(e){setNotice(errorMessage(e))}
  }
  const citations=(ids:string[]) => <span className="remix-citations">{ids.map(id=><button key={id} className="wb-button" onClick={()=>{const item=job?.evidence?.find(e=>e.id===id);if(item)setCitation(item)}}>引用 {id}</button>)}</span>
  return <div className="remix">
    <section className="wb-card remix-card"><div className="an-heading"><h2>ContentRemixAgent</h2><button className="wb-button" onClick={()=>{void config.refetch();void sources.refetch()}}>刷新配置与素材</button></div><p className="wb-hint">基于已有小红书内容或上传视频，生成可核查的单条内容拆解报告。分析正文、语音与已保存或手动补充的评论，按创作需求生成可编辑成稿，并复用你确认的写作经验。本次不分析封面与画面。</p>
      {config.isLoading&&<p role="status">正在检查分析环境…</p>}{config.isError&&<p role="alert">分析配置读取失败，请检查本机服务。<button className="wb-button" onClick={()=>void config.refetch()}>重试配置</button></p>}
      {config.data&&<><div className="remix-status"><span>接收服务：{config.data.provider_host||'未配置'}</span><span>分析模型：{config.data.model} · {config.data.model_ready?'已配置':'未就绪'}</span><span>FFmpeg：{config.data.ffmpeg_ready?'已安装':'未安装'}</span><span>转写：{config.data.asr==='cloud'?'云端':'本机'} {config.data.asr_model} · {config.data.asr_ready?'已配置':'未就绪'}</span></div>
        <details open={!config.data.model_ready||!config.data.asr_ready}><summary>本机配置说明</summary><p>在 Mac 的私有 remote.env 中设置以下变量，重启 MediaCrawler 后生效。密钥不要填写到网页、聊天或 Git。兼容服务需支持 /chat/completions、JSON 对象输出。</p><pre>MC_REMIX_API_KEY=your_api_key{`\n`}MC_REMIX_BASE_URL=https://api.openai.com/v1{`\n`}MC_REMIX_MODEL=gpt-4.1-mini{`\n`}MC_REMIX_ASR=moss</pre><p>Mac 安装媒体工具：<code>brew install ffmpeg</code>。MOSS 本机转写复用 BaoCut 自带的 worker 和已下载的 MOSS、Qwen3 时间戳对齐模型，不会自动下载模型。默认从 /Applications/BaoCut.app 和 ~/.baocut/models 读取，可通过 MC_REMIX_MOSS_WORKER、MC_REMIX_MOSS_MODELS 指定其他路径；需要可用的 MLX／Metal 后端。若使用 whisper.cpp，设置 MC_REMIX_ASR=local、MC_REMIX_WHISPER_MODEL，并安装 whisper-cli；可通过 MC_REMIX_WHISPER_BIN 指定路径。若改用云端转写，设置 MC_REMIX_ASR=cloud、MC_REMIX_ASR_MODEL=whisper-1，服务需支持带片段时间戳的 /audio/transcriptions。</p>{config.data.issues.map(issue=><p key={issue}>{issue}</p>)}</details>
      </>}
    </section>
    <section className="wb-card remix-card"><label>当前内容项目<select aria-label="当前内容项目" value={projectId} onChange={e=>{setProjectId(e.target.value);setUseResearch(false);setConsent(false)}}><option value="">暂不关联项目</option>{projects.data?.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select></label>{projectId&&<label className="remix-check"><input type="checkbox" checked={useResearch} disabled={!writable} onChange={e=>{setUseResearch(e.target.checked);setConsent(false)}}/>使用项目明确引用的研究版本（候选资产与原文摘录）</label>}{job&&projectId&&<button className="wb-button" disabled={!writable||busy} onClick={()=>void (async()=>{try{await api.post(`/remix/projects/${projectId}/links`,{kind:'job',resource_id:job.id});setNotice('此分析已关联项目，后续成稿和成品将自动汇集。');await cache.invalidateQueries({queryKey:['content-project',projectId]})}catch(e){setNotice(errorMessage(e))}})()}>将当前分析关联此项目</button>}</section>
    <ProfilePanel writable={writable} selected={profileId} onSelect={p=>{setProfileId(p?.id||'');setIncludeGlobal(false);setConsent(false);setBrief({...brief,platform:p?.platform||'xhs',audience:p?.audience||'',tone:p?.tone||'',topic:p?.topic||'',avoid:p?.avoid||''})}}/>
    <section className="wb-card remix-card"><h2>新建分析</h2><fieldset disabled={!writable||busy}><div className="an-actions"><button type="button" className="wb-button" aria-pressed={mode==='xhs'} onClick={()=>changeMode('xhs')}>已有小红书内容</button><button type="button" className="wb-button" aria-pressed={mode==='upload'} onClick={()=>changeMode('upload')}>上传视频</button></div>
      {mode==='xhs'?<><label>搜索已采集标题或内容 ID<input value={search} onChange={e=>{setSearch(e.target.value);setSourcePage(1)}}/></label>
        {sources.isLoading&&<p role="status">正在读取已有内容…</p>}{sources.isError&&<p role="alert">素材读取失败。<button className="wb-button" onClick={()=>void sources.refetch()}>重试素材</button></p>}
        {!!sources.data?.items.length&&<label>选择内容<select aria-label="选择内容" value={contentId} onChange={e=>{setContentId(e.target.value);setSourceVideo(!!sources.data?.items.find(s=>s.content_id===e.target.value)?.video_available);setVideo(false);setConsent(false)}}><option value="">请选择</option>{sources.data.items.map(s=><option key={s.content_id} value={s.content_id}>{s.title||s.content_id} · {s.author||'未提供作者'}{s.video_available?' · 有本机视频':''} · 已保存 {s.saved_comments??0} 条评论</option>)}</select></label>}
        {sources.data&&!sources.data.items.length&&<p className="an-empty">暂无匹配的小红书内容。请先在采集工作台选择 SQLite 采集，或上传视频。</p>}
        {!!sources.data?.total&&<div className="an-actions"><span>共 {sources.data.total} 条 · 第 {sourcePage} 页</span><button className="wb-button" disabled={sourcePage===1} onClick={()=>setSourcePage(sourcePage-1)}>上一批素材</button><button className="wb-button" disabled={sourcePage*20>=sources.data.total} onClick={()=>setSourcePage(sourcePage+1)}>下一批素材</button></div>}
        <label className="remix-check"><input type="checkbox" checked={video} disabled={!sourceVideo} onChange={e=>{setVideo(e.target.checked);setConsent(false)}}/>同时分析本机视频语音</label>{!sourceVideo&&<p className="wb-hint">没有本机视频时仅分析已保存标题与正文。可开启采集工作台的“下载媒体素材”采集，或切换上传视频。</p>}
      </>:<label>视频文件（MP4 / MOV / WebM / M4V，最多 200 MB、15 分钟）<input type="file" accept=".mp4,.mov,.webm,.m4v" onChange={e=>{setFile(e.target.files?.[0]||null);uploadKey.current=null;setConsent(false)}}/></label>}
      {!!profileId&&<label className="remix-check"><input type="checkbox" checked={includeGlobal} onChange={e=>{setIncludeGlobal(e.target.checked);setConsent(false)}}/>本次同时使用已确认的通用经验（默认仅使用此账号经验）</label>}
      <BriefForm value={brief} onChange={value=>{setBrief(value);setConsent(false)}}/>
      <label className="remix-check"><input type="checkbox" checked={comments} onChange={e=>{setComments(e.target.checked);setConsent(false)}}/>分析已有评论样本（不会自动采集）</label>
      {comments&&<label>补充评论（每行一条）<textarea aria-label="补充评论（每行一条）" rows={3} maxLength={8000} value={pastedComments} placeholder="可粘贴需要分析的评论；同一行作为一条评论，内容会发送给所选分析模型。" onChange={e=>{setPastedComments(e.target.value);setConsent(false)}}/></label>}
      {video&&config.data&&(!config.data.asr_ready||!config.data.ffmpeg_ready)&&<p className="wb-error">视频分析需要 FFmpeg 和已配置的转写服务，请先完成本机配置。</p>}
      <label className="remix-check"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>我同意将本次标题、正文、所选语音转写、评论样本、账号画像、创作需求、适用写作经验及勾选的项目研究发送给已配置的分析模型。{video&&config.data?.cloud_audio?'云端转写还会发送视频音频。':'本机转写不会向转写服务发送音频。'}模型调用可能产生费用。</label>
      <p className="wb-hint">每次最多分析 30000 字，模型输出最多 10000 token；后台串行执行，队列最多 5 个。重试未完成的模型调用可能重复计费。</p>
      <button className="wb-button primary" disabled={!consent||!config.data?.model_ready||(mode==='xhs'?!contentId:!file)||(video&&(!config.data?.asr_ready||!config.data?.ffmpeg_ready))} onClick={()=>void submit()}>{busy?'正在提交…':'开始分析'}</button>
      {busy&&uploadPercent!==null&&<p role="status">视频上传 {uploadPercent}%</p>}
    </fieldset></section>
    <KnowledgePanel writable={writable} profileId={profileId}/>
    {notice&&<p role="status" className="an-notice">{notice}</p>}
    <section className="wb-card remix-card"><h2>分析任务</h2>{jobs.isLoading&&<p role="status">正在读取任务…</p>}{jobs.isError&&<p role="alert">任务读取失败。<button className="wb-button" onClick={()=>void jobs.refetch()}>重试任务</button></p>}{jobs.data&&!jobs.data.items.length&&<p className="an-empty">还没有分析任务。配置模型后，选择真实素材开始分析。</p>}
      <div className="remix-job-list">{jobs.data?.items.map(item=><button className={`remix-job ${item.id===selected?'selected':''}`} key={item.id} onClick={()=>{setSelected(item.id);setCitation(null)}}><span>{item.title}<small>{new Date(item.created_at).toLocaleString('zh-CN')} · {stages[item.stage]||item.stage}</small></span><strong>{states[item.status]||item.status}</strong></button>)}</div>
      {!!jobs.data?.total&&<div className="an-actions"><span>共 {jobs.data.total} 个 · 第 {jobsPage} 页</span><button className="wb-button" disabled={jobsPage<=1} onClick={()=>setJobsPage(jobsPage-1)}>上一页任务</button><button className="wb-button" disabled={jobsPage*20>=jobs.data.total} onClick={()=>setJobsPage(jobsPage+1)}>下一页任务</button></div>}
    </section>
    {selected&&<section className="wb-card remix-card" aria-label="分析任务详情">{detail.isLoading&&<p role="status">正在读取分析结果…</p>}{detail.isError&&<p role="alert">结果读取失败。<button className="wb-button" onClick={()=>void detail.refetch()}>重试结果</button></p>}{job&&<><div className="an-heading"><h2>{job.title}</h2><span>{states[job.status]} · {stages[job.stage]}</span></div>
      <div className="an-actions">{active(job)&&<button className="wb-button" disabled={!writable||busy} onClick={()=>void action('cancel')}>取消分析</button>}{['failed','cancelled','interrupted'].includes(job.status)&&<button className="wb-button" disabled={!writable||busy} onClick={()=>void action('retry')}>从检查点重试</button>}{job.report&&<><button className="wb-button" onClick={()=>void exportReport()}>导出 Markdown</button><button className="wb-button" disabled={!writable||busy||!consent||!config.data?.model_ready} onClick={()=>void reanalyze()}>按当前需求重新分析此素材</button></>}</div>
      {job.error&&<p role="alert" className="wb-error">{job.error}</p>}{active(job)&&<p role="status">任务由电脑后台运行，可离开此页面。当前阶段：{stages[job.stage]}</p>}
      {job.source?.url&&<p><a href={`/api/remix/jobs/${job.id}/source-link`} target="_blank" rel="noreferrer">查看原始小红书内容 ↗</a></p>}
      {job.source?.metrics&&<dl className="remix-metrics"><div><dt>作者</dt><dd>{job.source.author||'未提供'}</dd></div><div><dt>发布时间</dt><dd>{job.source.published_at?new Date(job.source.published_at).toLocaleString('zh-CN'):'未提供'}</dd></div>{Object.entries({likes:'点赞',favorites:'收藏',comments:'评论',shares:'分享'}).map(([key,label])=><div key={key}><dt>{label}</dt><dd>{typeof job.source?.metrics?.[key]==='number'?job.source.metrics[key]?.toLocaleString('zh-CN'):'—'}</dd></div>)}</dl>}
      {job.report&&<>{job.source?.profile&&<p>本次账号画像：{job.source.profile.name} · v{job.source.profile.version}（冻结快照）</p>}<EvidenceCoverage evidence={job.evidence||[]}/><h3>内容摘要</h3><p>{job.report.summary}</p>{citations(job.report.summary_evidence_ids)}
        {job.report.sections.map(section=><section className="remix-section" key={section.dimension}><h3>{section.dimension}</h3>{section.findings.map((finding,i)=><div className="remix-finding" key={i}><p><strong>观察：</strong>{finding.observation}</p><p><strong>推断 / 待验证：</strong>{finding.interpretation}</p>{citations(finding.evidence_ids)}</div>)}</section>)}
        <CommentTopics jobId={job.id} onCite={citations}/>
        {!!job.report.comment_analysis?.length&&<section className="remix-section"><h3>评论样本分析</h3>{job.report.comment_analysis.map((finding,i)=><div key={i} className="remix-finding"><p><strong>观察：</strong>{finding.observation}</p><p><strong>改写启发：</strong>{finding.interpretation}</p>{citations(finding.evidence_ids)}</div>)}</section>}
        {!!job.report.rewrites?.length&&<RewriteResults key={`rewrite-${job.id}`} jobId={job.id} rewrites={job.report.rewrites} framework={job.report.recommended_framework||''} reason={job.report.framework_reason||''} writable={writable} brief={job.source?.creative_brief||job.input?.brief} profileId={job.source?.profile?.id||''} onCite={citations}/>}
        <ProductionPanel key={`production-${job.id}`} jobId={job.id} writable={writable}/>
        <PublicationPanel jobId={job.id} writable={writable} profileId={job.source?.profile?.id||''} topic={(job.source?.creative_brief||job.input?.brief)?.topic} contentType={(job.source?.creative_brief||job.input?.brief)?.content_type}/>
        <h3>原创选题与提纲</h3>{job.report.ideas.map((idea,i)=><section className="remix-section" key={i}><h4>{idea.title}</h4><ol>{idea.outline.map((line,n)=><li key={n}>{line}</li>)}</ol>{citations(idea.evidence_ids)}<button className="wb-button" disabled={!writable} onClick={()=>void saveDraft(i)}>保存提纲到草稿</button></section>)}
        <h3>待补充与核实</h3><ul>{job.report.limitations.map((line,i)=><li key={i}>{line}</li>)}</ul><p className="wb-hint">模型：{job.model}{job.cache_hit?' · 复用已校验报告缓存':''} · 提示词：{job.prompt_version} · {job.usage?.total_tokens!==undefined?`${job.usage.total_tokens} token`:'服务未提供 token 用量'} · 更新于 {new Date(job.updated_at).toLocaleString('zh-CN')}</p>
      </>}
      {job.input?.analyze_video&&job.evidence?.some(e=>e.kind==='transcript')&&<div className="remix-video"><h3>原视频与转写引用</h3><video key={job.id} ref={videoElement} controls preload="metadata" src={`/api/remix/jobs/${job.id}/video`}/><details><summary>查看完整转写</summary>{job.evidence.filter(e=>e.kind==='transcript').map(e=><p key={e.id}><button className="wb-button" onClick={()=>setCitation(e)}>{time(e.start||0)} · {e.id}</button> {e.text}</p>)}</details></div>}
      {citation&&<aside className="remix-citation" aria-label="引用原文"><div className="an-heading"><h3>引用 {citation.id}</h3><button className="wb-button" onClick={()=>setCitation(null)}>关闭引用</button></div><p>{citation.text}</p>{citation.start!==undefined&&<button className="wb-button" onClick={()=>{if(videoElement.current){videoElement.current.currentTime=citation.start||0;videoElement.current.scrollIntoView({block:'center'});void videoElement.current.play().catch(()=>{})}}}>跳转视频 {time(citation.start)}–{time(citation.end||0)}</button>}</aside>}
    </>}</section>}
  </div>
}
