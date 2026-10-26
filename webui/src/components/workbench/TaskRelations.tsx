import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import api from '@/lib/api'

type Related={id:string;title:string;status:string;task_kind?:string;attempts?:number}
type Relations={parent?:Related;dependency?:{job_id:string;title:string;result_sha256:string};snapshot_matches_current?:boolean;children:{items:Related[];total:number}}
export function TaskRelations({jobId,kind,onOpen}:{jobId:string;kind?:string;onOpen:(id:string)=>void}) {
 const [page,setPage]=useState(1)
 const query=useQuery({queryKey:['task-relations',jobId,page],queryFn:async()=>(await api.get<Relations>(`/remix/jobs/${jobId}/relations`,{params:{page}})).data,refetchInterval:q=>q.state.data?.children.items.some(j=>['queued','running'].includes(j.status))?2000:false})
 const data=query.data
 return <section className="task-relations" aria-label="分析与写作关联"><p className="wb-hint">{kind==='analysis'?'独立分析任务：本次只生成分析。后续写作单独排队，沿用本次保存的报告与来源。':kind==='writing'?'独立写作任务：只生成稿件，重试不会重新分析或转写。':'历史任务：保留原有报告与稿件，可用已有结果新建独立写作。'}</p>
  {query.isError&&<p role="alert">关联记录读取失败。<button onClick={()=>void query.refetch()}>重试关联</button></p>}
  {data?.dependency&&<p>复用结果：{data.dependency.title} · 来源版本 {data.dependency.result_sha256.slice(0,12)} {data.parent&&<button className="wb-button" onClick={()=>onOpen(data.parent!.id)}>打开来源任务</button>}{data.snapshot_matches_current===false&&<span>原任务后来有变更；本次继续使用已冻结版本。</span>}</p>}
  {!!data?.children.total&&<><h3>基于此结果的写作任务</h3><div className="remix-job-list">{data.children.items.map(j=><button className="remix-job" key={j.id} onClick={()=>onOpen(j.id)}><span>{j.title}<small>独立任务 · 已尝试 {j.attempts||1} 次</small></span><strong>{{completed:'已完成',running:'写作中',queued:'排队中',failed:'失败',cancelled:'已取消',interrupted:'已中断'}[j.status]||j.status}</strong></button>)}</div><div className="an-actions"><button className="wb-button" disabled={page===1} onClick={()=>setPage(page-1)}>上一页写作</button><span>共 {data.children.total} 个写作任务</span><button className="wb-button" disabled={page*20>=data.children.total} onClick={()=>setPage(page+1)}>下一页写作</button></div></>}
 </section>
}
