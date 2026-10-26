import {useEffect,useRef,useState} from 'react'
import api from '@/lib/api'
import {useSession,canOperate} from '@/lib/session'
import {errorMessage} from '@/lib/workbench'

type Snapshot<T>={revision:number;data:T|null;updated_at:string|null}
export function useEditorRecovery<T>(kind:string,resourceId:string,value:T,apply:(value:T)=>void){
 const session=useSession();const writable=canOperate(session)
 const [loadEpoch,setLoadEpoch]=useState(0);const touched=useRef(false)
 const [candidate,setCandidate]=useState<T|null>(null);const [message,setMessage]=useState('')
 const latest=useRef(value);latest.current=value
 const applyRef=useRef(apply);applyRef.current=apply
 const baseline=useRef(JSON.stringify(value));const revision=useRef(0);const ready=useRef(false);const blocked=useRef(false)
 const retired=useRef(false);const queue=useRef(Promise.resolve());const initial=useRef(true)
 const storageKey=`mc-recovery:${session.mode}:${session.sub}:${kind}:${resourceId}`
 const endpoint=`/remix/editors/${kind}/${encodeURIComponent(resourceId)}`
 useEffect(()=>{
  let live=true;ready.current=false;initial.current=true
  let local:{data:T;revision:number}|null=null
  try{local=JSON.parse(localStorage.getItem(storageKey)||'null')}catch{/* Invalid recovery records do not replace edits. */}
  void api.get<Snapshot<T>>(endpoint).then(({data})=>{
   if(!live)return
   revision.current=data.revision
   const pending=local?.data??data.data
   if(pending!==null&&JSON.stringify(pending)!==baseline.current){setCandidate(pending);blocked.current=true;setMessage(local&&local.revision<data.revision?'另有更新的服务端恢复稿。本机编辑仍保留；恢复本机版本前请确认没有其他页面正在编辑。':'发现未完成的编辑，可恢复后继续。恢复稿不等于正式版本。')}
   else{blocked.current=false;ready.current=true;setMessage('编辑恢复已就绪')}
   setLoadEpoch(v=>v+1)
  }).catch(e=>{if(live){blocked.current=true;if(local)setCandidate(local.data);setMessage('恢复服务暂不可用，当前编辑保留在此浏览器。'+errorMessage(e))}})
  return()=>{live=false}
 },[endpoint,storageKey])
 const serialized=JSON.stringify(value)
 useEffect(()=>{
  if(!writable||retired.current)return
  if(initial.current){initial.current=false;return}
  if(serialized!==baseline.current)touched.current=true
  if(!touched.current)return
  try{localStorage.setItem(storageKey,JSON.stringify({data:value,revision:revision.current}))}catch{setMessage('浏览器恢复空间不足，请及时保存正式版本')}
  if(blocked.current||!ready.current)return
  const timer=setTimeout(()=>{
   const data=value
   queue.current=queue.current.then(async()=>{
    if(blocked.current)return
    try{
     const response=await api.put<Snapshot<T>>(endpoint,{revision:revision.current,data})
     revision.current=response.data.revision
     if(!retired.current&&JSON.stringify(latest.current)===JSON.stringify(data)){
      localStorage.setItem(storageKey,JSON.stringify({data,revision:revision.current}));setMessage('未完成编辑已保存，可跨页恢复；正式版本仍需手动保存。')
     }
    }catch(e){blocked.current=true;setMessage(errorMessage(e)+'；当前编辑保留在此浏览器，未覆盖其他页面。')}
   })
  },600)
  return()=>clearTimeout(timer)
 },[serialized,endpoint,storageKey,writable,loadEpoch])
 // Persist the final keystrokes even if unmounted before the debounce expires.
 useEffect(()=>()=>{if(writable&&ready.current&&!blocked.current&&JSON.stringify(latest.current)!==baseline.current){try{localStorage.setItem(storageKey,JSON.stringify({data:latest.current,revision:revision.current}))}catch{/* Preserve the server copy. */}}},[storageKey,writable])
 const restore=()=>{if(candidate!==null){applyRef.current(candidate);setCandidate(null);blocked.current=false;ready.current=true;setMessage('已恢复编辑，请核对后保存正式版本。')}}
 const discard=()=>{setCandidate(null);blocked.current=false;ready.current=true;localStorage.removeItem(storageKey);setMessage('已使用当前编辑；下一次修改将更新恢复稿。')}
 const clear=async()=>{retired.current=true;blocked.current=true;ready.current=false;baseline.current=JSON.stringify(latest.current);localStorage.removeItem(storageKey);await queue.current;try{await api.delete(endpoint,{params:{revision:revision.current}})}catch(e){setMessage(errorMessage(e))}}
 return {candidate,message,restore,discard,clear}
}
export function RecoveryNotice({recovery}:{recovery:{candidate:unknown;message:string;restore:()=>void;discard:()=>void}}){
 return <aside className="remix-citation" aria-label="编辑恢复"><p role="status">{recovery.message||'正在读取恢复草稿…'}</p>{recovery.candidate!==null&&<div className="an-actions"><button className="wb-button" onClick={recovery.restore}>恢复未完成编辑</button><button className="wb-button" onClick={recovery.discard}>使用当前内容</button></div>}</aside>
}
