import {useEffect,useRef,useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import api,{type FilePreviewResponse} from '@/lib/api'
import type {DataFile} from '@/types/crawler'
import {errorMessage} from '@/lib/workbench'

const text=(value:unknown)=>typeof value==='string'||typeof value==='number'?String(value):'—'

export function FilePreview({file,onClose}:{file:DataFile;onClose:()=>void}){
 const dialog=useRef<HTMLDialogElement>(null)
 const [raw,setRaw]=useState(false);const [page,setPage]=useState(1)
 const content=useQuery({queryKey:['dataFilePreview',file.path,raw,page],queryFn:async()=>(await api.get<FilePreviewResponse>('/data/files/'+file.path.split('/').map(encodeURIComponent).join('/'),{params:{limit:20,offset:(page-1)*20,raw}})).data,retry:false})
 useEffect(()=>{dialog.current?.showModal()},[])
 const data=content.data
 return <dialog ref={dialog} className="wb-file-dialog" aria-label="文件预览" onCancel={onClose}>
  <section className="wb-file-preview" aria-label="数据文件预览">
   <div className="wb-files-heading"><div><h2>{file.display_name||file.name}</h2><p>{file.path}</p></div><button autoFocus className="wb-button" onClick={onClose}>关闭预览</button></div>
   {file.path.includes('/redfox/')&&<div className="an-actions"><button className="wb-button" aria-pressed={!raw} onClick={()=>{setRaw(false);setPage(1)}}>内容预览</button><button className="wb-button" aria-pressed={raw} onClick={()=>{setRaw(true);setPage(1)}}>原始 JSON</button></div>}
   {content.isLoading?<p role="status">正在读取文件…</p>:content.isError?<p role="alert">{errorMessage(content.error)}</p>:data?.mcp&&!raw?<>
    <p>解析到 {data.total} 条记录 · 第 {page} 页。本地预览不会发起采集或模型调用。</p>
    {data.mcp.error&&<p className="wb-error" role="alert">{data.mcp.error}</p>}
    {data.mcp.state==='raw_only'&&<p>这是任务回执、账号信息或转写结果，请切换“原始 JSON”查看。</p>}
    {data.data.map((row,i)=><article className="wb-preview-note" key={i}><h3>{text(row.title||row.content_id||row.comment_id)}</h3>{row.preview_error?<p role="alert">{text(row.preview_error)}</p>:<><p>内容 ID：{text(row.content_id)} · 作者：{text(row.author)} · 发布时间：{text(row.published_at)}</p><p className="wb-preview-body">{text(row.body)}</p><p>点赞 {text(row.likes)} · 收藏 {text(row.favorites)} · 评论 {text(row.comments)} · 分享 {text(row.shares)}</p></>}</article>)}
    {data.total>20&&<div className="an-actions"><button className="wb-button" disabled={page===1} onClick={()=>setPage(page-1)}>上一页预览</button><span>共 {data.total} 条</span><button className="wb-button" disabled={page*20>=data.total} onClick={()=>setPage(page+1)}>下一页预览</button></div>}
   </>:<pre>{JSON.stringify(data,null,2)}</pre>}
  </section>
 </dialog>
}
