import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Play, Square } from 'lucide-react'
import api from '@/lib/api'
import { errorMessage } from '@/lib/workbench'
import { useCrawlerStore } from '@/store/crawlerStore'
import { useStartCrawler, useStopCrawler } from '@/hooks/useCrawler'
import { canOperate, useSession } from '@/lib/session'

type Tool = {platform: string; name: string; description: string; inputSchema: {properties: Record<string, {type: string; enum?:string[]; format?:string}>; required: string[]}; datePolicies?:Record<string,{min:string;max:string;recommended:string}>}
const platforms: Record<string, string> = {xhs: '小红书', dy: '抖音', twitter: 'X / Twitter', youtube: 'YouTube'}
const labels: Record<string, string> = {keyword:'关键词', search_query:'搜索关键词', offset:'偏移量', page_num:'页码', page_size:'每页条数', work_id:'作品 ID', work_link:'笔记链接', work_url:'作品链接', video_id:'视频 ID', video_url:'视频链接', url:'视频 / 笔记链接', task_id:'远程任务 ID', account_id:'账号 ID', red_id:'小红书号', userid:'用户 ID', user_id:'用户 ID', sec_user_id:'sec_user_id', author_url:'创作者主页链接', tweet_id:'推文 ID', screen_name:'X 用户名', rest_id:'X 用户 ID', cursor:'翻页游标', continuation_token:'下一页令牌', opus_id:'笔记 ID', data_num:'评论条数（-1 为全部）', rank_date:'榜单日期', category:'分类', start_date:'开始日期', end_date:'结束日期', start_time:'开始时间', end_time:'结束时间', type:'分类', date_type:'榜单周期', sort_type:'排序方式', sort_by:'排序方式', search_type:'搜索类型', timeout_seconds:'超时秒数', short_id:'短号', unique_name:'抖音号', country_code:'国家代码', language_code:'语言代码', publish_time_start:'发布开始日期', publish_time_end:'发布结束日期'}

export function RedFoxCollector() {
  const writable = canOperate(useSession())
  const config = useCrawlerStore(s => s.config)
  const status = useCrawlerStore(s => s.status)
  const [platform, setPlatform] = useState('xhs')
  const [selected, setSelected] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const [validation, setValidation] = useState('')
  const [parentId, setParentId] = useState('')
  const start = useStartCrawler(), stop = useStopCrawler()
  const catalog = useQuery({queryKey:['redfoxTools'], queryFn:async()=>(await api.get<{tools:Tool[]; configured:Record<string,boolean>}>('/crawler/redfox/tools')).data})
  const tools = (catalog.data?.tools || []).filter(t => t.platform === platform)
  const tool = tools.find(t => t.name === selected) || tools.find(t => t.name.includes('search_articles') || t.name.includes('search_tweets') || t.name.includes('search_videos')) || tools[0]
  const busy = status === 'running' || status === 'stopping' || start.isPending
  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!tool || !writable || busy || !catalog.data?.configured[platform]) return
    const args: Record<string, string | number> = {}
    for (const [name, schema] of Object.entries(tool.inputSchema.properties)) {
      if (!values[name]?.trim()) continue
      args[name] = schema.type === 'number' ? Number(values[name]) : values[name].trim()
    }
    if (tool.inputSchema.required.some(name => args[name] === undefined)) {setValidation('请填写工具的必填参数'); return}
    setValidation('')
    start.mutate({...config, platform, collection_method:'redfox_mcp', mcp_tool:tool.name, mcp_arguments:args, mcp_parent_content_id:parentId,
      crawler_type:tool.name.includes('search') ? 'search' : 'detail', keywords:'', specified_ids:'', creator_ids:'',
      cookies:'', xhs_filters:undefined, enable_media:false, enable_comments:false, enable_sub_comments:false,
      headless:false, start_page:1, save_option:'json'})
  }
  return <section className="wb-card wb-tool-form">
    <div className="wb-card-heading"><h2>RedFox MCP 采集</h2><span className="wb-badge">无需浏览器登录</span></div>
    <form onSubmit={submit}><fieldset disabled={busy || !writable}>
      <label>目标平台<select aria-label="目标平台" value={platform} onChange={e=>{setPlatform(e.target.value);setSelected('');setValues({});setParentId('');setValidation('')}}>{Object.entries(platforms).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
      {catalog.isError && <p role="alert" className="wb-error">{errorMessage(catalog.error)}<button type="button" className="wb-button" onClick={()=>void catalog.refetch()}>重新加载</button></p>}
      {catalog.isLoading && <p className="wb-hint">正在读取工具目录…</p>}
      {catalog.data && <p className={catalog.data.configured[platform] ? 'wb-hint' : 'wb-error'}>{catalog.data.configured[platform] ? '已找到本机持久化 RedFox 凭据。' : '未找到凭据：请在采集电脑配置 REDFOX_API_KEY 或 Codex MCP 持久配置后刷新。'}</p>}
      <label>MCP 工具<select aria-label="MCP 工具" value={tool?.name || ''} onChange={e=>{setSelected(e.target.value);const next=tools.find(t=>t.name===e.target.value);setValues(Object.fromEntries(Object.entries(next?.datePolicies||{}).map(([field,policy])=>[field,policy.recommended])));setParentId('');setValidation('')}}>{tools.map(t=><option key={t.name} value={t.name}>{t.description.split('。')[0]} · {t.name}</option>)}</select></label>
      {tool && <><p className="wb-hint" style={{whiteSpace:'pre-line'}}>{tool.description}</p>
        <div className="wb-form-grid">{Object.entries(tool.inputSchema.properties).map(([name,schema])=><label key={tool.name+name}>{labels[name] || name}{tool.inputSchema.required.includes(name) ? ' *' : '（可选）'}{schema.enum ? <select aria-label={labels[name] || name} required={tool.inputSchema.required.includes(name)} value={values[name] || ''} onChange={e=>setValues({...values,[name]:e.target.value})}><option value="">请选择官方分类</option>{schema.enum.map(value=><option key={value} value={value}>{value}</option>)}</select> : <input aria-label={(labels[name] || name)+(tool.inputSchema.required.includes(name)?' *':'（可选）')} type={schema.format==='date'?'date':schema.type === 'number' ? 'number' : 'text'} min={tool.datePolicies?.[name]?.min} max={tool.datePolicies?.[name]?.max} step={schema.format==='date'?undefined:'any'} required={tool.inputSchema.required.includes(name)} value={values[name] || ''} onChange={e=>setValues({...values,[name]:e.target.value})} placeholder={name}/>}</label>)}</div></>}
      {tool?.datePolicies?.start_date&&<div className="wb-hint"><p>黑马榜按单日查询，并非从开始日期累计查询。为避开服务的30天边界，本工作台提交范围为上海日期 {tool.datePolicies.start_date.min} 至 {tool.datePolicies.start_date.max}；部分日期即使格式正确，也可能因数据或服务窗口问题被拒绝。</p><button type="button" className="wb-button" onClick={()=>{setValues({...values,start_date:tool.datePolicies!.start_date.recommended});setValidation('')}}>使用建议日期 {tool.datePolicies.start_date.recommended}</button><p>此按钮只填写日期；点击“执行 MCP 采集”才会发出请求，可能消耗积分。</p></div>}
      {tool?.name.includes('comments') && <label>关联内容 ID（可选）<input value={parentId} maxLength={200} pattern="[A-Za-z0-9_-]*" onChange={e=>setParentId(e.target.value)} placeholder="评论结果不含笔记/视频 ID 时填写"/><small>优先使用结果或原提交任务的内容 ID；此项用于补充历史任务的关联，不发送给 RedFox。</small></label>}
      <p className="wb-hint">每次执行一次工具调用，可能消耗 RedFox 积分；翻页需填写上次结果中的游标或偏移量后再次执行。评论、转写等异步工具只提交任务，请用对应结果查询工具读取远程任务 ID。</p>
      <p className="wb-hint">原始 JSON 保存在 data/{platform}/redfox，有效内容与评论自动同步到数据分析。小红书正文、评论及批次可进入内容资料库和 AI 内容分析；其他平台目前支持数据分析。账号信息、任务回执、转写结果保留原文件。停止本机任务不会撤销远程已提交的任务。</p>
    </fieldset>
    {validation && <p className="wb-error" role="alert">{validation}</p>}
    <div className="wb-form-footer"><span>RedFox MCP · JSON 保存</span>{busy ? <button type="button" className="wb-button danger" disabled={!writable || status==='stopping' || stop.isPending} onClick={()=>stop.mutate()}><Square size={16}/>停止任务</button> : <button className="wb-button primary" type="submit" disabled={!writable || !tool || !catalog.data?.configured[platform]}><Play size={16}/>执行 MCP 采集</button>}</div>
    </form>
  </section>
}
