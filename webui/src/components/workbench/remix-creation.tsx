import {useEffect, useRef, useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import api from '@/lib/api'
import {errorMessage} from '@/lib/workbench'
import {RecoveryNotice,useEditorRecovery} from './editor-recovery'
import {useCreatorProfiles} from './remix-learning'

export type Brief = {platform:string;content_type:string;audience:string;objective:string;topic:string;framework:string;tone:string;must_keep:string;avoid:string;supplemental:string}
export const defaultBrief: Brief = {platform:'xhs',content_type:'图文',audience:'',objective:'分享',topic:'',framework:'auto',tone:'',must_keep:'',avoid:'',supplemental:''}
export const frameworks = ['需求解答','决策对比','真实经历','信息筛选']
export type Rewrite = {angle:string;framework:string;title_options:{title:string;reason:string}[];body:string;hook:string;script:string[];cover_text:string;visuals:string[];tags:string[];fact_checks:string[];change_notes:string[];evidence_ids:string[]}
type Version = {id:string;rewrite_index:number;title:string;content:string;feedback:string;adopted:boolean;created_at:string;original_title:string;original_content:string}
type Evidence = {id:string;kind:string;text:string}
type Entry = {profile_id?:string;id?:string;kind:string;title:string;content:string;platform:string;topic:string;reason:string;enabled:boolean;version:number;source_job_id:string;source_version_id:string}
const emptyEntry:Entry={profile_id:'',kind:'preference',title:'',content:'',platform:'all',topic:'',reason:'',enabled:true,version:1,source_job_id:'',source_version_id:''}
const platformNames:Record<string,string>={all:'全部平台',xhs:'小红书',douyin:'抖音',bilibili:'哔哩哔哩',other:'其他'}

export function BriefForm({value,onChange,defaultOpen=true}:{value:Brief;onChange:(value:Brief)=>void;defaultOpen?:boolean}) {
  const field=(key:keyof Brief,text:string,placeholder='')=><label>{text}<input maxLength={key==='must_keep'?1000:key==='topic'?100:300} value={value[key]} placeholder={placeholder} onChange={e=>onChange({...value,[key]:e.target.value})}/></label>
  return <details open={defaultOpen}><summary>创作需求与改写框架</summary><div className="remix-form-grid">
    <label>目标平台<select aria-label="目标平台" value={value.platform} onChange={e=>onChange({...value,platform:e.target.value})}>{Object.entries(platformNames).filter(([k])=>k!=='all').map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <label>输出形式<select aria-label="输出形式" value={value.content_type} onChange={e=>onChange({...value,content_type:e.target.value})}><option>图文</option><option>视频</option></select></label>
    <label>创作目的<select aria-label="创作目的" value={value.objective} onChange={e=>onChange({...value,objective:e.target.value})}>{['分享','收藏','咨询','转化'].map(x=><option key={x}>{x}</option>)}</select></label>
    <label>改写框架<select aria-label="改写框架" value={value.framework} onChange={e=>onChange({...value,framework:e.target.value})}><option value="auto">自动推荐</option>{frameworks.map(x=><option key={x}>{x}</option>)}</select></label>
    {field('audience','目标受众','例如：准备长期旅居、重视生活便利的人')}{field('topic','题材标签','与经验库的题材标签一致时会优先复用')}
    {field('tone','表达语气','例如：直接、客观、有生活细节')}{field('avoid','禁用表达','例如：全网最低、亲测、必火')}
  </div><label>必须保留的事实<input maxLength={1000} value={value.must_keep} placeholder="填写有来源的事实或希望保留的原文信息" onChange={e=>onChange({...value,must_keep:e.target.value})}/></label>
    <label>补充材料<textarea aria-label="补充材料" rows={4} maxLength={3000} value={value.supplemental} placeholder="补充价格口径、真实经历或其他背景。补充资料会标记为用户提供。" onChange={e=>onChange({...value,supplemental:e.target.value})}/></label>
    <p className="wb-hint">框架用于组织表达；缺少事实时保留待核实项。自动推荐会给出理由，你可以指定框架后重新生成。</p>
  </details>
}

export function EvidenceCoverage({evidence}:{evidence:Evidence[]}) {
  const entry=evidence.find(e=>e.kind==='coverage')
  if(!entry)return <p className="wb-hint">历史报告使用旧版输入，重新提交素材可生成含评论和完整改写的新报告。</p>
  try {
    const coverage=JSON.parse(entry.text) as {body_chars:number;body_original_chars:number;source_body_previously_truncated:boolean;transcript_chars:number;transcript_original_chars:number;comments:{enabled?:boolean;saved_total?:number;manual_submitted?:number;sampled?:number;included?:number;policy?:string};used_research?:{id:string;name:string;assets:number}[];used_knowledge:{id:string;version:number;title:string;profile_id?:string}[]}
    const c=coverage.comments
    return <details><summary>本次分析范围与写作经验</summary><p>正文 {coverage.body_chars}/{coverage.body_original_chars} 字 · 语音 {coverage.transcript_chars}/{coverage.transcript_original_chars} 字{coverage.source_body_previously_truncated?' · 原始正文已截断':''}。本次未分析封面和画面。</p>
      <p>{c.enabled?`评论：本机已保存 ${c.saved_total??0} 条，手动补充 ${c.manual_submitted??0} 条，取样去重后本次纳入 ${c.included??0} 条。`:'未启用评论分析。'}评论样本仅代表本次纳入内容；互动计数的“评论数”不等于已保存评论数。</p>{c.enabled&&<p>{c.policy}</p>}
      {coverage.used_knowledge.length?<><p>本次复用了以下确认过的经验，后续修改经验不会改变本次快照：</p><ul>{coverage.used_knowledge.map(k=><li key={k.id}>{k.title} · v{k.version} · {k.profile_id?'账号经验':'通用经验'}</li>)}</ul></>:<p>本次没有匹配的写作经验。可在经验库保存通用偏好，或使用相同平台与题材标签。</p>}{!!coverage.used_research?.length&&<><p>本次选用的项目研究快照（候选解释与来源声明待核实）：</p>{coverage.used_research.map(r=><p key={r.id}>{r.name} · {r.id.slice(0,8)} · {r.assets} 项资产</p>)}</>}
    </details>
  }catch{return null}
}

export function RewriteResults({jobId,rewrites,framework,reason,writable,brief,profileId='',onCite}:{jobId:string;rewrites:Rewrite[];framework:string;reason:string;writable:boolean;brief?:Partial<Brief>;profileId?:string;onCite:(ids:string[])=>React.ReactNode}) {
  const cache=useQueryClient()
  const [index,setIndex]=useState(0);const [titleIndex,setTitleIndex]=useState(0)
  const [title,setTitle]=useState(rewrites[0]?.title_options[0]?.title||'');const [content,setContent]=useState(rewrites[0]?.body||'');const [feedback,setFeedback]=useState('')
  const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false)
  const key=useRef<{fingerprint:string;key:string}|null>(null)
  const versions=useQuery({queryKey:['remix-versions',jobId],queryFn:async()=>(await api.get<Version[]>(`/remix/jobs/${jobId}/versions`)).data})
  const rewrite=rewrites[index]
  const recovery=useEditorRecovery('rewrite',jobId,{index,titleIndex,title,content,feedback},v=>{setIndex(v.index);setTitleIndex(v.titleIndex);setTitle(v.title);setContent(v.content);setFeedback(v.feedback)})
  const run=async(action:()=>Promise<void>)=>{setBusy(true);setNotice('');try{await action()}catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}}
  const save=()=>run(async()=>{
    const data={rewrite_index:index,title_option:titleIndex,title,content,feedback};const fingerprint=JSON.stringify(data)
    if(key.current?.fingerprint!==fingerprint)key.current={fingerprint,key:crypto.randomUUID()}
    await api.post(`/remix/jobs/${jobId}/versions`,{...data,request_key:key.current.key})
    await cache.invalidateQueries({queryKey:['remix-versions',jobId]});setNotice('已保存新版本，原始生成稿保持可查。')
  })
  const adopt=(v:Version)=>run(async()=>{await api.post(`/remix/jobs/${jobId}/versions/${v.id}/adopt`);await cache.invalidateQueries({queryKey:['remix-versions',jobId]});await cache.invalidateQueries({queryKey:['workbenchDrafts']});setNotice('已采用此版本并保存到草稿。重复采用不会覆盖草稿中的人工编辑。')})
  const learn=(v:Version)=>run(async()=>{
    await api.post('/remix/knowledge',{...emptyEntry,kind:'edit',title:('修改经验：'+v.title).slice(0,100),content:v.feedback,platform:brief?.platform||'xhs',topic:brief?.topic||'',profile_id:profileId,source_job_id:jobId,source_version_id:v.id})
    await cache.invalidateQueries({queryKey:['remix-knowledge']});setNotice('已将你的修改反馈确认为经验；可在写作经验库编辑、停用或删除。')
  })
  if(!rewrite)return null
  return <section className="remix-section"><h3>完整改写稿</h3><RecoveryNotice recovery={recovery}/><p>推荐框架：<strong>{framework}</strong> · {reason}</p>
    <div className="an-actions">{rewrites.map((r,i)=><button key={i} className="wb-button" aria-pressed={index===i} onClick={()=>{setIndex(i);setTitleIndex(0);setTitle(r.title_options[0].title);setContent(r.body);setFeedback('')}}>方案 {i+1} · {r.angle}</button>)}</div>
    <p>当前框架：{rewrite.framework}</p>{onCite(rewrite.evidence_ids)}
    <div className="remix-title-options">{rewrite.title_options.map((t,i)=><button key={i} className="remix-job" aria-pressed={titleIndex===i} onClick={()=>{setTitleIndex(i);setTitle(t.title)}}><span>{t.title}<small>{t.reason}</small></span></button>)}</div>
    <fieldset disabled={!writable||busy}><label>成稿标题<input maxLength={200} value={title} onChange={e=>setTitle(e.target.value)}/></label>
      <label>改写正文<textarea aria-label="改写正文" rows={12} maxLength={8000} value={content} onChange={e=>setContent(e.target.value)}/></label>
      <label>修改反馈<textarea aria-label="修改反馈" rows={3} maxLength={1500} value={feedback} placeholder="说明你改了什么，以及以后希望如何表达。保存后可单独确认为写作经验。" onChange={e=>setFeedback(e.target.value)}/></label>
      <button className="wb-button primary" disabled={!title.trim()||!content.trim()} onClick={()=>void save()}>保存成稿版本</button>
    </fieldset>
    <details><summary>封面、脚本与修改说明</summary><p>封面文案：{rewrite.cover_text}</p>{rewrite.hook&&<p>开头口播：{rewrite.hook}</p>}{!!rewrite.script.length&&<><h4>分段脚本</h4><ol>{rewrite.script.map((s,i)=><li key={i}>{s}</li>)}</ol></>}
      <h4>配图／镜头建议</h4><ul>{rewrite.visuals.map((s,i)=><li key={i}>{s}</li>)}</ul><p>建议标签：{rewrite.tags.join(' · ')||'无'}</p>
      <h4>待核实事项</h4>{rewrite.fact_checks.length?<ul>{rewrite.fact_checks.map((s,i)=><li key={i}>{s}</li>)}</ul>:<p>模型未列出额外事项，采用前仍需人工核对事实。</p>}<h4>修改说明</h4><ul>{rewrite.change_notes.map((s,i)=><li key={i}>{s}</li>)}</ul>
    </details>
    <h4>已保存版本与采用稿</h4>{versions.isError&&<p role="alert">成稿版本读取失败。<button className="wb-button" onClick={()=>void versions.refetch()}>重试版本</button></p>}
    {!versions.data?.length&&<p className="wb-hint">编辑后保存版本，再选择采用并保存到草稿。</p>}{versions.data?.map(v=><details key={v.id}><summary>{v.adopted?'已采用 · ':''}{v.title} · {new Date(v.created_at).toLocaleString('zh-CN')}</summary><p className="remix-copy">{v.content}</p><p>修改反馈：{v.feedback||'未填写'}</p>
      <details><summary>对照原始生成稿</summary><p>{v.original_title}</p><p className="remix-copy">{v.original_content}</p></details>
      <div className="an-actions"><button className="wb-button" disabled={!writable||busy} onClick={()=>void adopt(v)}>采用并保存到草稿</button><button className="wb-button" disabled={!writable||busy||!v.feedback.trim()} onClick={()=>void learn(v)}>确认修改经验</button></div>
    </details>)}
    {notice&&<p role="status">{notice}</p>}
  </section>
}

export function KnowledgePanel({writable,profileId=''}:{writable:boolean;profileId?:string}) {
  const cache=useQueryClient();const profiles=useCreatorProfiles();const [page,setPage]=useState(1)
  const [entry,setEntry]=useState<Entry>({...emptyEntry});const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false)
  const [history,setHistory]=useState<Entry[]>([])
  const entries=useQuery({queryKey:['remix-knowledge',page],queryFn:async()=>(await api.get<{items:Entry[];total:number}>('/remix/knowledge',{params:{page}})).data})
  const mutate=async(action:()=>Promise<unknown>)=>{setBusy(true);setNotice('');try{await action();await cache.invalidateQueries({queryKey:['remix-knowledge']});setNotice('经验库已更新；新任务会按平台、题材使用已启用的经验。')}catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}}
  useEffect(()=>{setEntry({...emptyEntry,profile_id:profileId});setHistory([])},[profileId])
  const payload=(e:Entry)=>({profile_id:e.profile_id||'',kind:e.kind,title:e.title,content:e.content,platform:e.platform,topic:e.topic,reason:e.reason,enabled:e.enabled,version:e.version,source_job_id:e.source_job_id,source_version_id:e.source_version_id})
  const edit=(e:Entry)=>{setEntry({profile_id:e.profile_id||'',id:e.id,kind:e.kind,title:e.title,content:e.content,platform:e.platform,topic:e.topic,reason:e.reason,enabled:e.enabled,version:e.version,source_job_id:e.source_job_id,source_version_id:e.source_version_id});setHistory([])}
  const save=()=>mutate(async()=>{if(entry.id)await api.put('/remix/knowledge/'+entry.id,payload(entry));else await api.post('/remix/knowledge',payload(entry));setEntry({...emptyEntry,profile_id:profileId});setHistory([])})
  return <section className="wb-card remix-card"><details><summary>写作经验库 · 偏好、修改经验与确认规则</summary><p>仅复用你确认并启用的经验，优先匹配平台及相同题材标签，每次最多六条、每条最多800字。单次反馈不会自动升级为通用规则。</p>
    <fieldset disabled={!writable||busy}><label>经验账号作用域<select aria-label="经验账号作用域" value={entry.profile_id||''} onChange={e=>setEntry({...entry,profile_id:e.target.value})}><option value="">通用经验（仅在选择通用或主动允许时使用）</option>{profiles.data?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><div className="remix-form-grid"><label>经验类型<select aria-label="经验类型" value={entry.kind} onChange={e=>setEntry({...entry,kind:e.target.value})}><option value="preference">个人偏好</option><option value="edit">修改经验</option><option value="rule">确认的写作规则</option></select></label>
      <label>适用平台<select aria-label="适用平台" value={entry.platform} onChange={e=>setEntry({...entry,platform:e.target.value})}>{Object.entries(platformNames).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
      <label>适用题材<input maxLength={100} value={entry.topic} placeholder="留空表示通用；填写后仅匹配相同标签" onChange={e=>setEntry({...entry,topic:e.target.value})}/></label>
      <label>经验标题<input maxLength={100} value={entry.title} onChange={e=>setEntry({...entry,title:e.target.value})}/></label></div>
      <label>经验内容<textarea aria-label="经验内容" rows={3} maxLength={2000} value={entry.content} onChange={e=>setEntry({...entry,content:e.target.value})}/></label>
      <label>确认理由<input maxLength={500} value={entry.reason} onChange={e=>setEntry({...entry,reason:e.target.value})}/></label>
      <label className="remix-check"><input type="checkbox" checked={entry.enabled} onChange={e=>setEntry({...entry,enabled:e.target.checked})}/>启用，允许后续分析复用</label>
      <div className="an-actions"><button className="wb-button" disabled={!entry.title.trim()||!entry.content.trim()} onClick={()=>void save()}>{entry.id?'确认修改经验':'确认并保存经验'}</button>{entry.id&&<button className="wb-button" onClick={()=>{setEntry({...emptyEntry,profile_id:profileId});setHistory([])}}>取消编辑</button>}</div>
    </fieldset>
    {entries.isLoading&&<p role="status">正在读取经验库…</p>}{entries.isError&&<p role="alert">经验库读取失败。<button className="wb-button" onClick={()=>void entries.refetch()}>重试经验库</button></p>}
    {entries.data?.items.map(e=><div key={e.id} className="remix-section"><h4>{e.title} · v{e.version} · {e.enabled?'启用':'停用'}</h4><p className="remix-copy">{e.content}</p><p className="wb-hint">{e.profile_id?(profiles.data?.find(p=>p.id===e.profile_id)?.name||'关联账号'):'通用经验'} · {platformNames[e.platform]} · {e.topic||'通用题材'} · {e.kind==='rule'?'确认规则':e.kind==='edit'?'修改经验':'个人偏好'}{e.source_job_id?' · 有来源任务':''}</p>
      <div className="an-actions"><button className="wb-button" disabled={!writable||busy} onClick={()=>edit(e)}>编辑经验</button>
        <button className="wb-button" disabled={!writable||busy} onClick={()=>void mutate(async()=>{await api.put('/remix/knowledge/'+e.id,{...payload(e),enabled:!e.enabled});if(entry.id===e.id)setEntry({...emptyEntry,profile_id:profileId})})}>{e.enabled?'停用经验':'启用经验'}</button>
        <button className="wb-button" disabled={busy} onClick={()=>void (async()=>{try{setHistory((await api.get<Entry[]>(`/remix/knowledge/${e.id}/history`)).data)}catch(err){setNotice(errorMessage(err))}})()}>查看经验历史</button>
        <button className="wb-button" disabled={!writable||busy} onClick={()=>void mutate(async()=>{await api.delete('/remix/knowledge/'+e.id);if(entry.id===e.id)setEntry({...emptyEntry,profile_id:profileId});setHistory([])})}>删除经验</button></div>
    </div>)}
    {entries.data&&!entries.data.items.length&&<p>还没有写作经验。可以先保存语气、禁用表达等个人偏好。</p>}
    {!!history.length&&<details open><summary>经验版本历史</summary>{history.map(e=><p key={e.version}>v{e.version} · {e.title}：{e.content}</p>)}</details>}
    {!!entries.data?.total&&<div className="an-actions"><span>共 {entries.data.total} 条 · 第 {page} 页</span><button className="wb-button" disabled={page===1} onClick={()=>setPage(page-1)}>上一页经验</button><button className="wb-button" disabled={page*20>=entries.data.total} onClick={()=>setPage(page+1)}>下一页经验</button></div>}
    {notice&&<p role="status">{notice}</p>}
  </details></section>
}
