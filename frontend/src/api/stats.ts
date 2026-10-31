/**
 * 运营统计与成本统计端点的调用函数。
 *
 * 统计区间口径为**闭区间起点 / 开区间终点**(起点含、终点不含);`null`
 * 表示该侧不设界(至今全部)。
 */
import type { CostReport, KnowledgeBaseStats } from '@/types/stats'

import { request } from './client'

/** 运营统计一屏:知识库规模 + 问答健康度 + 估算成本 + 分桶序列。 */
export async function getStats(
  options: { start?: string; end?: string; bucket?: string } = {},
) {
  const result = await request<KnowledgeBaseStats>('/api/v1/stats', {
    query: { ...options },
  })
  return result.data
}

/** 成本明细:Token 用量与估算成本(按时间段查询)。 */
export async function getCost(options: { start?: string; end?: string } = {}) {
  const result = await request<CostReport>('/api/v1/stats/cost', {
    query: { ...options },
  })
  return result.data
}
