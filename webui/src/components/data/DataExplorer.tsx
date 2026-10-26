import {useMemo,useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {FolderOpen,RefreshCw,FileJson,FileSpreadsheet,Download,Eye,Search,X} from 'lucide-react'
import {dataApi} from '@/lib/api'
import {formatFileSize,formatDateTime} from '@/lib/utils'
import {errorMessage} from '@/lib/workbench'
import type {DataFile} from '@/types/crawler'
import api from '@/lib/api'
import {useSession,canOperate} from '@/lib/session'

function category(file:DataFile){
 if(file.path.includes('/redfox/'))return 'MCP 结果'
 if(file.path.includes('/search_metadata/'))return '搜索记录'
 if(/comment/i.test(file.name))return '评论数据'
 if(/creator/i.test(file.name))return '作者数据'
 return '内容数据'
}
export function DataExplorer(){
 const writable=canOperate(useSession())
 const [syncing,setSyncing]=useState('');const [syncMessage,setSyncMessage]=useState('')
 const [parents,setParents]=useState<Record<string,string>>({})
 const [filter,setFilter]=useState('全部文件');const [search,setSearch]=useState('');const [page,setPage]=useState(1);const [preview,setPreview]=useState<DataFile|null>(null)
 const files=useQuery({queryKey:['dataFiles'],queryFn:async()=>(await dataApi.getFiles()).data.files})
 const data=files.data||[]
 const syncMcp=async(file:DataFile)=>{
  setSyncing(file.path);setSyncMessage('')
  try{const {data:report}=await api.post<{contents:number;comments:number;rejected:number;replayed:boolean;warnings:string[]}>('/crawler/redfox/import',{path:file.path,...(parents[file.path]?.trim()?{parent_content_id:parents[file.path].trim()}:{})});setSyncMessage(`${report.replayed?'已同步过，未重复导入':'同步完成'}：内容 ${report.contents} 条 · 评论 ${report.comments} 条 · 拒绝 ${report.rejected} 条。${report.warnings.join(' ')}`);await files.refetch()}
  catch(error){setSyncMessage(errorMessage(error));await files.refetch()}
  finally{setSyncing('')}
 }
 const categories=useMemo(()=>['全部文件',...new Set(data.map(category))],[data])
 const selected=data.filter(f=>(filter==='全部文件'||category(f)===filter)&&(`${f.name} ${f.path}`.toLowerCase().includes(search.toLowerCase())))
 const content=useQuery({queryKey:['dataFilePreview',preview?.path],enabled:!!preview,queryFn:async()=>(await dataApi.getFileContent(preview!.path,20)).data,retry:false})
 const title=(file:DataFile)=>file.display_name||(category(file)==='搜索记录'?'网页搜索记录':/^(search_|detail_|creator_|contents|comments|[a-f0-9]{16,})/i.test(file.name)?category(file)+' · '+formatDateTime(file.modified_at):file.name.replace(/\.[^.]+$/,'').replace(/_/g,' '))
 return <div className="wb-file-explorer"><div className="wb-files-heading"><div><span className="wb-eyebrow">LOCAL EXPORTS</span><h2>数据文件</h2><p>集中预览与导出；数据库中的内容可在上方资料库直接选择分析。</p></div><button className="wb-button" disabled={files.isFetching} onClick={()=>void files.refetch()}><RefreshCw size={15}/>重新扫描</button></div>
 <div className="wb-files-summary"><div><strong>{data.length}</strong><span>本机文件</span></div><div><strong>{formatFileSize(data.reduce((n,f)=>n+f.size,0))}</strong><span>文件总量</span></div><div><strong>{new Set(data.map(f=>f.type)).size}</strong><span>保存格式</span></div></div>
 <div className="wb-files-toolbar"><div className="wb-files-tabs">{categories.map(c=><button key={c} aria-pressed={filter===c} onClick={()=>{setFilter(c);setPage(1)}}>{c}<span>{c==='全部文件'?data.length:data.filter(f=>category(f)===c).length}</span></button>)}</div><label className="wb-files-search"><Search size={16}/><input aria-label="搜索数据文件" placeholder="搜索文件名或路径" value={search} onChange={e=>{setSearch(e.target.value);setPage(1)}}/></label></div>
 {syncMessage&&<p role="status" className="wb-hint">{syncMessage}</p>}{files.isError&&<p className="wb-error" role="alert">文件列表读取失败，请重新扫描。</p>}{!selected.length&&<div className="wb-empty"><FolderOpen/><h3>{files.isLoading?'正在读取文件…':'没有匹配的文件'}</h3><p>调整搜索条件，或先采集并导出数据。</p></div>}
 <div className="wb-file-grid">{selected.slice((page-1)*12,page*12).map(file=>{const Icon=['xls','xlsx','csv'].includes(file.type)?FileSpreadsheet:FileJson;return <article className="wb-export-card" key={file.path}><div className="wb-export-card-top"><span className="wb-export-icon"><Icon size={22}/></span><span className="wb-file-format">{file.type.toUpperCase()}</span></div><h3 title={file.name}>{title(file)}</h3><p className="wb-export-path" title={file.path}>{file.path}</p><div className="wb-export-meta"><span>{formatFileSize(file.size)}</span><span>{file.record_count!=null?`${file.record_count} 条记录`:category(file)}</span></div><p className="wb-export-time">{formatDateTime(file.modified_at)}</p><div>{category(file)==='MCP 结果'&&file.display_name?.includes('comments')&&<label>关联内容 ID（评论可选）<input value={parents[file.path]||''} maxLength={200} placeholder="历史评论缺少所属内容时填写" onChange={e=>setParents({...parents,[file.path]:e.target.value})}/></label>}</div><div className="wb-export-actions">{category(file)==='MCP 结果'&&<button className="wb-button" disabled={!writable||!!syncing} onClick={()=>void syncMcp(file)}>{syncing===file.path?'同步中…':'同步到分析'}</button>}<button className="wb-button" onClick={()=>setPreview(file)}><Eye size={15}/>预览</button><a className="wb-button" href={dataApi.getDownloadUrl(file.path.split('/').map(encodeURIComponent).join('/'))} target="_blank" rel="noreferrer"><Download size={15}/>下载</a></div></article>})}</div>
 <div className="an-actions"><span>{selected.length} 个文件 · 第 {page} 页</span><button className="wb-button" disabled={page===1} onClick={()=>setPage(page-1)}>上一页文件</button><button className="wb-button" disabled={page*12>=selected.length} onClick={()=>setPage(page+1)}>下一页文件</button></div>
 {preview&&<section className="wb-file-preview" aria-label="数据文件预览"><div className="wb-files-heading"><h3>{preview.name}</h3><button className="wb-button" onClick={()=>setPreview(null)}><X size={15}/>关闭预览</button></div>{content.isLoading?<p>正在读取前20条…</p>:content.isError?<p role="alert">{errorMessage(content.error)}</p>:<pre>{JSON.stringify(content.data,null,2)}</pre>}</section>}
 </div>
}
