import {CreationPanel,type CreationRequest} from './CreationPanel'
import {RecoveryNotice,useEditorRecovery} from './editor-recovery'
import { useSession, canOperate } from '@/lib/session'
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePenLine, Plus, Save, Download, ImagePlus, X, ArrowLeft, ArrowRight } from 'lucide-react'
import { toast } from 'sonner'
import { workbenchApi, assetUrl, errorMessage, platformNames, type Draft } from '@/lib/workbench'

const emptyDraft = (): Draft => ({id: crypto.randomUUID(), title: '', content: '', tags: [], images: [], platform: 'xhs', version: 0})
const signature = (draft: Draft) => JSON.stringify([draft.title, draft.content, draft.tags, draft.images, draft.platform])
export function DraftStudio({active,assetRequest,openRequest,creationRequest,onReport}: {active:boolean;creationRequest?:CreationRequest|null;onReport:(id:string)=>void;assetRequest: {id: string; sequence: number} | null;openRequest?:{id:string;sequence:number}|null}) {
  const [workspace,setWorkspace]=useState<'editor'|'agent'>('editor')
  useEffect(()=>{if(creationRequest)setWorkspace('agent')},[creationRequest?.sequence])
  useEffect(()=>{if(openRequest)setWorkspace('editor')},[openRequest?.sequence])
  const writable = canOperate(useSession())
  const {data: drafts = [], isError: draftsError} = useQuery({queryKey: ['workbenchDrafts'], queryFn: workbenchApi.drafts})
  const {data: assets = []} = useQuery({queryKey: ['workbenchAssets'], queryFn: () => workbenchApi.assets()})
  const client = useQueryClient()
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [saved, setSaved] = useState(() => signature(draft))
  const [saving, setSaving] = useState(false)
  const [picker, setPicker] = useState(false)
  const [previewOpen,setPreviewOpen]=useState(()=>window.matchMedia('(min-width: 1151px)').matches)
  const [slide, setSlide] = useState(0)
  const editorElement=useRef<HTMLElement|null>(null)
  const previewElement=useRef<HTMLElement|null>(null)
  useEffect(()=>{if(previewOpen&&window.matchMedia('(max-width: 1150px)').matches)previewElement.current?.scrollIntoView({block:'start'})},[previewOpen])
  const handledRequest = useRef<number | null>(null)
  const handledOpen=useRef<number|null>(null)
  const recovery=useEditorRecovery('draft','current',draft,setDraft)
  useEffect(()=>{if(openRequest&&handledOpen.current!==openRequest.sequence){const next=drafts.find(d=>d.id===openRequest.id);if(next){handledOpen.current=openRequest.sequence;openDraft(next)}}},[openRequest,drafts])
  const dirty = signature(draft) !== saved
  useEffect(() => {
    if (assetRequest && handledRequest.current !== assetRequest.sequence) {
      handledRequest.current = assetRequest.sequence
      setDraft(d => d.images.includes(assetRequest.id) ? d : {...d, images: [...d.images, assetRequest.id].slice(0,18)})
    }
  }, [assetRequest])
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {if (dirty) {e.preventDefault(); e.returnValue = ''}}
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  function openDraft(next: Draft) {
    if (saving) return
    if (dirty && !window.confirm('当前草稿有未保存的修改，确定放弃修改并切换吗？')) return
    setDraft({...next, images: [...next.images], tags: [...next.tags]}); setSaved(signature(next)); setSlide(0)
  }
  async function save() {
    setSaving(true)
    try {
      const submitted = draft
      const result = await workbenchApi.save(submitted)
      setDraft(current => ({...current, version: result.version, updated_at: result.updated_at}))
      setSaved(signature(submitted))
      await client.invalidateQueries({queryKey: ['workbenchDrafts']})
      toast.success('草稿已保存到本机')
    } catch (error) { toast.error(errorMessage(error)) }
    finally { setSaving(false) }
  }
  function exportText() {
    const blob = new Blob([`${draft.title}\n\n${draft.content}\n\n${draft.tags.map(t => '#' + t).join(' ')}`], {type: 'text/plain;charset=utf-8'})
    const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `${draft.title.trim().replace(/[\\/:*?"<>|]/g, '_') || '未命名草稿'}.txt`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  function move(index: number, offset: number) {
    setDraft(d => {const images = [...d.images]; [images[index], images[index + offset]] = [images[index + offset], images[index]]; return {...d, images}})
    setSlide(0)
  }
  const currentSlide = Math.min(slide, Math.max(0, draft.images.length - 1))
  return <><div className="wb-workspace-tabs" aria-label="创作工作区"><button className={workspace==='agent'?'selected':''} aria-pressed={workspace==='agent'} onClick={()=>setWorkspace('agent')}>AI 辅助创作</button><button className={workspace==='editor'?'selected':''} aria-pressed={workspace==='editor'} onClick={()=>setWorkspace('editor')}>草稿编辑与预览</button><span className="wb-tabs-status">选择来源 → 调用 Agent → 编辑草稿</span></div>
    <div hidden={workspace!=='agent'}><CreationPanel active={active&&workspace==='agent'} request={creationRequest} onReport={onReport} onDraft={id=>{const next=client.getQueryData<Draft[]>(['workbenchDrafts'])?.find(d=>d.id===id);if(next){openDraft(next);setWorkspace('editor')}}}/></div>
    <div className="wb-draft-workspace" hidden={workspace!=='editor'}><div className={`wb-studio ${previewOpen?'has-preview':''}`}>
    <section className="wb-card wb-draft-toolbar" aria-label="草稿工具栏"><div className="wb-draft-picker"><label>本机草稿<select aria-label="选择本机草稿" disabled={saving} value={drafts.some(d=>d.id===draft.id)?draft.id:''} onChange={e=>{const next=drafts.find(d=>d.id===e.target.value);if(next)openDraft(next)}}><option value="">{draft.version?'当前草稿':'新草稿 · 尚未保存'}</option>{drafts.map(item=><option key={item.id} value={item.id}>{item.title||'未命名草稿'} · v{item.version}</option>)}</select></label><button className="wb-button" aria-label="新建草稿" disabled={saving||!writable} onClick={()=>openDraft(emptyDraft())}><Plus size={16}/>新建</button></div><div className="wb-draft-toolbar-actions"><button className="wb-button" aria-pressed={previewOpen} onClick={()=>setPreviewOpen(!previewOpen)}><FilePenLine size={15}/>{previewOpen?'收起预览':'查看预览'}</button><button className="wb-button" onClick={exportText}><Download size={15}/>导出文案</button><button className="wb-button primary" disabled={saving||!writable} onClick={save}><Save size={15}/>{saving?'保存中…':'保存草稿'}</button></div>{draftsError&&<p role="alert" className="wb-error">草稿列表加载失败</p>}</section>
    <section ref={editorElement} className="wb-card wb-draft-editor"><div className="wb-card-heading"><span className="wb-icon-box"><FilePenLine size={19}/></span><div><h2>编辑草稿</h2><p>{dirty?'有未保存的修改':draft.version?`已保存 · 版本 ${draft.version}`:'新草稿'}</p></div><label className="wb-editor-platform">参考平台<select aria-label="参考平台" disabled={!writable} value={draft.platform} onChange={e=>setDraft({...draft,platform:e.target.value})}>{Object.entries(platformNames).map(([id,name])=><option value={id} key={id}>{name}</option>)}</select></label></div><RecoveryNotice recovery={recovery} compact/>
      <fieldset disabled={!writable} className="wb-editor-fields"><label className="wb-editor-title-field">标题<input aria-label="标题" maxLength={200} placeholder="给灵感起个标题" value={draft.title} onChange={e => setDraft({...draft, title: e.target.value})}/><small>{draft.title.length} / 200 字 · 草稿长度不代表平台发布限制</small></label><label>正文<textarea aria-label="正文" rows={12} maxLength={20000} placeholder="整理采集到的灵感，写下自己的内容…" value={draft.content} onChange={e => setDraft({...draft, content: e.target.value})}/><small>{draft.content.length} / 20000 字</small></label><label>话题标签<input placeholder="用空格或逗号分隔，例如：生活 旅行" value={draft.tags.join(' ')} onChange={e => setDraft({...draft, tags: e.target.value.replace(/#/g,'').split(/[\s,，]+/).slice(0,50)})}/></label>
      <div className="wb-editor-media-header"><strong>配图 · {draft.images.length} / 18</strong><button className="wb-button" onClick={() => setPicker(!picker)}><ImagePlus size={15}/>{picker ? '收起素材' : '选择素材'}</button></div>
      <div className="wb-draft-images">{draft.images.map((id,index) => <div key={id}><img alt={`草稿配图 ${index + 1}`} src={assetUrl(id)}/><span>{index + 1}</span><div className="wb-image-actions"><button disabled={index === 0} onClick={() => move(index,-1)} aria-label={`前移配图 ${index + 1}`}><ArrowLeft size={13}/></button><button onClick={() => setDraft({...draft,images: draft.images.filter(i => i !== id)})} aria-label={`移除配图 ${index + 1}`}><X size={13}/></button><button disabled={index === draft.images.length-1} onClick={() => move(index,1)} aria-label={`后移配图 ${index + 1}`}><ArrowRight size={13}/></button></div></div>)}</div>
      {picker && <div className="wb-image-picker">{!assets.filter(a => a.kind === 'image').length && <p className="wb-hint">暂无图片，请先在素材管理中导入。</p>}{assets.filter(a => a.kind === 'image').map(asset => <button key={asset.id} aria-label={`选择素材 ${asset.name}`} aria-pressed={draft.images.includes(asset.id)} disabled={draft.images.length >= 18 && !draft.images.includes(asset.id)} onClick={() => setDraft({...draft, images: draft.images.includes(asset.id) ? draft.images.filter(id => id !== asset.id) : [...draft.images,asset.id]})}><img src={asset.url} alt={asset.name}/>{draft.images.includes(asset.id) && <span>已选择</span>}</button>)}</div>}
      <p className="wb-editor-footer">草稿保存在本机 · 编辑后点击“保存草稿”</p></fieldset>
    </section>
    <aside ref={previewElement} className="wb-preview-column" aria-label="草稿实时预览" hidden={!previewOpen}><div className="wb-preview-label"><span className="wb-live-dot"/>实时草稿预览<span>{platformNames[draft.platform]}</span><button className="wb-button wb-preview-return" onClick={()=>{setPreviewOpen(false);editorElement.current?.scrollIntoView({block:'start'})}}>返回编辑</button></div><article className="wb-note-preview">
      {draft.images.length ? <div className="wb-note-cover"><img src={assetUrl(draft.images[currentSlide])} alt={`预览配图 ${currentSlide + 1}`}/><span>{currentSlide + 1} / {draft.images.length}</span></div> : <div className="wb-note-placeholder"><ImagePlus size={34}/><span>选择图片，预览你的内容</span></div>}
      {draft.images.length > 1 && <div className="wb-note-dots">{draft.images.map((id,index) => <button className={currentSlide === index ? 'active' : ''} onClick={() => setSlide(index)} key={id} aria-label={`查看配图 ${index + 1}`}/>)}</div>}
      <div className="wb-note-content"><div className="wb-note-author"><span>M</span><strong>本机草稿</strong><small>内容预览</small></div><h3>{draft.title || '标题将在这里呈现'}</h3><p>{draft.content || '写下正文，右侧会实时同步展示。'}</p><div className="wb-note-tags">{draft.tags.filter(Boolean).map((tag,index) => <span key={index}>#{tag} </span>)}</div></div>
    </article><p className="wb-hint">预览样式仅供参考，未连接任何发布接口。</p></aside>
  </div></div></>
}
