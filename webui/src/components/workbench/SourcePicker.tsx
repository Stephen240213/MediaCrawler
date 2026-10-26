import {useEffect,useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import api from '@/lib/api'
import {errorMessage,type Task,workbenchApi} from '@/lib/workbench'

type Row={content_id:string;title:string;author:string;keyword:string;content_type:string;likes:number|null;comments:number|null}
type Choices={items:Row[];total:number;page:number;scope?:string}
export function SourcePicker({value,onChange,disabled=false,initialTask='',keywords=[]}:{value:string[];onChange:(ids:string[])=>void;disabled?:boolean;initialTask?:string;keywords?:string[]}){
 const [search,setSearch]=useState('');const [keyword,setKeyword]=useState('');const [task,setTask]=useState(initialTask);const [page,setPage]=useState(1)
 const tasks=useQuery({queryKey:['workbenchTasks'],queryFn:workbenchApi.tasks})
 const sources=useQuery({queryKey:['research-choices',search,keyword,task,page],queryFn:async()=>(await api.get<Choices>('/remix/research/source-choices',{params:{search,keyword,task_id:task,page}})).data,retry:false})
 useEffect(()=>setPage(1),[search,keyword,task])
 const toggle=(id:string)=>onChange(value.includes(id)?value.filter(v=>v!==id):[...value,id])
 const addPage=()=>onChange([...new Set([...value,...(sources.data?.items.map(s=>s.content_id)||[])])].slice(0,200))
 return <div className="wb-source-picker" aria-label="直接选择资料"><div className="wb-picker-toolbar"><label>搜索资料<input aria-label="搜索研究资料" value={search} onChange={e=>setSearch(e.target.value)} placeholder="标题、作者或内容 ID"/></label><label>来源关键词<select aria-label="筛选来源关键词" value={keyword} onChange={e=>setKeyword(e.target.value)}><option value="">全部关键词</option>{keywords.map(k=><option key={k}>{k}</option>)}</select></label><label>采集批次<select aria-label="筛选采集批次" value={task} onChange={e=>setTask(e.target.value)}><option value="">所有批次</option>{task&&!tasks.data?.some(t=>t.id===task)&&<option value={task}>已保存的批次范围（历史记录不可见）</option>}{tasks.data?.filter((t:Task)=>t.platform==='xhs').map(t=><option key={t.id} value={t.id}>{t.target||'采集任务'} · {new Date(t.started_at).toLocaleString()}</option>)}</select></label></div>
 <div className="wb-picker-selection"><span>已选 <strong>{value.length}</strong> / 200 条 · 找到 {sources.data?.total||0} 条</span><button type="button" className="wb-button" disabled={disabled} onClick={addPage}>选择本页</button><button type="button" className="wb-button" disabled={disabled||!value.length} onClick={()=>onChange([])}>清空选择</button></div>
 {sources.data?.scope&&<p className="wb-hint">{sources.data.scope}</p>}{sources.isError&&<p role="alert">{errorMessage(sources.error)}</p>}
 <div className="wb-picker-rows">{sources.data?.items.map(s=><label className={`wb-picker-row ${value.includes(s.content_id)?'selected':''}`} key={s.content_id}><input type="checkbox" aria-label={`选择资料 ${s.title||s.content_id}`} disabled={disabled||value.length>=200&&!value.includes(s.content_id)} checked={value.includes(s.content_id)} onChange={()=>toggle(s.content_id)}/><span><strong>{s.title||'未命名内容'}</strong><small>{s.author||'作者未知'} · {s.keyword||'无来源关键词'} · {s.content_type||'类型未知'}</small></span><span className="wb-picker-metrics">赞 {s.likes??'—'}<small>评论 {s.comments??'—'}</small></span></label>)}</div>
 <div className="an-actions"><button type="button" className="wb-button" disabled={page===1} onClick={()=>setPage(page-1)}>上一页资料</button><span>第 {page} 页</span><button type="button" className="wb-button" disabled={page*20>=(sources.data?.total||0)} onClick={()=>setPage(page+1)}>下一页资料</button></div>
 </div>
}
