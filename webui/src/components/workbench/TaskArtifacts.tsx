import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import api from '@/lib/api'
import {type Rewrite} from './remix-creation'

export type ArtifactManifest={sources:{state:string;dataset?:{contents:number;comments:number}};analysis:{state:string};creation:{state:string;ready_count:number;generated_count:number};validation:{state:string;human_review:string}}
export function TaskArtifacts({jobId,manifest,failed}:{jobId:string;manifest:ArtifactManifest;failed:boolean}) {
 const [open,setOpen]=useState(false)
 const candidates=useQuery({queryKey:['draft-candidates',jobId],enabled:open&&failed,queryFn:async()=>(await api.get<{items:{index:number;rewrite:Rewrite}[]}>(`/remix/jobs/${jobId}/draft-candidates`)).data})
 return <section aria-label="任务产物状态" className="task-artifacts">
  <div className="artifact-grid"><div><small>1. 来源</small><strong>完整来源可追溯</strong>{manifest.sources.dataset&&<span>{manifest.sources.dataset.contents} 条内容 · {manifest.sources.dataset.comments} 条评论</span>}</div><div><small>2. 分析</small><strong>{manifest.analysis.state==='reused'?'复用已有分析结果':manifest.analysis.state==='not_requested'?'直接使用已选材料':manifest.analysis.state==='ready'?'分析结果可查看':'分析结果待校验'}</strong></div><div><small>3. 创作</small><strong>{manifest.creation.state==='not_requested'?'写作由独立任务执行':`${manifest.creation.ready_count} / ${manifest.creation.generated_count} 篇稿件可查看`}</strong></div><div><small>4. 校验与复核</small><strong>{manifest.validation.state==='ready'?'结构与引用校验通过':'部分产物需处理'}</strong><span>采用前仍需人工核实事实</span></div></div>
  {failed&&manifest.creation.ready_count>0&&<><p>已生成的稿件独立保留；本次完整报告仍有校验问题。可查看稿件与来源，处理后重新进行本机校验。</p><button className="wb-button" onClick={()=>setOpen(!open)}>{open?'收起待核对稿件':'查看已生成稿件（待核对）'}</button>{open&&<>{candidates.isError&&<p role="alert">稿件读取失败。<button onClick={()=>void candidates.refetch()}>重试读取</button></p>}{candidates.data?.items.map(item=><details key={item.index}><summary>方案 {item.index+1} · {item.rewrite.title_options[0].title}</summary><p className="remix-copy">{item.rewrite.body}</p><p>引用：{item.rewrite.evidence_ids.join('、')}</p></details>)}</>}</>}
 </section>
}
