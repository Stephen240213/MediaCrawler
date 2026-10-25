import { useSession, canOperate } from '@/lib/session'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import api from '@/lib/api'
import './analytics.css'

type Point = {label: string; value: number}
type Content = {platform: string; content_id: string; title?: string; author?: string; keyword?: string; published_at?: string; collected_at?: string; likes: number | null; favorites: number | null; comments: number | null; shares: number | null; url?: string; interactions: number | null; valid_metric_count: number; comment_id?: string; body?: string}
type CommentAnalysis = {sample_comments: number; total_comments: number; unique_texts: number; question_count: number; truncated_comments: number; themes: Point[]; sentiments: Point[]; rules: Record<string, string[]>}
type Overview = {ready: boolean; summary: Record<string, number | string | null>; charts: Record<string, Point[]>; keywords: {keyword: string}[]; generated_at: string; word_sample_bodies: number; comment_analysis?: CommentAnalysis}
type ImportResult = {total: number; inserted: number; updated: number; duplicates: number; rejected: number; warnings: number; dry_run: boolean; errors: {row: number; error?: string; warning?: string}[]}
type MetricHistory = {comparable: boolean; delta: number | null; rate: number | null; first: {collected_at: string; value: number} | null; last: {collected_at: string; value: number} | null}
type History = {snapshot_count: number; metrics: Record<string, MetricHistory>; items: {collected_at: string; likes: number | null; favorites: number | null; comments: number | null; shares: number | null}[]}
const platforms: Record<string, string> = {xhs: '小红书', dy: '抖音', ks: '快手', bili: '哔哩哔哩', wb: '微博', tieba: '贴吧', zhihu: '知乎'}
const metrics: Record<string, string> = {likes: '点赞', favorites: '收藏', comments: '评论', shares: '分享'}
const number = (v: unknown) => typeof v === 'number' ? v.toLocaleString('zh-CN') : '—'
const date = (v: unknown) => typeof v === 'string' && v ? new Date(v).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '未提供'
const safeUrl = (value?: string) => value && /^https?:\/\//i.test(value) ? value : undefined
const message = (error: unknown) => {
  const detail = (error as {response?: {data?: {detail?: unknown}}})?.response?.data?.detail
  return typeof detail === 'string' ? detail : '请求失败，请检查本机后端连接后重试。'
}

function Chart({title, points, onSelect, hint, line = false}: {title: string; points: Point[]; onSelect: (p: Point) => void; hint?: string; line?: boolean}) {
  const max = Math.max(1, ...points.map(p => p.value))
  return <section className="wb-card an-chart"><h2>{title}</h2>{hint && <p className="an-muted">{hint}</p>}
    {!points.length ? <p className="an-empty">暂无有效数据</p> : <>
      {line && <svg role="img" aria-label={`${title}趋势图，点击下方日期查看明细`} viewBox="0 0 600 130" className="an-line"><polyline fill="none" stroke="currentColor" strokeWidth="2" points={points.map((p, i) => `${20 + i / Math.max(1, points.length - 1) * 560},${110 - p.value / max * 90}`).join(' ')}/>{points.map((p, i) => <circle key={p.label} cx={20 + i / Math.max(1, points.length - 1) * 560} cy={110 - p.value / max * 90} r="4" tabIndex={0} role="button" aria-label={`${p.label}：${p.value}，查看明细`} onClick={() => onSelect(p)} onKeyDown={e => {if (e.key === 'Enter' || e.key === ' ') onSelect(p)}}><title>{p.label}：{p.value}</title></circle>)}</svg>}
      <div className="an-bars">{points.map(p => <button key={p.label} onClick={() => onSelect(p)} title="点击筛选对应明细"><span>{platforms[p.label] || p.label}</span><span className="an-track"><i style={{width: `${p.value / max * 100}%`}}/></span><strong>{number(p.value)}</strong></button>)}</div>
    </>}
  </section>
}

function WordCloud({points, onSelect}: {points: Point[]; onSelect: (p: Point) => void}) {
  const max = Math.max(1, ...points.map(p => p.value))
  return <section className="wb-card an-chart"><h2>评论词云</h2><p className="an-muted">字号按含词评论数平方根缩放；与高频词使用同一筛选和样本。点击词语查看原文。</p>
    {!points.length ? <p className="an-empty">暂无有效词语</p> : <div className="an-word-cloud" aria-label="评论词云">{points.map((p,i) => <button key={p.label} onClick={() => onSelect(p)} style={{fontSize: `${14+26*Math.sqrt(p.value/max)}px`, color: ['#6d28d9','#7c3aed','#4f46e5','#0f766e'][i%4]}} title={`${p.label}：${p.value} 条评论`}>{p.label}<small>{p.value}</small></button>)}</div>}
  </section>
}

export function Analytics() {
  const writable = canOperate(useSession())
  const cache = useQueryClient()
  const [platform, setPlatform] = useState('')
  const [keyword, setKeyword] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [basis, setBasis] = useState('published_at')
  const [drill, setDrill] = useState({content_id: '', author_id: '', content_type: ''})
  const [kind, setKind] = useState<'content' | 'comment'>('content')
  const [sort, setSort] = useState('interactions')
  const [word, setWord] = useState('')
  const [commentCode, setCommentCode] = useState('')
  const [downloading, setDownloading] = useState('')
  const [commentDate, setCommentDate] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Content | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [importPlatform, setImportPlatform] = useState('')
  const [importKind, setImportKind] = useState('content')
  const [result, setResult] = useState<ImportResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const options = {platform, keyword, start, end, basis, ...drill}
  const overview = useQuery({queryKey: ['analytics', 'overview', options], queryFn: async () => (await api.get<Overview>('/analytics/overview', {params: options})).data, retry: false})
  const details = useQuery({queryKey: ['analytics', 'details', options, page, kind, sort, word, commentDate, commentCode], queryFn: async () => (await api.get<{items: Content[]; total: number}>('/analytics/details', {params: {...options, page, kind, sort, word, comment_date: commentDate, comment_code: commentCode}})).data, retry: false})
  const history = useQuery({queryKey: ['analytics', 'history', selected?.platform, selected?.content_id], queryFn: async () => (await api.get<History>('/analytics/history', {params: {platform: selected?.platform, content_id: selected?.content_id}})).data, enabled: !!selected, retry: false})
  const resetPage = () => {setPage(1); setSelected(null)}
  const refresh = () => cache.invalidateQueries({queryKey: ['analytics']})
  const initialize = async () => {
    setBusy(true); setNotice('')
    try {const r = await api.post('/analytics/initialize'); setNotice(r.data.backup ? `初始化完成。原库备份：${r.data.backup}` : '分析结构已就绪。'); await refresh()} catch (e) {setNotice(message(e))} finally {setBusy(false)}
  }
  const importFile = async (dry: boolean) => {
    if (!file) return
    setBusy(true); setNotice('')
    try {
      const response = await api.post<ImportResult>('/analytics/import', file, {headers: {'Content-Type': 'application/octet-stream'}, params: {filename: file.name, platform: importPlatform, kind: importKind, dry_run: dry}})
      setResult(response.data)
      if (!dry) {setNotice('导入完成，已刷新分析。'); await refresh()}
    } catch (e) {setNotice(message(e)); setResult(null)} finally {setBusy(false)}
  }
  const exportFile = async (format: string) => {
    setBusy(true); setNotice('')
    try {
      const r = await api.get('/analytics/export', {params: {...options, format}, responseType: 'blob'})
      const url = URL.createObjectURL(r.data); const a = document.createElement('a'); a.href = url; a.download = `分析筛选结果.${format}`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {setNotice('导出失败：单次上限 50000 条，请缩小筛选范围或检查本机服务。')} finally {setBusy(false)}
  }
  const selectWord = (p: Point) => {setKind('comment'); setWord(p.label); setCommentCode(''); setCommentDate(''); resetPage()}
  const selectCode = (code: string) => {setKind('comment'); setCommentCode(code); setWord(''); setCommentDate(''); resetPage()}
  const downloadNote = async (item: Content) => {
    setDownloading(`${item.platform}:${item.content_id}`); setNotice('')
    try {
      const r = await api.post('/analytics/note-download', null, {params: {platform: item.platform, content_id: item.content_id}, responseType:'blob', timeout:180000})
      const url = URL.createObjectURL(r.data); const a = document.createElement('a'); a.href = url
      a.download = (item.title || item.content_id).replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0,70) + '.zip'; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000)
      const status = `下载包已生成：媒体 ${r.headers['x-bundle-media'] || '0'} 个，其中视频 ${r.headers['x-bundle-videos'] || '0'} 个。`
      const report = r.headers['x-bundle-report'] ? decodeURIComponent(r.headers['x-bundle-report']) : ''
      setNotice(Number(r.headers['x-bundle-warnings']) ? `${status}${report} 详情见 Download Report.txt。` : status)
    } catch {setNotice('笔记打包失败，请检查本机服务后重试。')} finally {setDownloading('')}
  }
  const data = overview.data
  const summary = data?.summary
  const timeDrill = (key: string, p: Point) => {setBasis(key); setStart(p.label); setEnd(p.label); resetPage()}
  const reset = () => {setPlatform(''); setKeyword(''); setStart(''); setEnd(''); setBasis('published_at'); setDrill({content_id: '', author_id: '', content_type: ''}); setWord(''); setCommentCode(''); setCommentDate(''); resetPage()}
  return <div className="analytics">
    <section className="wb-card an-filter"><div className="an-heading"><h2>SQLite 数据分析</h2><button className="wb-button" onClick={() => {void refresh()}}>刷新数据</button></div>
      <div className="an-filter-grid">
        <label>平台<select value={platform} onChange={e => {setPlatform(e.target.value); resetPage()}}><option value="">全部平台</option>{Object.entries(platforms).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <label>关键词（精确匹配）<input list="analytics-keywords" value={keyword === '__missing__' ? '' : keyword} placeholder={keyword === '__missing__' ? '仅未提供关键词' : '全部关键词'} onChange={e => {setKeyword(e.target.value); resetPage()}}/><datalist id="analytics-keywords">{data?.keywords.map(k => <option key={k.keyword} value={k.keyword}/>)}</datalist></label>
        <label>日期范围口径<select value={basis} onChange={e => {setBasis(e.target.value); resetPage()}}><option value="published_at">内容发布时间</option><option value="collected_at">最近采集时间</option></select></label>
        <label>开始日期<input type="date" value={start} onChange={e => {setStart(e.target.value); resetPage()}}/></label>
        <label>结束日期<input type="date" value={end} onChange={e => {setEnd(e.target.value); resetPage()}}/></label>
      </div><div className="an-actions"><button className="wb-button" onClick={reset}>清除筛选与明细定位</button><button className="wb-button" disabled={busy || !data?.ready} onClick={() => void exportFile('csv')}>导出 CSV</button><button className="wb-button" disabled={busy || !data?.ready} onClick={() => void exportFile('xlsx')}>导出 Excel</button></div>
      <p className="an-muted">日期均为北京时间（UTC+8）。发布时间反映内容发布，采集时间反映每条内容最近一次采集；当前日期筛选同时作用于所有面板。导出当前内容筛选，不包含评论文本筛选。</p>
      {(drill.author_id || drill.content_id || drill.content_type || word) && <p className="an-selection">明细定位：{drill.content_id && `内容 ${drill.content_id} `}{drill.author_id && '已选作者 '}{drill.content_type && `类型 ${drill.content_type === '__missing__' ? '未提供' : drill.content_type} `}{word && `评论包含「${word}」`}</p>}
    </section>
    {notice && <p role="status" className="an-notice">{notice}</p>}
    {overview.isLoading && <p role="status" className="an-empty">正在读取本机数据库…</p>}
    {overview.isError && <div role="alert" className="an-error">{message(overview.error)}<button className="wb-button" onClick={() => void overview.refetch()}>重试</button></div>}
    {data && !data.ready && <section className="wb-card an-empty"><h2>尚未初始化分析结构</h2><p>将复用现有采集 SQLite 库，已有库会先自动备份，再兼容回填。不会写入草稿或任务库。</p><button className="wb-button primary" disabled={busy || !writable} onClick={() => void initialize()}>备份并初始化</button></section>}
    {data?.ready && <>
      <div className="an-cards">{[['内容数', summary?.content_count], ['已采集评论数', summary?.comment_count], ['作者数', summary?.author_count], ['最近采集时间', date(summary?.last_collected_at)]].map(([label, value]) => <section className="wb-card" key={String(label)}><p>{label}</p><strong>{typeof value === 'string' ? value : number(value)}</strong></section>)}</div>
      {summary?.content_count === 0 && <section className="wb-card an-empty">当前筛选没有内容。可清除筛选、导入历史文件，或在本机采集时选择 SQLite；不会填充示例数据。</section>}
      <div className="an-grid"><Chart title="内容发布趋势" hint={`有效发布时间 ${number(summary?.valid_publish_count)} 条；按发布日去重内容数`} points={data.charts.published_at} line onSelect={p => timeDrill('published_at', p)}/><Chart title="最近采集情况" hint={`有效采集时间 ${number(summary?.valid_collect_count)} 条；非历次采集事件总量`} points={data.charts.collected_at} line onSelect={p => timeDrill('collected_at', p)}/></div>
      <div className="an-grid three">{[['platform', '平台分布'], ['keyword', '关键词分布'], ['content_type', '内容类型分布']].map(([key, title]) => <Chart key={key} title={title} hint="按内容数排列，最多展示前 30 项，点击查看明细" points={data.charts[key]} onSelect={p => {const value = p.label === '未提供' ? '__missing__' : p.label; if (key === 'platform') setPlatform(value); else if (key === 'keyword') setKeyword(value); else setDrill({...drill, content_type: value}); resetPage()}}/>)}</div>
      <div className="an-grid"><Chart title="评论发布趋势" hint={`有效评论时间 ${number(summary?.valid_comment_time_count)} 条；仅统计已采集且关联当前内容的去重评论`} points={data.charts.comment_trend} line onSelect={p => {setKind('comment'); setWord(''); setCommentCode(''); resetPage(); setCommentDate(p.label)}}/><WordCloud points={data.charts.words} onSelect={selectWord}/></div>
      {data.comment_analysis && <section className="wb-card an-chart"><h2>评论内容分析</h2><p className="an-muted">当前关联评论 {number(data.comment_analysis.total_comments)} 条；分析最近 {number(data.comment_analysis.unique_texts)} 个非空不同文本（最多 5000 个），对应 {number(data.comment_analysis.sample_comments)} 条评论。每文本最多 2000 字，截断 {number(data.comment_analysis.truncated_comments)} 条。重复文本按评论记录数加权。</p>
        <p>包含提问线索：<button className="wb-button" onClick={() => selectCode('提问意图')}>{number(data.comment_analysis.question_count)} 条（{data.comment_analysis.sample_comments ? (100*data.comment_analysis.question_count/data.comment_analysis.sample_comments).toFixed(1) : '0'}% 样本）</button></p>
        <div className="an-grid"><Chart title="关注主题" points={data.comment_analysis.themes} hint="规则多标签分类，一条评论可命中多个主题，数量不能相加作为评论总数。点击查看当前全部评论中的匹配原文。" onSelect={p => selectCode(p.label)}/><Chart title="情绪线索" points={data.comment_analysis.sentiments} hint="词典规则互斥分类；不可靠识别反讽、上下文和复杂否定。未识别不等于中性，不作为满意度结论。" onSelect={p => selectCode(p.label)}/></div>
        <details><summary>分析方法、规则及参考资料</summary><p>先按评论 ID 去重，分词去除常见停用词与表情标记；高频词按含词评论数统计，词云仅用于探索。主题采用可核查的关键词编码；情绪按正向、负向、并存、未识别分组，负向短语优先移除再匹配正向词。分类点击查询全部当前评论，可能比有界样本数量多。</p>{Object.entries(data.comment_analysis.rules).map(([label,terms]) => <p key={label}>{label}：{terms.join('、')}</p>)}<p>方法参考：<a href="https://github.com/fxsjy/jieba#主要功能" target="_blank" rel="noreferrer">Jieba 分词与关键词提取</a> · <a href="https://scikit-learn.org/stable/auto_examples/applications/plot_topics_extraction_with_nmf_lda.html" target="_blank" rel="noreferrer">scikit-learn 主题提取</a>。本版使用透明规则编码，未运行 NMF/LDA 或训练情感模型。</p></details>
      </section>}
      <Chart title="评论高频词" hint="按样本中含词评论数统计；已过滤常见口语及表情。点击查看当前全部评论中的包含原文。" points={data.charts.words} onSelect={selectWord}/>
      <section className="wb-card an-details" id="analytics-details"><div className="an-heading"><h2>{kind === 'content' ? '内容互动排行与明细' : '原始评论'}</h2><div className="an-actions"><button className="wb-button" aria-pressed={kind === 'content'} onClick={() => {setKind('content'); resetPage()}}>内容</button><button className="wb-button" aria-pressed={kind === 'comment'} onClick={() => {setKind('comment'); resetPage()}}>评论</button>{kind === 'content' && <select aria-label="内容排序" value={sort} onChange={e => {setSort(e.target.value); resetPage()}}><option value="interactions">互动总量排行</option><option value="recent">最近采集</option></select>}</div></div>
        {kind === 'content' && <p className="an-muted">互动总量为已知的点赞、收藏、评论、分享之和；仅用于排序，部分字段缺失时不可直接横向比较。全缺失显示「—」。</p>}
        {kind === 'comment' && (commentCode || word) && <p>评论筛选：{commentCode || `包含「${word}」`} <button className="wb-button" onClick={() => {setWord(''); setCommentCode(''); resetPage()}}>清除评论筛选</button></p>}
        {kind === 'comment' && commentDate && <p>评论日期：{commentDate} <button className="wb-button" onClick={() => {setCommentDate(''); resetPage()}}>清除评论日期</button></p>}
        {details.isLoading && <p role="status">正在加载明细…</p>}{details.isError && <p role="alert">{message(details.error)}<button onClick={() => void details.refetch()}>重试</button></p>}
        {!details.isLoading && !details.isError && !details.data?.items.length && <p className="an-empty">暂无匹配的{kind === 'content' ? '内容' : '评论'}</p>}
        {!!details.data?.items.length && <div className="an-table-wrap"><table><thead><tr><th>平台 / ID</th><th>{kind === 'content' ? '内容 / 作者' : '评论原文 / 所属内容'}</th><th>发布时间</th>{kind === 'content' && <><th>点赞 / 收藏 / 评论 / 分享</th></>}<th>操作</th></tr></thead><tbody>{details.data.items.map(item => <tr key={`${item.platform}:${item.comment_id || item.content_id}`}><td>{platforms[item.platform]}<small>{item.comment_id || item.content_id}</small></td><td>{kind === 'content' ? <>{item.title || '未提供标题'}<small>{item.author || '未提供作者'} · {item.keyword || '未提供关键词'}</small></> : <>{item.body || '空评论'}<small>{item.title || item.content_id}</small></>}</td><td>{date(item.published_at)}</td>{kind === 'content' && <><td>{[item.likes, item.favorites, item.comments, item.shares].map(number).join(' / ')}</td></>}<td>{safeUrl(item.url) ? <a href={safeUrl(item.url)} target="_blank" rel="noreferrer">原始内容 ↗</a> : <span>未提供链接</span>}<button className="wb-button" onClick={() => setSelected(item)}>查看互动历史</button>{kind === 'content' && <button className="wb-button" disabled={!writable || !!downloading} onClick={() => void downloadNote(item)}>{downloading === `${item.platform}:${item.content_id}` ? '打包下载中…' : '一键下载'}</button>}</td></tr>)}</tbody></table></div>}
        <div className="an-actions"><span>共 {number(details.data?.total)} 条 · 第 {page} 页</span><button className="wb-button" disabled={page <= 1 || details.isFetching} onClick={() => setPage(page - 1)}>上一页</button><button className="wb-button" disabled={!details.data || page * 20 >= details.data.total || details.isFetching} onClick={() => setPage(page + 1)}>下一页</button></div>
      </section>
      {selected && <section className="wb-card an-history"><div className="an-heading"><h2>互动历史 · {selected.title || selected.content_id}</h2><button className="wb-button" onClick={() => setSelected(null)}>关闭历史</button></div>{history.isLoading && <p role="status">正在读取快照…</p>}{history.isError && <p role="alert">{message(history.error)}</p>}{history.data && <><p className="an-muted">共 {history.data.snapshot_count} 个快照，最多显示最近 100 个；增长使用各指标最早与最新的非空快照，基数为 0 时不计算增长率。</p><div className="an-cards">{Object.entries(history.data.metrics).map(([key, h]) => <div key={key}><strong>{metrics[key]}</strong><p>{h.comparable ? `增长 ${number(h.delta)} · ${h.rate === null ? '基数为 0，无增长率' : (h.rate * 100).toFixed(1) + '%'}` : '暂无可比较历史'}</p>{h.comparable && <small>{date(h.first?.collected_at)} → {date(h.last?.collected_at)}</small>}</div>)}</div><div className="an-table-wrap"><table><thead><tr>{Object.values(metrics).map(m => <th key={m}>{m}</th>)}</tr></thead><tbody>{history.data.items.map(h => <tr key={h.collected_at}><td>{date(h.collected_at)}</td>{[h.likes, h.favorites, h.comments, h.shares].map((v, i) => <td key={i}>{number(v)}</td>)}</tr>)}</tbody></table></div></>}</section>}
    </>}
    <section className="wb-card an-import"><h2>导入历史数据</h2><p className="an-muted">支持 Excel (.xlsx)、UTF-8 CSV、JSON、JSONL；单次 10 MB / 50000 行。Excel 读取首个工作表。先校验后确认写入；错误行跳过，有效行原子写入。不会导入草稿和任务库。</p><p className="an-muted">必填：平台与内容 ID；评论还需评论 ID。支持原采集字段或 platform、content_id、author、keyword、published_at、collected_at、likes、favorites、comments、shares；评论正文为 body。日期缺失保留为空，不生成伪造历史。</p><div className="an-filter-grid"><label>数据文件<input type="file" accept=".xlsx,.csv,.json,.jsonl" onChange={e => {setFile(e.target.files?.[0] || null); setResult(null)}}/></label><label>平台<select value={importPlatform} onChange={e => {setImportPlatform(e.target.value); setResult(null)}}><option value="">使用文件中的 platform</option>{Object.entries(platforms).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label>记录类型<select value={importKind} onChange={e => {setImportKind(e.target.value); setResult(null)}}><option value="content">内容</option><option value="comment">评论</option></select></label></div><div className="an-actions"><button className="wb-button" disabled={!writable || !file || busy || !data?.ready} onClick={() => void importFile(true)}>{busy ? '处理中…' : '校验并预览结果'}</button><button className="wb-button primary" disabled={!writable || !result?.dry_run || busy || result.inserted + result.updated === 0} onClick={() => void importFile(false)}>确认导入有效记录</button></div>{result && <div role="status"><p>{result.dry_run ? '校验预览' : '导入结果'}：总计 {result.total} · 新记录 {result.inserted} · 已有内容的新观察 {result.updated} · 重复 {result.duplicates} · 拒绝 {result.rejected} · 警告 {result.warnings}</p><ul>{result.errors.map((e, i) => <li key={i}>第 {e.row} 行：{e.error || e.warning}</li>)}</ul></div>}</section>
    <details className="wb-card an-definitions"><summary>指标定义、有效记录与数据口径</summary><p>内容以「平台 + 内容 ID」去重；评论以「平台 + 评论 ID」去重。概览评论数为已采集且可关联内容的原始评论数量，不是平台显示评论数之和。内容字段中的评论数来自平台互动指标，可能包含尚未采集的评论。</p><p>收藏、分享等不支持的指标保留为空；字符串数字支持千、万、亿及 k/w/m。原平台表中已有默认值 0 无法追溯为缺失。作者缺失不计入作者数。历史只从实际记录的采集时间开始，旧库回填无法恢复更新前的历史。</p><p>图表点击可筛选明细。分布展示前 30 项，内容和评论按 20 条分页；评论内容分析采用有界样本与可解释规则，不代表全量语义结论。缺少发布时间的内容不进入发布时间趋势，但仍计入未设置日期筛选的总数。</p><p>有效记录：发布时间 {number(summary?.valid_publish_count)}；采集时间 {number(summary?.valid_collect_count)}；点赞 {number(summary?.valid_likes_count)}；收藏 {number(summary?.valid_favorites_count)}；评论指标 {number(summary?.valid_comments_count)}；分享 {number(summary?.valid_shares_count)}。</p><p>源数据更新时间：{date(summary?.data_updated_at)}；本次查询：{date(data?.generated_at)}。数据仅通过本机只读聚合接口查询，不向前端传输数据库文件。</p></details>
  </div>
}
