import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { crawlerApi, configApi } from '@/lib/api'
import { useCrawlerStore } from '@/store/crawlerStore'
import { errorMessage } from '@/lib/workbench'
import type { CrawlerStatus } from '@/lib/api'
import type { CrawlerConfig } from '@/types/crawler'

export function useCrawlerStatus() {
  const setStatus = useCrawlerStore((state) => state.setStatus)
  const setRunningInfo = useCrawlerStore((state) => state.setRunningInfo)

  return useQuery({
    queryKey: ['crawlerStatus'],
    queryFn: async () => {
      const { data } = await crawlerApi.getStatus()
      setStatus(data.status)
      setRunningInfo(data.platform, data.crawler_type, data.started_at)
      return data
    },
    refetchInterval: 2000,
  })
}

export function useStartCrawler() {
  const queryClient = useQueryClient()
  const setStatus = useCrawlerStore((state) => state.setStatus)
  const clearLogs = useCrawlerStore((state) => state.clearLogs)

  return useMutation({
    mutationFn: (config: CrawlerConfig) => crawlerApi.start(config),
    onMutate: () => {
      clearLogs()
      setStatus('running')
    },
    onSuccess: (response) => {
      toast.success(response.data.replayed ? '已找到同一请求的任务记录，未重复启动' : '采集任务已启动')
      queryClient.invalidateQueries({ queryKey: ['crawlerStatus'] })
    },
    onError: (error: Error) => {
      setStatus('idle')
      toast.error(`启动失败：${errorMessage(error)}`)
    },
  })
}

export function useStopCrawler() {
  const queryClient = useQueryClient()
  const setStatus = useCrawlerStore((state) => state.setStatus)

  return useMutation({
    mutationFn: () => {
      const status = queryClient.getQueryData<CrawlerStatus>(['crawlerStatus'])
      if (!status?.task?.id) throw new Error('无法确认任务 ID，请刷新状态后重试')
      return crawlerApi.stop(status.task.id)
    },
    onMutate: () => {
      setStatus('stopping')
    },
    onSuccess: () => {
      toast.success('任务已停止')
      setStatus('idle')
      queryClient.invalidateQueries({ queryKey: ['crawlerStatus'] })
    },
    onError: (error: Error) => {
      toast.error(`停止失败：${errorMessage(error)}`)
    },
  })
}

export function useCrawlerLogs() {
  const setLogs = useCrawlerStore((state) => state.setLogs)

  return useQuery({
    queryKey: ['crawlerLogs'],
    queryFn: async () => {
      const { data } = await crawlerApi.getLogs(500)
      setLogs(data.logs)
      return data.logs
    },
    refetchInterval: 5000, // Durable HTTP fallback also repairs WebSocket gaps.
  })
}

export function usePlatforms() {
  return useQuery({
    queryKey: ['platforms'],
    queryFn: async () => {
      const { data } = await configApi.getPlatforms()
      return data.platforms
    },
    staleTime: Infinity,
  })
}

export function useConfigOptions() {
  return useQuery({
    queryKey: ['configOptions'],
    queryFn: async () => {
      const { data } = await configApi.getOptions()
      return data
    },
    staleTime: Infinity,
  })
}
