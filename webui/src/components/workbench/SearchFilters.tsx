import { useQuery } from '@tanstack/react-query'
import { SlidersHorizontal, RotateCcw } from 'lucide-react'
import api from '@/lib/api'
import { useCrawlerStore } from '@/store/crawlerStore'
import type { XhsSearchFilters } from '@/types/crawler'

export const defaultXhsFilters: XhsSearchFilters = {
  sort_by: 'general', note_type: 'all', publish_time: 'all', search_scope: 'all', location: 'all',
  max_scrolls: 20, timeout_seconds: 300,
}
export interface FilterDefinition {key: string; label: string; options: {value: string; label: string}[]}
export function SearchFilters({disabled}: {disabled: boolean}) {
  const config = useCrawlerStore(s => s.config)
  const update = useCrawlerStore(s => s.updateConfig)
  const enabled = !!config.xhs_filters
  const values = config.xhs_filters || defaultXhsFilters
  const {data, isError} = useQuery({queryKey: ['xhsFilterOptions'], queryFn: async () => (await api.get<{groups: FilterDefinition[]}>('/config/xhs-search-filters')).data})
  const setFilter = (key: string, value: string | number) => update({xhs_filters: {...values, [key]: value}, start_page: 1})
  return <section className="wb-search-filters" aria-label="小红书高级筛选">
    <div className="wb-filter-heading"><SlidersHorizontal size={17}/><div><h3>高级筛选</h3><p>在小红书网页逐项筛选，确认生效后采集</p></div><label className="wb-check"><input type="checkbox" checked={enabled} disabled={disabled || !data} onChange={e => update({xhs_filters: e.target.checked ? {...defaultXhsFilters} : undefined, start_page: 1})}/>启用网页筛选</label></div>
    {isError && <p className="wb-error" role="alert">筛选选项加载失败，刷新后重试。</p>}
    {!enabled && <p className="wb-hint">启用后支持排序、内容类型、发布时间、搜索范围和位置条件。</p>}
    {enabled && data && <>
      <div className="wb-form-grid wb-filter-fields">{data.groups.map(group => <label key={group.key}>{group.label}<select disabled={disabled} value={String(values[group.key as keyof XhsSearchFilters])} onChange={e => setFilter(group.key, e.target.value)}>{group.options.map(option => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>)}</div>
      <div className="wb-filter-summary" aria-label="已选筛选条件">{data.groups.map(group => <span key={group.key}>{group.label}：{group.options.find(o => o.value === values[group.key as keyof XhsSearchFilters])?.label}</span>)}</div>
      <div className="wb-form-grid"><label>最多滚动加载次数<input disabled={disabled} type="number" min={1} max={100} value={values.max_scrolls} onChange={e => setFilter('max_scrolls', Number(e.target.value))}/></label><label>每个关键词时间上限（秒）<input disabled={disabled} type="number" min={30} max={1800} value={values.timeout_seconds} onChange={e => setFilter('timeout_seconds', Number(e.target.value))}/></label></div>
      <p className="wb-hint">只采集筛选后的结果，从首批结果开始。位置选项取决于平台当前环境；筛选失败时不会改用默认结果。</p>
      <button type="button" className="wb-filter-reset" disabled={disabled} onClick={() => update({xhs_filters: {...defaultXhsFilters}, start_page: 1})}><RotateCcw size={13}/>重置筛选</button>
    </>}
  </section>
}
