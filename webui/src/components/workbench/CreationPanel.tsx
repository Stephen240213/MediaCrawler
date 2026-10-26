import {SourceArchive} from './SourceArchive'
import {useEffect,useRef,useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import api from '@/lib/api'
import {canOperate,useSession} from '@/lib/session'
import {errorMessage} from '@/lib/workbench'
import {BriefForm,defaultBrief,type Rewrite} from './remix-creation'

type Choice={id:string;name:string;description?:string;version?:number}
export type CreationRequest={source:'report'|'analytics'|'research';referenceId?:string;filters?:Record<string,string>;sequence:number}
type Job={id:string;title:string;status:string;error?:string;stage:string;created_at:string;report?:{rewrites:Rewrite[]};source?:{provenance?:Record<string,unknown>;execution?:{agent:Choice;skills:Choice[]}}}
const states:Record<string,string>={queued:'排队中',running:'生成中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'}
export function CreationPanel({request,onDraft,onReport,active}:{active:boolean;request?:CreationRequest|null;onDraft:(id:string)=>void;onReport:(id:string)=>void}) {
 const writable=canOperate(useSession()),cache=useQueryClient()
 const [source,setSource]=useState('manual'),[reference,setReference]=useState(''),[material,setMaterial]=useState(''),[title,setTitle]=useState('')
 const [filters,setFilters]=useState<Record<string,string>>({}),[agent,setAgent]=useState('content_writer'),[skills,setSkills]=useState<string[]>(['curate'])
 const [brief,setBrief]=useState({...defaultBrief}),[consent,setConsent]=useState(false),[busy,setBusy]=useState(false),[notice,setNotice]=useState('')
 const [selected,setSelected]=useState(''),[page,setPage]=useState(1),[space,setSpace]=useState('')
 const key=useRef<{input:string;key:string}|null>(null)
 const catalog=useQuery({queryKey:['creation-catalog'],queryFn:async()=>(await api.get<{agents:Choice[];skills:Choice[];model:{model:string;model_ready:boolean;provider_host:string}}>('/remix/creation/catalog')).data,enabled:active})
 const jobs=useQuery({queryKey:['remix-jobs',page],queryFn:async()=>(await api.get<{items:Job[];total:number}>('/remix/jobs',{params:{page}})).data,enabled:active})
 const spaces=useQuery({queryKey:['research-spaces'],queryFn:async()=>(await api.get<(Choice&{name:string})[]>('/remix/research/spaces')).data,enabled:active&&source==='research'})
 const research=useQuery({queryKey:['creation-revisions',space],queryFn:async()=>(await api.get<{revisions:{id:string;created_at:string;status:string}[]}>(`/remix/research/spaces/${space}`)).data,enabled:active&&source==='research'&&!!space})
 const job=useQuery({queryKey:['remix-job',selected],queryFn:async()=>(await api.get<Job>('/remix/jobs/'+selected)).data,enabled:active&&!!selected,refetchInterval:q=>['queued','running'].includes(q.state.data?.status||'')?1500:false})
 useEffect(()=>{if(request){setSource(request.source);setReference(request.referenceId||'');setFilters(request.filters||{});setConsent(false);setSelected('');setNotice('')}},[request?.sequence])
 useEffect(()=>setConsent(false),[source,reference,material,title,filters,agent,skills,brief])
 const submit=async()=>{
  if(!consent||busy||!writable)return
  setBusy(true);setNotice('')
  try {
   const input={source,reference_id:reference,material:source==='manual'?material:'',title,analytics_filters:source==='analytics'?filters:{},agent_id:agent,skill_ids:skills,brief,include_comments:false,consent}
   const fingerprint=JSON.stringify(input)
   if(key.current?.input!==fingerprint)key.current={input:fingerprint,key:crypto.randomUUID()}
   const result=await api.post<Job>('/remix/jobs',{...input,request_key:key.current.key})
   key.current=null;setSelected(result.data.id);setConsent(false);setNotice('创作任务已提交，完成后在下方查看并选用草稿。');await cache.invalidateQueries({queryKey:['remix-jobs']})
  }catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}
 }
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
   <fieldset disabled={!writable||busy}><label>创作来源<select aria-label="创作来源" value={source} onChange={e=>{setSource(e.target.value);setReference('')}}><option value="manual">自己填写</option><option value="report">AI 内容分析结果</option><option value="research">账号 / 专题研究结果</option><option value="analytics">数据分析结果</option></select></label>
   {source==='manual'&&<label>创作材料<textarea aria-label="创作材料" rows={5} maxLength={18000} value={material} onChange={e=>setMaterial(e.target.value)} placeholder="填写事实、经历、参考文案或需要表达的想法…"/></label>}
   {source==='report'&&<><label>选择分析报告<select aria-label="选择分析报告" value={reference} onChange={e=>setReference(e.target.value)}><option value="">请选择已完成报告</option>{request?.source==='report'&&request.referenceId&&!jobs.data?.items.some(j=>j.id===request.referenceId)&&<option value={request.referenceId}>已带入报告 {request.referenceId.slice(0,8)}</option>}{jobs.data?.items.filter(j=>j.status==='completed').map(j=><option key={j.id} value={j.id}>{j.title} · {new Date(j.created_at).toLocaleString('zh-CN')}</option>)}</select></label><div className="an-actions"><button className="wb-button" disabled={page<=1} onClick={()=>setPage(page-1)}>上一批报告</button><span>第 {page} 页 · 共 {jobs.data?.total||0} 个任务</span><button className="wb-button" disabled={page*20>=(jobs.data?.total||0)} onClick={()=>setPage(page+1)}>下一批报告</button></div></>}
   {source==='research'&&<><label>研究范围<select aria-label="创作研究范围" value={space} onChange={e=>{setSpace(e.target.value);setReference('')}}><option value="">请选择范围</option>{spaces.data?.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>研究版本<select aria-label="创作研究版本" value={reference} onChange={e=>setReference(e.target.value)}><option value="">请选择已完成版本</option>{request?.source==='research'&&reference&&!research.data?.revisions.some(r=>r.id===reference)&&<option value={reference}>已带入版本 {reference.slice(0,8)}</option>}{research.data?.revisions.filter(r=>['completed','partial'].includes(r.status)).map(r=><option value={r.id} key={r.id}>{new Date(r.created_at).toLocaleString('zh-CN')} · {r.status==='partial'?'部分完成':'已完成'}</option>)}</select></label></>}
   {source==='analytics'&&<><label>数据平台<select aria-label="创作数据平台" value={filters.platform||''} onChange={e=>setFilters({...filters,platform:e.target.value,task_id:''})}><option value="">全部平台</option>{[['xhs','小红书'],['dy','抖音'],['twitter','X / Twitter'],['youtube','YouTube']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label><details><summary>本次数据筛选条件</summary><pre>{JSON.stringify(filters,null,2)}</pre><p>提交时按此筛选冻结全部已保存的内容正文、原始评论和完整统计，不再只传统计摘要。可从“数据分析”中按当前筛选带入。</p></details></>}
   <label>创作标题 / 任务名称（可选）<input maxLength={200} value={title} onChange={e=>setTitle(e.target.value)}/></label></fieldset>
   {(jobs.isError||spaces.isError||research.isError)&&<p role="alert" className="wb-error">来源列表读取失败，请重试。<button className="wb-button" onClick={()=>{void jobs.refetch();if(source==='research')void spaces.refetch();if(space)void research.refetch()}}>重新加载来源</button></p>}
  </section>
  <section className="wb-card remix-card"><h2>2. 选择 Agent 与 Skill</h2><p className="wb-hint">内置 Agent 调用已配置的模型；Skill 是本工作台的创作方法，可多选，版本随任务保存。</p>
   {catalog.isError&&<p role="alert">创作配置读取失败。<button className="wb-button" onClick={()=>void catalog.refetch()}>重新加载配置</button></p>}
   <fieldset disabled={!writable||busy}><label>创作 Agent<select aria-label="创作 Agent" value={agent} onChange={e=>{setAgent(e.target.value);setBrief({...brief,content_type:e.target.value==='script_writer'?'视频':'图文'})}}>{catalog.data?.agents.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label><p className="wb-hint">{catalog.data?.agents.find(a=>a.id===agent)?.description}</p>
   <div className="creation-skills" aria-label="创作 Skill">{catalog.data?.skills.map(s=><label className="remix-check" key={s.id}><input type="checkbox" checked={skills.includes(s.id)} onChange={e=>setSkills(e.target.checked?[...skills,s.id]:skills.filter(id=>id!==s.id))}/>{s.name} · v{s.version}</label>)}</div>
   <BriefForm defaultOpen={false} value={brief} onChange={value=>setBrief({...value,content_type:agent==='script_writer'?'视频':value.content_type})}/>
   <p className="wb-hint">模型：{catalog.data?.model.model||'读取中'} · 接收服务：{catalog.data?.model.provider_host||'未配置'}</p>
   <label className="remix-check"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>我同意将所选来源的完整分析、已保存正文与评论、创作需求和 Skill 发送给已配置的模型，调用可能产生费用。</label>
   <button className="wb-button primary" disabled={!consent||!catalog.data?.model.model_ready||(source==='manual'?!material.trim():source!=='analytics'&&!reference)} onClick={()=>void submit()}>{busy?'正在提交…':'调用 Agent 生成草稿'}</button></fieldset>
  </section>
  {notice&&<p role="status" className="an-notice">{notice}</p>}
  {selected&&<section className="wb-card remix-card"><div className="an-heading"><h2>3. 查看创作结果</h2><button className="wb-button" onClick={()=>onReport(selected)}>打开完整报告</button></div>{job.isError&&<p role="alert">结果读取失败。<button onClick={()=>void job.refetch()}>重试</button></p>}<p role="status">{states[job.data?.status||'']||'正在读取…'}</p>{job.data?.error&&<p role="alert" className="wb-error">{job.data.error}</p>}<SourceArchive key={selected} jobId={selected} embedded/>{job.data?.source?.execution&&<p className="wb-hint">{job.data.source.execution.agent.name} · {job.data.source.execution.skills.map(s=>s.name+' v'+s.version).join('、')||'未选 Skill'}</p>}{job.data?.report?.rewrites.map((r,i)=><article className="remix-section" key={i}><h3>{r.title_options[0].title}</h3><p style={{whiteSpace:'pre-wrap'}}>{r.body}</p>{r.script.length>0&&<details><summary>口播与分段脚本</summary><p>{r.hook}</p><ol>{r.script.map((line,n)=><li key={n}>{line}</li>)}</ol></details>}<button className="wb-button primary" disabled={!writable||busy} onClick={()=>void adopt(i,r)}>选用并编辑此稿</button></article>)}</section>}
 </div>
}
