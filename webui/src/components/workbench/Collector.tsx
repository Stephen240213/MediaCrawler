import { useState } from 'react'
import { Play, Square, ChevronDown, Search, FileText, Users } from 'lucide-react'
import { useCrawlerStore } from '@/store/crawlerStore'
import { useStartCrawler, useStopCrawler } from '@/hooks/useCrawler'
import { platformNames, modeNames } from '@/lib/workbench'
import { ParsedIdList } from '@/components/config/ParsedIdList'

export const collectionTools = [
  {id: 'search', title: '关键词搜索', description: '按关键词查找内容与评论', icon: Search},
  {id: 'detail', title: '指定内容采集', description: '读取笔记、帖子或视频链接', icon: FileText},
  {id: 'creator', title: '创作者采集', description: '采集创作者主页中的内容', icon: Users},
]
export function Collector() {
  const config = useCrawlerStore(s => s.config)
  const update = useCrawlerStore(s => s.updateConfig)
  const status = useCrawlerStore(s => s.status)
  const start = useStartCrawler()
  const stop = useStopCrawler()
  const [validation, setValidation] = useState('')
  const busy = status === 'running' || status === 'stopping' || start.isPending
  const tool = collectionTools.find(t => t.id === config.crawler_type) || collectionTools[0]
  const Icon = tool.icon
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const target = config.crawler_type === 'search' ? config.keywords : config.crawler_type === 'detail' ? config.specified_ids : config.creator_ids
    if (!target.trim()) { setValidation('请先填写采集目标'); return }
    if (config.login_type === 'cookie' && !config.cookies.trim()) { setValidation('请填写 Cookie，或切换为扫码登录'); return }
    setValidation('')
    start.mutate({...config, enable_media: config.enable_media && !['tieba', 'zhihu'].includes(config.platform)})
  }
  return <section className="wb-card wb-tool-form">
    <div className="wb-card-heading"><span className="wb-icon-box"><Icon size={21}/></span><div><h2>{tool.title}</h2><p>{tool.description}</p></div><span className="wb-badge">浏览器执行</span></div>
    <form onSubmit={submit}>
      <fieldset disabled={busy}>
        <div className="wb-form-grid">
          <label>目标平台<select value={config.platform} onChange={e => update({platform: e.target.value})}>{Object.entries(platformNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
          <label>登录方式<select value={config.login_type} onChange={e => update({login_type: e.target.value})}><option value="qrcode">二维码登录</option><option value="cookie">Cookie 登录</option></select></label>
        </div>
        <label>{config.crawler_type === 'search' ? '搜索关键词' : config.crawler_type === 'detail' ? '内容链接或 ID' : '创作者主页链接或 ID'}<span className="wb-required"> *</span>
          <textarea required rows={3} value={config.crawler_type === 'search' ? config.keywords : config.crawler_type === 'detail' ? config.specified_ids : config.creator_ids}
            placeholder={config.crawler_type === 'search' ? '例如：城市漫游, 周末露营' : '粘贴完整链接；多个目标用英文逗号分隔'}
            onChange={e => update({[config.crawler_type === 'search' ? 'keywords' : config.crawler_type === 'detail' ? 'specified_ids' : 'creator_ids']: e.target.value})}/>
        </label>
        <p className="wb-hint">{config.crawler_type === 'search' ? '多个关键词用英文逗号分隔。' : config.platform === 'xhs' ? '小红书请保留完整链接中的 xsec_token 参数。' : '支持的链接格式随平台而异。'}</p>
        {config.crawler_type !== 'search' && <ParsedIdList value={config.crawler_type === 'detail' ? config.specified_ids : config.creator_ids} platform={config.platform} type={config.crawler_type === 'detail' ? 'detail' : 'creator'}/ >}
        {config.login_type === 'cookie' && <label>Cookie<textarea rows={2} value={config.cookies} onChange={e => update({cookies: e.target.value})} autoComplete="off" spellCheck={false}/><small>仅用于当前任务，不保存到草稿或任务记录。</small></label>}
        <div className="wb-separator"/>
        <h3>采集与保存</h3>
        <div className="wb-form-grid">
          <label>内容数量上限<input type="number" min={1} max={10000} value={config.max_notes_count ?? ''} placeholder="使用项目默认值" onChange={e => update({max_notes_count: e.target.value ? Number(e.target.value) : undefined})}/></label>
          <label>保存格式<select value={config.save_option} onChange={e => update({save_option: e.target.value})}>{[['json', 'JSON'], ['jsonl', 'JSONL'], ['csv', 'CSV'], ['excel', 'Excel'], ['sqlite', 'SQLite'], ['db', 'MySQL（需配置）'], ['mongodb', 'MongoDB（需配置）']].map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        </div>
        <label className="wb-switch-row"><span><strong>采集评论</strong><small>保存内容下的评论</small></span><input type="checkbox" checked={config.enable_comments} onChange={e => update({enable_comments: e.target.checked, enable_sub_comments: e.target.checked && config.enable_sub_comments})}/></label>
        {config.enable_comments && <div className="wb-dependent">
          <label>每条内容的评论上限<input type="number" min={1} max={10000} placeholder="使用项目默认值" value={config.max_comments_count ?? ''} onChange={e => update({max_comments_count: e.target.value ? Number(e.target.value) : undefined})}/></label>
          <label className="wb-check"><input type="checkbox" checked={config.enable_sub_comments} onChange={e => update({enable_sub_comments: e.target.checked})}/>展开并采集子评论</label>
        </div>}
        <label className="wb-switch-row"><span><strong>下载媒体素材</strong><small>{['tieba','zhihu'].includes(config.platform) ? '当前平台不支持媒体下载' : '图片与视频会出现在素材管理中'}</small></span><input type="checkbox" disabled={['tieba','zhihu'].includes(config.platform)} checked={config.enable_media && !['tieba','zhihu'].includes(config.platform)} onChange={e => update({enable_media: e.target.checked})}/></label>
        <details className="wb-advanced"><summary><ChevronDown size={15}/>高级参数</summary><div className="wb-form-grid">
          <label>起始页<input type="number" min={1} value={config.start_page} onChange={e => update({start_page: Number(e.target.value) || 1})}/></label>
          <label className="wb-check"><input type="checkbox" checked={config.headless} onChange={e => update({headless: e.target.checked})}/>无头模式</label>
        </div><p className="wb-hint">首次扫码登录建议保持浏览器可见。</p></details>
      </fieldset>
      {validation && <p role="alert" className="wb-error">{validation}</p>}
      <div className="wb-form-footer"><span>结果保存在本机 data 目录</span>{busy ? <button type="button" className="wb-button danger" disabled={status === 'stopping' || stop.isPending} onClick={() => stop.mutate()}><Square size={16}/>{status === 'stopping' ? '正在停止…' : '停止任务'}</button> : <button className="wb-button primary" type="submit"><Play size={16}/>执行{modeNames[config.crawler_type]}</button>}</div>
    </form>
  </section>
}
