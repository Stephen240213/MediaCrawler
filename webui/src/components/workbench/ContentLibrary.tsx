import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import api from '@/lib/api'
import {useSession,canOperate} from '@/lib/session'
import {SourcePicker} from './SourcePicker'
import {type ResearchRequest} from './ResearchWorkbench'
type Dataset={exact:boolean;scope:string;total:number;counts:Record<string,number>;unavailable:number;task:{target:string};items:{content_id:string;available:boolean}[]}
export function ContentLibrary({taskId,onResearch}:{taskId?:string;onResearch:(r:ResearchRequest)=>void}){
 const [selected,setSelected]=useState<string[]>([]);const writable=canOperate(useSession())
 const dataset=useQuery({queryKey:['collection-dataset',taskId],enabled:!!taskId,queryFn:async()=>(await api.get<Dataset>(`/workbench/tasks/${taskId}/dataset`)).data})
 const discovery=useQuery({queryKey:['research-discovery'],queryFn:async()=>(await api.get<{keywords:string[];total:number}>('/remix/research/discovery')).data})
 return <section className="wb-card wb-library"><div className="wb-card-heading"><div><h2>{taskId?'本次采集资料':'内容资料库'}</h2><p>按笔记去重保存，评论与互动来自当前本机记录。</p></div><span className="wb-badge">{dataset.data?.total??discovery.data?.total??0} 条</span></div>
 {dataset.isError&&<p role="alert">本次采集资料读取失败，请返回任务记录重试。</p>}{dataset.data&&<div className="wb-dataset-summary"><strong>{dataset.data.task.target}</strong><p>{dataset.data.scope}</p><div>{dataset.data.exact?<><span>新增 {dataset.data.counts.new||0}</span><span>复用 {dataset.data.counts.reused||0}</span><span>刷新 {dataset.data.counts.refreshed||0}</span></>:<><span>历史可追溯 {dataset.data.total} 条</span><span>新增 / 复用类型未知</span></>}<span>当前不可用 {dataset.data.unavailable}</span></div>{dataset.data.exact&&<button className="wb-button primary" disabled={!writable||dataset.data.total===0} onClick={()=>onResearch({taskId,ids:dataset.data!.items.filter(i=>i.available).map(i=>i.content_id).slice(0,200),exact:true,name:dataset.data!.task.target+' · 批次研究',sequence:Date.now()})}>分析本次采集资料</button>}</div>}
 <SourcePicker value={selected} onChange={setSelected} disabled={!writable} initialTask={taskId} keywords={discovery.data?.keywords}/><div className="wb-library-footer"><p>已选择 {selected.length} 条，创建研究后预览增量范围；此操作不会立即调用模型。</p><button className="wb-button primary" disabled={!writable||!selected.length} onClick={()=>onResearch({ids:selected,exact:false,name:(dataset.data?.task.target||'所选资料')+' · 内容研究',sequence:Date.now()})}>分析所选资料</button></div></section>
}
