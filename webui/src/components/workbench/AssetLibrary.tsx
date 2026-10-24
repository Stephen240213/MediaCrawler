import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ImagePlus, RefreshCw, Film, Download, Images } from 'lucide-react'
import { toast } from 'sonner'
import { workbenchApi, formatSize, errorMessage, type Asset } from '@/lib/workbench'

export function AssetLibrary({onUse}: {onUse: (asset: Asset) => void}) {
  const [archived, setArchived] = useState(false)
  const {data = [], isLoading, isError, refetch} = useQuery({queryKey: ['workbenchAssets', archived], queryFn: () => workbenchApi.assets(archived), refetchInterval: 10000})
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState('all')
  const [preview, setPreview] = useState<Asset | null>(null)
  async function upload(files: FileList | null) {
    if (!files?.length) return
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        if (file.size > 10 * 1024 * 1024) { toast.error(`${file.name} 超过 10 MB`); continue }
        const base64 = await new Promise<string>((resolve, reject) => {const reader = new FileReader(); reader.onerror = reject; reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.readAsDataURL(file)})
        await workbenchApi.upload(file.name, base64)
      }
      await client.invalidateQueries({queryKey: ['workbenchAssets']})
      toast.success('图片导入完成')
    } catch (error) { toast.error(errorMessage(error)) }
    finally { setUploading(false); if (input.current) input.current.value = '' }
  }
  async function archive(asset: Asset) {
    try {
      await workbenchApi.archive(asset.id, !archived)
      await client.invalidateQueries({queryKey: ['workbenchAssets']})
      toast.success(archived ? '素材已恢复' : '已移出素材库，文件与草稿引用保留，可在已移出列表恢复')
    } catch (error) { toast.error(errorMessage(error)) }
  }
  const filtered = data.filter(asset => (kind === 'all' || asset.kind === kind) && `${asset.name} ${asset.id}`.toLowerCase().includes(search.toLowerCase()))
  return <>
    <section className="wb-card">
      <div className="wb-card-heading"><Images size={20}/><div><h2>素材管理</h2><p>汇集采集的媒体与本机导入的图片</p></div><button className="wb-button" onClick={() => refetch()} aria-label="刷新素材"><RefreshCw size={15}/></button><button className="wb-button primary" disabled={uploading} onClick={() => input.current?.click()}><ImagePlus size={16}/>{uploading ? '正在导入…' : '导入图片'}</button><input ref={input} hidden type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple onChange={e => upload(e.target.files)}/></div>
      <div className="wb-library-toolbar"><label className="wb-search-label">搜索素材<input placeholder="按文件名或路径搜索" value={search} onChange={e => setSearch(e.target.value)}/></label><label>素材类型<select value={kind} onChange={e => setKind(e.target.value)}><option value="all">全部素材</option><option value="image">图片</option><option value="video">视频</option></select></label><button className="wb-button" onClick={() => setArchived(!archived)}>{archived ? '返回素材库' : '已移出的素材'}</button><span className="wb-hint">共 {filtered.length} 个 · 导入图片每张不超过 10 MB</span></div>
      {isError ? <p className="wb-empty" role="alert">素材加载失败，请刷新重试。</p> : !filtered.length ? <div className="wb-empty"><Images size={38}/><h3>{isLoading ? '正在读取素材…' : data.length ? '没有匹配的素材' : '把灵感收进素材库'}</h3><p>导入本机图片，或在采集时开启「下载媒体素材」。</p></div> : <div className="wb-asset-grid">{filtered.map(asset => <article className="wb-asset-card" key={asset.id}>
        <button className="wb-asset-visual" onClick={() => setPreview(asset)} aria-label={`预览 ${asset.name}`}>{asset.kind === 'image' ? <img src={asset.url} loading="lazy" alt={asset.name}/> : <span><Film size={36}/>视频素材</span>}<span className="wb-asset-source">{asset.source}</span></button>
        <div className="wb-asset-info"><strong title={asset.id}>{asset.name}</strong><small>{formatSize(asset.size)} · {new Date(asset.updated_at * 1000).toLocaleDateString('zh-CN')}</small><div><a className="wb-button" href={asset.url} download={asset.name} aria-label={`下载 ${asset.name}`}><Download size={14}/></a>{asset.kind === 'image' && !archived && <button className="wb-button" onClick={() => onUse(asset)}>加入草稿</button>}</div><button className="wb-asset-archive" onClick={() => archive(asset)}>{archived ? '恢复素材' : '移出素材库'}</button></div>
      </article>)}</div>}
    </section>
    {preview && <div className="wb-modal" role="dialog" aria-modal="true" aria-label="素材预览" onClick={() => setPreview(null)} onKeyDown={e => {if (e.key === 'Escape') setPreview(null)}}><div className="wb-modal-content" onClick={e => e.stopPropagation()}><button autoFocus className="wb-button wb-modal-close" onClick={() => setPreview(null)}>关闭预览</button>{preview.kind === 'image' ? <img src={preview.url} alt={preview.name}/> : <video controls src={preview.url}/>}<p>{preview.name}</p></div></div>}
  </>
}
