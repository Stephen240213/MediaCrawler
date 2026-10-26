import {useState} from 'react'
import {useQuery,useQueryClient} from '@tanstack/react-query'
import api from '@/lib/api'
import {errorMessage} from '@/lib/workbench'

type Definition={id:string;name:string;description?:string;instruction:string;version:number;enabled:boolean;builtin:boolean;content_type?:'图文'|'视频';content_types?:('图文'|'视频')[]}
const blank={id:'',name:'',description:'',instruction:'',version:0,enabled:true,builtin:false,content_type:'图文' as const,content_types:['图文','视频'] as ('图文'|'视频')[]}
export function CreationRegistry({writable}:{writable:boolean}) {
 const cache=useQueryClient(),[open,setOpen]=useState(false),[kind,setKind]=useState<'agent'|'skill'>('agent'),[editing,setEditing]=useState(''),[form,setForm]=useState<Definition>({...blank}),[busy,setBusy]=useState(false),[notice,setNotice]=useState('')
 const list=useQuery({queryKey:['creation-registry'],enabled:open,queryFn:async()=>(await api.get<{agents:Definition[];skills:Definition[]}>('/remix/creation/registry')).data})
 const versions=useQuery({queryKey:['creation-definition-versions',kind,editing,form.version],enabled:open&&!!editing,queryFn:async()=>(await api.get<Definition[]>(`/remix/creation/definitions/${kind}/${editing}/versions`)).data})
 const reset=()=>{setEditing('');setForm({...blank});setNotice('')}
 const save=async()=>{
  setBusy(true);setNotice('')
  try {
   const common={id:form.id,name:form.name.trim(),description:form.description||'',instruction:form.instruction.trim(),enabled:form.enabled,version:form.version}
   const payload=kind==='agent'?{...common,content_type:form.content_type}:{...common,content_types:form.content_types}
   const base=`/remix/creation/${kind==='agent'?'agents':'skills'}`
   const result=editing?await api.put<Definition>(`${base}/${editing}`,payload):await api.post<Definition>(base,payload)
   setForm(result.data);setEditing(result.data.id);setNotice(`已保存 ${result.data.name} · v${result.data.version}，已有任务继续使用原版本。`)
   await cache.invalidateQueries({queryKey:['creation-registry']});await cache.invalidateQueries({queryKey:['creation-catalog']})
  }catch(e){setNotice(errorMessage(e))}finally{setBusy(false)}
 }
 const entries=kind==='agent'?list.data?.agents:list.data?.skills
 return <details className="creation-registry" onToggle={e=>setOpen(e.currentTarget.open)}><summary>管理 Agent 与 Skill</summary><p className="wb-hint">配置是本机保存的生成指令，调用已配置模型。每次修改建立新版本，已有任务保留原配置。此处不执行脚本或外部工具。</p>
  {list.isError&&<p role="alert">配置读取失败。<button onClick={()=>void list.refetch()}>重新读取</button></p>}
  <fieldset disabled={!writable||busy}><div className="remix-form-grid"><label>配置类型<select aria-label="配置类型" value={kind} onChange={e=>{setKind(e.target.value as typeof kind);reset()}}><option value="agent">Agent</option><option value="skill">Skill</option></select></label><label>编辑已有配置<select aria-label="编辑已有配置" value={editing} onChange={e=>{const item=entries?.find(v=>v.id===e.target.value);if(item){setForm({...item});setEditing(item.id);setNotice('')}else reset()}}><option value="">新建配置</option>{entries?.filter(v=>!v.builtin).map(v=><option value={v.id} key={v.id}>{v.name} · v{v.version}{v.enabled?'':' · 已停用'}</option>)}</select></label></div>
  <label>配置编号<input aria-label="配置编号" value={form.id} disabled={!!editing} maxLength={64} placeholder="例如 travel_editor" onChange={e=>setForm({...form,id:e.target.value})}/></label><label>配置名称<input aria-label="配置名称" value={form.name} maxLength={80} onChange={e=>setForm({...form,name:e.target.value})}/></label>
  <label>配置说明<input aria-label="配置说明" value={form.description||''} maxLength={500} onChange={e=>setForm({...form,description:e.target.value})}/></label><label>执行方法<textarea aria-label="执行方法" rows={4} maxLength={5000} value={form.instruction} onChange={e=>setForm({...form,instruction:e.target.value})}/></label>
  {kind==='agent'?<label>Agent 输出类型<select aria-label="Agent 输出类型" value={form.content_type||'图文'} onChange={e=>setForm({...form,content_type:e.target.value as '图文'|'视频'})}><option>图文</option><option>视频</option></select></label>:<div aria-label="Skill 支持类型">{(['图文','视频'] as const).map(type=><label className="remix-check" key={type}><input type="checkbox" checked={form.content_types?.includes(type)||false} onChange={e=>setForm({...form,content_types:e.target.checked?[...form.content_types||[],type]:form.content_types?.filter(v=>v!==type)})}/>支持{type}</label>)}</div>}
  <label className="remix-check"><input type="checkbox" checked={form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})}/>允许新任务使用此配置</label><button className="wb-button" disabled={!/^[a-z][a-z0-9_-]{1,63}$/.test(form.id)||!form.name.trim()||!form.instruction.trim()||(kind==='skill'&&!form.content_types?.length)} onClick={()=>void save()}>{busy?'正在保存…':editing?'保存配置新版本':'保存新配置'}</button></fieldset>
  {notice&&<p role="status">{notice}</p>}
  {editing&&<details><summary>配置版本历史</summary>{versions.isError&&<p role="alert">历史读取失败。<button onClick={()=>void versions.refetch()}>重试历史</button></p>}{versions.data?.map(v=><article key={v.version}><strong>v{v.version} · {v.name} · {v.enabled?'启用':'停用'}</strong><p className="remix-copy">{v.instruction}</p></article>)}</details>}
  <details><summary>内置方法参考</summary>{entries?.filter(v=>v.builtin).map(v=><article key={v.id}><strong>{v.name} · v{v.version}</strong><p>{v.instruction}</p><button className="wb-button" disabled={!writable} onClick={()=>{setEditing('');setForm({...v,id:v.id.slice(0,58)+'_copy',version:0,builtin:false});setNotice('请使用新的编号保存自己的配置。')}}>复制为新配置</button></article>)}</details>
 </details>
}
