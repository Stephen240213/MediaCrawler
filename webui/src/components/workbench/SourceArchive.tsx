import {useEffect,useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import api from '@/lib/api'
import {errorMessage} from '@/lib/workbench'

type RecordItem={id:string;kind:string;label:string;data:unknown}
type Records={items:RecordItem[];total:number;page:number;dataset_complete:boolean;counts?:{contents:number;comments:number};legacy_analytics:boolean}
export function SourceArchive({jobId,recordId='',embedded=false}:{jobId:string;recordId?:string;embedded?:boolean}) {
 const [open,setOpen]=useState(!!recordId),[kind,setKind]=useState(''),[page,setPage]=useState(1),[notice,setNotice]=useState('')
 useEffect(()=>{if(recordId){setOpen(true);setPage(1);setKind('')}},[recordId])
 const records=useQuery({queryKey:['source-archive',jobId,kind,page,recordId],enabled:open,queryFn:async()=>(await api.get<Records>(`/remix/jobs/${jobId}/source-records`,{params:{kind,page,record_id:recordId}})).data})
 const download=async()=>{
  setNotice('')
  try{const result=await api.get(`/remix/jobs/${jobId}/source-archive.json`,{responseType:'blob'});const url=URL.createObjectURL(result.data);const a=document.createElement('a');a.href=url;a.download='完整来源-'+jobId+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){setNotice(errorMessage(e))}
 }
 return <section className={embedded?'source-archive':'wb-card remix-card source-archive'} aria-label={recordId?'完整引用来源':'完整任务来源'}>
  <div className="an-heading"><h3>{recordId?'完整引用来源':'来源报告与原始数据'}</h3><div className="an-actions"><button className="wb-button" onClick={()=>setOpen(!open)}>{open?'收起来源':'查看完整来源'}</button><button className="wb-button" onClick={()=>void download()}>下载完整来源包</button></div></div>
  <p className="wb-hint">查看本次任务保存的完整分析、正文、评论和来源快照。模型生成结果与原始记录分开保留。</p>
  {notice&&<p role="alert">{notice}</p>}
  {open&&<>{records.isLoading&&<p role="status">正在读取完整来源…</p>}{records.isError&&<p role="alert">{errorMessage(records.error)}<button className="wb-button" onClick={()=>void records.refetch()}>重试读取来源</button></p>}
   {records.data?.dataset_complete&&<p>完整冻结：内容 {records.data.counts?.contents} 条 · 原始评论 {records.data.counts?.comments} 条</p>}
   {records.data?.legacy_analytics&&<p className="wb-error">此历史任务只保存了统计结果，没有冻结原始明细。可按原筛选重新创作以保存完整来源；当前数据库不能冒充历史快照。</p>}
   {!recordId&&<label>来源类型<select aria-label="来源类型" value={kind} onChange={e=>{setKind(e.target.value);setPage(1)}}><option value="">全部来源</option><option value="analysis">分析结果</option><option value="content">内容原文</option><option value="comment">原始评论</option><option value="evidence">引用证据</option></select></label>}
   {records.data?.items.map(item=><details key={item.id} open={!!recordId}><summary>{item.label} · {item.id}</summary><pre>{JSON.stringify(item.data,null,2)}</pre></details>)}
   {records.data?.total===0&&<p>此类型没有已保存记录。</p>}
   {!!records.data?.total&&<div className="an-actions"><span>共 {records.data.total} 项 · 第 {page} 页</span><button className="wb-button" disabled={page===1} onClick={()=>setPage(page-1)}>上一页来源</button><button className="wb-button" disabled={page*20>=records.data.total} onClick={()=>setPage(page+1)}>下一页来源</button></div>}
  </>}
 </section>
}
