import {SourceArchive} from './SourceArchive'
import {CreationRegistry} from './CreationRegistry'
import {ProfilePanel} from './remix-learning'
import {TaskRelations} from './TaskRelations'
import {useContentProjects} from './ContentProjects'
import {useEffect,useRef,useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import api from '@/lib/api'
import {canOperate,useSession} from '@/lib/session'
import {errorMessage} from '@/lib/workbench'
import {BriefForm,defaultBrief,type Rewrite} from './remix-creation'

type Choice={id:string;name:string;description?:string;version?:number;content_type?:'图文'|'视频';content_types?:string[]}
export type CreationRequest={source:'report'|'analytics'|'research';referenceId?:string;filters?:Record<string,string>;sequence:number}
type Job={id:string;title:string;status:string;error?:string;stage:string;created_at:string;task_kind?:string;stored_response_available?:boolean;parent_job_id?:string;input?:{project_id:string;source:string;reference_id:string;material:string;analytics_filters:Record<string,string>;agent_id:string;skill_ids:string[];brief:typeof defaultBrief;profile_id:string};report?:{rewrites:Rewrite[]};source?:{profile?:{id:string};creative_brief?:typeof defaultBrief;analysis_dependency?:{title:string;result_sha256:string};provenance?:Record<string,unknown>;execution?:{agent:Choice;skills:Choice[]}}}
const states:Record<string,string>={queued:'排队中',running:'生成中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'}
export function CreationPanel({request,onDraft,onReport,active}:{active:boolean;request?:CreationRequest|null;onDraft:(id:string)=>void;onReport:(id:string)=>void}) {
 const writable=canOperate(useSession()),cache=useQueryClient()
 const [source,setSource]=useState('manual'),[reference,setReference]=useState(''),[material,setMaterial]=useState(''),[title,setTitle]=useState('')
 const [filters,setFilters]=useState<Record<string,string>>({}),[agent,setAgent]=useState('content_writer'),[skills,setSkills]=useState<string[]>(['curate'])
 const [brief,setBrief]=useState({...defaultBrief}),[consent,setConsent]=useState(false),[busy,setBusy]=useState(false),[notice,setNotice]=useState('')
 const [selected,setSelected]=useState(''),[page,setPage]=useState(1),[space,setSpace]=useState('')
 const [reuse,setReuse]=useState(''),[profileId,setProfileId]=useState(''),[historyPage,setHistoryPage]=useState(1)
 const [projectId,setProjectId]=useState('');const projects=useContentProjects()
 const prefill=useRef('')
 const key=useRef<{input:string;key:string}|null>(null)
 const catalog=useQuery({queryKey:['creation-catalog'],queryFn:async()=>(await api.get<{agents:Choice[];skills:Choice[];model:{model:string;model_ready:boolean;provider_host:string}}>('/remix/creation/catalog')).data,enabled:active})
 const jobs=useQuery({queryKey:['remix-jobs',page],queryFn:async()=>(await api.get<{items:Job[];total:number}>('/remix/jobs',{params:{page}})).data,enabled:active})
 const spaces=useQuery({queryKey:['research-spaces'],queryFn:async()=>(await api.get<(Choice&{name:string})[]>('/remix/research/spaces')).data,enabled:active&&source==='research'})
 const research=useQuery({queryKey:['creation-revisions',space],queryFn:async()=>(await api.get<{revisions:{id:string;created_at:string;status:string}[]}>(`/remix/research/spaces/${space}`)).data,enabled:active&&source==='research'&&!!space})
 const job=useQuery({queryKey:['remix-job',selected],queryFn:async()=>(await api.get<Job>('/remix/jobs/'+selected)).data,enabled:active&&!!selected,refetchInterval:q=>['queued','running'].includes(q.state.data?.status||'')?1500:false})
 const history=useQuery({queryKey:['writing-history',historyPage],enabled:active,queryFn:async()=>(await api.get<{items:Job[];total:number}>('/remix/jobs',{params:{task_kind:'writing',page:historyPage}})).data,refetchInterval:q=>q.state.data?.items.some(j=>['queued','running'].includes(j.status))?1500:false})
 const parent=useQuery({queryKey:['writing-source-report',reference],enabled:active&&source==='report'&&!!reference&&!reuse,queryFn:async()=>(await api.get<Job>('/remix/jobs/'+reference)).data})
 useEffect(()=>{if(request){prefill.current='';setReuse('');setSource(request.source);setReference(request.referenceId||'');setFilters(request.filters||{});setConsent(false);setSelected('');setNotice('')}},[request?.sequence])
 useEffect(()=>{if(parent.data&&source==='report'&&reference&&prefill.current!==reference&&!reuse){prefill.current=reference;setBrief({...defaultBrief,...parent.data.source?.creative_brief,content_type:catalog.data?.agents.find(a=>a.id===agent)?.content_type||'图文'});setProfileId(parent.data.source?.profile?.id||'');setProjectId(parent.data.input?.project_id||'')}},[parent.data,reference,source,reuse])
 const activeAgent=catalog.data?.agents.find(a=>a.id===agent)
 const sourceLoading=source==='report'&&!reuse&&!!reference&&(parent.isLoading||parent.isError)
 const unavailableSkills=skills.some(id=>!catalog.data?.skills.some(s=>s.id===id&&s.content_types?.includes(brief.content_type)!==false))
 useEffect(()=>setConsent(false),[activeAgent?.version,catalog.data?.skills])
 useEffect(()=>{if(activeAgent?.content_type){setBrief(b=>({...b,content_type:activeAgent.content_type!}));setSkills(ids=>ids.filter(id=>catalog.data?.skills.find(s=>s.id===id)?.content_types?.includes(activeAgent.content_type!)!==false))}},[activeAgent?.id,activeAgent?.version,activeAgent?.content_type])
 useEffect(()=>setConsent(false),[source,reference,material,title,filters,agent,skills,brief,projectId])
 const submit=async()=>{
  if(!consent||busy||!writable)return
  const previousSelection=selected
  setBusy(true);setNotice('正在提交独立写作任务…');setSelected('')
  try {
   const input={task_kind:'writing',reuse_job_id:reuse,project_id:projectId,source,reference_id:reference,material:source==='manual'?material:'',title,analytics_filters:source==='analytics'?filters:{},profile_id:profileId,agent_id:agent,agent_version:activeAgent?.version,skill_ids:skills,skill_versions:Object.fromEntries(skills.map(id=>[id,catalog.data?.skills.find(s=>s.id===id)?.version])),brief,include_comments:false,consent}
   const fingerprint=JSON.stringify(input)
   if(key.current?.input!==fingerprint)key.current={input:fingerprint,key:crypto.randomUUID()}
   const result=await api.post<Job>('/remix/jobs',{...input,request_key:key.current.key})
   key.current=null;setSelected(result.data.id);setConsent(false);setNotice('独立写作任务已提交，沿用已选来源，不重新分析或转写。');await cache.invalidateQueries({queryKey:['remix-jobs']});await cache.invalidateQueries({queryKey:['writing-history']})
  }catch(e){setSelected(previousSelection);setNotice(errorMessage(e))}finally{setBusy(false)}
 }
 const reuseSource=()=>{
  const input=job.data?.input;if(!input)return
  setReuse(selected);setSource(input.source);setReference(input.reference_id);setMaterial(input.material);setFilters(input.analytics_filters);setAgent(input.agent_id);setSkills(input.skill_ids);setBrief({...input.brief});setProfileId(input.profile_id);setProjectId(input.project_id||'');setTitle('');setConsent(false);prefill.current=input.reference_id
  setNotice('已锁定此任务的完整来源与分析快照。调整写作要求后单独生成新稿；不会重新读取当前数据或重新分析。')
 }
 const action=async(kind:'retry'|'cancel'|'revalidate')=>{setBusy(true);setNotice('');try{await api.post(`/remix/jobs/${selected}/${kind}`);await cache.invalidateQueries({queryKey:['remix-job',selected]});await cache.invalidateQueries({queryKey:['writing-history']})}catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}}
 const adopt=async(index:number,rewrite:Rewrite)=>{
  setBusy(true);setNotice('')
  try {
   const v=await api.post<{id:string}>(`/remix/jobs/${selected}/versions`,{rewrite_index:index,title_option:0,title:rewrite.title_options[0].title,content:rewrite.body,request_key:`creation-${selected}-${index}`})
   const result=await api.post<{draft:{id:string}}>(`/remix/jobs/${selected}/versions/${v.data.id}/adopt`)
   await cache.invalidateQueries({queryKey:['workbenchDrafts']});onDraft(result.data.draft.id)
  }catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}
 }
 if(!active)return null
 return <div className="remix creation-panel">
  <section className="wb-card remix-card"><h2>1. 选择内容来源</h2><p className="wb-hint">把已有结果带入创作，或填写自己的材料。提交时冻结来源，保留来源编号与数据口径。</p>
   {reuse&&<p className="an-notice" role="status">已锁定写作任务 {reuse.slice(0,8)} 的来源快照。<button className="wb-button" onClick={()=>{setReuse('');setConsent(false)}}>改选来源</button></p>}
   <fieldset disabled={!writable||busy||!!reuse}><label>创作来源<select aria-label="创作来源" value={source} onChange={e=>{setSource(e.target.value);setReference('')}}><option value="manual">自己填写</option><option value="report">AI 内容分析结果</option><option value="research">账号 / 专题研究结果</option><option value="analytics">数据分析结果</option></select></label>
   {source==='manual'&&<label>创作材料<textarea aria-label="创作材料" rows={5} maxLength={18000} value={material} onChange={e=>setMaterial(e.target.value)} placeholder="填写事实、经历、参考文案或需要表达的想法…"/></label>}
   {source==='report'&&<><label>选择分析报告<select aria-label="选择分析报告" value={reference} onChange={e=>setReference(e.target.value)}><option value="">请选择已完成报告</option>{request?.source==='report'&&request.referenceId&&!jobs.data?.items.some(j=>j.id===request.referenceId)&&<option value={request.referenceId}>已带入报告 {request.referenceId.slice(0,8)}</option>}{jobs.data?.items.filter(j=>j.status==='completed').map(j=><option key={j.id} value={j.id}>{j.title} · {new Date(j.created_at).toLocaleString('zh-CN')}</option>)}</select></label><div className="an-actions"><button className="wb-button" disabled={page<=1} onClick={()=>setPage(page-1)}>上一批报告</button><span>第 {page} 页 · 共 {jobs.data?.total||0} 个任务</span><button className="wb-button" disabled={page*20>=(jobs.data?.total||0)} onClick={()=>setPage(page+1)}>下一批报告</button></div></>}
   {source==='research'&&<><label>研究范围<select aria-label="创作研究范围" value={space} onChange={e=>{setSpace(e.target.value);setReference('')}}><option value="">请选择范围</option>{spaces.data?.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>研究版本<select aria-label="创作研究版本" value={reference} onChange={e=>setReference(e.target.value)}><option value="">请选择已完成版本</option>{request?.source==='research'&&reference&&!research.data?.revisions.some(r=>r.id===reference)&&<option value={reference}>已带入版本 {reference.slice(0,8)}</option>}{research.data?.revisions.filter(r=>['completed','partial'].includes(r.status)).map(r=><option value={r.id} key={r.id}>{new Date(r.created_at).toLocaleString('zh-CN')} · {r.status==='partial'?'部分完成':'已完成'}</option>)}</select></label></>}
   {source==='analytics'&&<><label>数据平台<select aria-label="创作数据平台" value={filters.platform||''} onChange={e=>setFilters({...filters,platform:e.target.value,task_id:''})}><option value="">全部平台</option>{[['xhs','小红书'],['dy','抖音'],['twitter','X / Twitter'],['youtube','YouTube']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label><details><summary>本次数据筛选条件</summary><pre>{JSON.stringify(filters,null,2)}</pre><p>提交时按此筛选冻结全部已保存的内容正文、原始评论和完整统计，不再只传统计摘要。可从“数据分析”中按当前筛选带入。</p></details></>}
   </fieldset><label>创作标题 / 任务名称（可选）<input disabled={!writable||busy} maxLength={200} value={title} onChange={e=>setTitle(e.target.value)}/></label>
   {(jobs.isError||spaces.isError||research.isError)&&<p role="alert" className="wb-error">来源列表读取失败，请重试。<button className="wb-button" onClick={()=>{void jobs.refetch();if(source==='research')void spaces.refetch();if(space)void research.refetch()}}>重新加载来源</button></p>}
   {source==='report'&&!reuse&&parent.isLoading&&<p role="status">正在读取分析快照与默认设置…</p>}{source==='report'&&!reuse&&parent.isError&&<p role="alert">分析快照读取失败。<button className="wb-button" onClick={()=>void parent.refetch()}>重新读取分析快照</button></p>}
  </section>
  <section className="wb-card remix-card"><h2>2. 选择 Agent 与 Skill</h2><p className="wb-hint">写作单独执行，复用所选结果。Agent 与 Skill 的版本随任务冻结，可新增自己的方法。</p>
   <button className="wb-button" onClick={()=>void catalog.refetch()}>刷新方法列表</button>
   {catalog.isError&&<p role="alert">创作配置读取失败。<button className="wb-button" onClick={()=>void catalog.refetch()}>重新加载配置</button></p>}
   <fieldset disabled={!writable||busy}><label>创作 Agent<select aria-label="创作 Agent" value={agent} onChange={e=>setAgent(e.target.value)}>{!activeAgent&&<option value={agent}>请选择可用 Agent</option>}{catalog.data?.agents.map(a=><option key={a.id} value={a.id}>{a.name} · v{a.version}</option>)}</select></label><p className="wb-hint">{activeAgent?.description}</p>
   <div className="creation-skills" aria-label="创作 Skill">{catalog.data?.skills.map(s=><label className="remix-check" key={s.id}><input type="checkbox" disabled={s.content_types?.includes(brief.content_type)===false} checked={skills.includes(s.id)} onChange={e=>setSkills(e.target.checked?[...skills,s.id]:skills.filter(id=>id!==s.id))}/>{s.name} · v{s.version}{s.content_types?.includes(brief.content_type)===false?' · 不适用此输出类型':''}</label>)}</div>
   {catalog.data&&unavailableSkills&&<p role="alert">部分已选 Skill 已停用或不适用此输出。<button className="wb-button" onClick={()=>setSkills(ids=>ids.filter(id=>catalog.data.skills.some(s=>s.id===id&&s.content_types?.includes(brief.content_type)!==false)))}>清除不可用 Skill</button></p>}
   <BriefForm contentTypeLocked defaultOpen={false} value={brief} onChange={value=>setBrief({...value,content_type:activeAgent?.content_type||value.content_type})}/>
   <ProfilePanel writable={writable} selected={profileId} onSelect={p=>{setProfileId(p?.id||'');setConsent(false)}}/>
   <label>关联内容项目<select aria-label="写作关联内容项目" value={projectId} onChange={e=>setProjectId(e.target.value)}><option value="">暂不关联项目</option>{projectId&&!projects.data?.some(p=>p.id===projectId)&&<option value={projectId}>原项目（请确认仍可使用）</option>}{projects.data?.map(p=><option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
   <p className="wb-hint">模型：{catalog.data?.model.model||'读取中'} · 接收服务：{catalog.data?.model.provider_host||'未配置'}</p>
   <label className="remix-check"><input type="checkbox" disabled={sourceLoading} checked={consent} onChange={e=>setConsent(e.target.checked)}/>我同意将所选来源的完整分析、已保存正文与评论、创作需求和 Skill 发送给已配置的模型，调用可能产生费用。</label>
   <button className="wb-button primary" disabled={sourceLoading||!consent||!activeAgent||unavailableSkills||!catalog.data?.model.model_ready||(!reuse&&(source==='manual'?!material.trim():source!=='analytics'&&!reference))} onClick={()=>void submit()}>{busy?'正在提交…':'调用 Agent 生成草稿'}</button></fieldset>
   <CreationRegistry writable={writable}/>
  </section>
  <section className="wb-card remix-card" aria-label="独立写作任务"><h2>写作任务记录</h2>{history.isError&&<p role="alert">记录读取失败。<button onClick={()=>void history.refetch()}>重试记录</button></p>}<label>打开写作任务<select aria-label="打开写作任务" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">请选择记录</option>{selected&&!history.data?.items.some(j=>j.id===selected)&&<option value={selected}>当前写作 {selected.slice(0,8)}</option>}{history.data?.items.map(j=><option key={j.id} value={j.id}>{j.title} · {states[j.status]}</option>)}</select></label><div className="an-actions"><button className="wb-button" disabled={historyPage===1} onClick={()=>setHistoryPage(historyPage-1)}>上一页写作记录</button><span>共 {history.data?.total||0} 个写作任务</span><button className="wb-button" disabled={historyPage*20>=(history.data?.total||0)} onClick={()=>setHistoryPage(historyPage+1)}>下一页写作记录</button></div></section>
  {notice&&<p role="status" className="an-notice">{notice}</p>}
  {selected&&<section className="wb-card remix-card"><div className="an-heading"><h2>3. 查看创作结果</h2><button className="wb-button" onClick={()=>onReport(selected)}>打开完整报告</button></div>{job.isError&&<p role="alert">结果读取失败。<button onClick={()=>void job.refetch()}>重试</button></p>}<p role="status">{states[job.data?.status||'']||'正在读取…'}</p><div className="an-actions">{job.data?.input&&<button className="wb-button" disabled={!writable||busy} onClick={reuseSource}>复用相同来源再写一版</button>}{['failed','cancelled','interrupted'].includes(job.data?.status||'')&&<button className="wb-button" disabled={!writable||busy} onClick={()=>void action('retry')}>仅重试写作</button>}{['queued','running'].includes(job.data?.status||'')&&<button className="wb-button" disabled={!writable||busy} onClick={()=>void action('cancel')}>取消写作</button>}{job.data?.stored_response_available&&job.data.status==='failed'&&<button className="wb-button" disabled={!writable||busy} onClick={()=>void action('revalidate')}>本机重新校验（不调用模型）</button>}</div><TaskRelations key={selected} jobId={selected} kind={job.data?.task_kind} onOpen={onReport}/>{job.data?.error&&<p role="alert" className="wb-error">{job.data.error}</p>}<SourceArchive key={selected} jobId={selected} embedded/>{job.data?.source?.execution&&<p className="wb-hint">{job.data.source.execution.agent.name}{job.data.source.execution.agent.version?` v${job.data.source.execution.agent.version}`:''} · {job.data.source.execution.skills.map(s=>s.name+' v'+s.version).join('、')||'未选 Skill'}</p>}{job.data?.report?.rewrites.map((r,i)=><article className="remix-section" key={i}><h3>{r.title_options[0].title}</h3><p style={{whiteSpace:'pre-wrap'}}>{r.body}</p>{r.script.length>0&&<details><summary>口播与分段脚本</summary><p>{r.hook}</p><ol>{r.script.map((line,n)=><li key={n}>{line}</li>)}</ol></details>}<button className="wb-button primary" disabled={!writable||busy} onClick={()=>void adopt(i,r)}>选用并编辑此稿</button></article>)}</section>}
 </div>
}
