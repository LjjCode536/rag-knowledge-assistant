<script setup lang="ts">
/**
 * 运营统计页:核心指标卡 + 分桶趋势图 + Token 用量与估算成本。
 *
 * **没有样本时后端返回 `null`,界面必须显示"无样本"而非 0**(ADR-0015)。
 */
import { useQuery } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
import VChart from 'vue-echarts'

import { getCost, getStats } from '@/api'
import ErrorDetails from '@/components/ErrorDetails.vue'
import type { StatsBucket, StatsBucketKind } from '@/types/stats'
// ECharts 按需注册:只装用到的图表与组件,不打整包
// (包名从 echarts 根取,不走上文的 `@/` 别名 —— 那是项目内路径别名)
import { BarChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

/**
 * 统计区间:闭区间起点 / 开区间终点(起点含、终点不含)。
 *
 * `null` 表示该侧不设界(至今全部)。界面选择器的文案与后端口径一致,
 * 不把"终点当天"算进来 —— 否则界面口径与统计口径会差一天。
 */
const range = ref<[Date, Date] | null>(null)
/** 分桶粒度:UTC 自然小时 / 自然日。 */
const bucket = ref<StatsBucketKind>('day')

const rangeStart = computed<string | undefined>(() => range.value?.[0]?.toISOString())
const rangeEnd = computed<string | undefined>(() => range.value?.[1]?.toISOString())

/** 统计参数:区间 + 分桶粒度;界面选择即实际传入后端,不只摆在界面上。 */
const statsParams = computed(() => ({
  start: rangeStart.value,
  end: rangeEnd.value,
  bucket: bucket.value,
}))

const statsQuery = useQuery({
  queryKey: computed(() => ['stats', statsParams.value] as const),
  queryFn: () => getStats(statsParams.value),
})

const costQuery = useQuery({
  queryKey: computed(
    () => ['stats', 'cost', { start: rangeStart.value, end: rangeEnd.value }] as const,
  ),
  queryFn: () => getCost({ start: rangeStart.value, end: rangeEnd.value }),
})

const stats = computed(() => statsQuery.data.value)
const qa = computed(() => stats.value?.qa)
const cost = computed(() => stats.value?.cost)

/** 无样本时的显示值:拒答率 / 平均延迟在无问答时为 `null`。 */
function orNoSample(value: number | null | undefined, suffix = ''): string {
  return value === null || value === undefined ? '无样本' : `${value}${suffix}`
}

/** 表格行类型:模板插槽里不便写类型标注,在此声明一次。 */
type BucketRow = StatsBucket

/**
 * 分桶趋势图配置:问答量(柱)+ 拒答率(线)+ 平均延迟(线)。
 *
 * **没有样本时后端返回 `null`,界面必须显示"无样本"而非 0** —— 图表里同理:
 * 拒答率与平均延迟为 `null` 时给 `null`(断开该点),不补 0,否则会把"没有
 * 问答"画成"零拒答"。
 */
const trendOption = computed(() => {
  const buckets = stats.value?.buckets ?? []
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['问答量', '拒答率', '平均延迟(ms)'] },
    grid: { left: 48, right: 48, top: 32, bottom: 32 },
    xAxis: {
      type: 'category',
      data: buckets.map((bucket) => new Date(bucket.bucket_start).toLocaleString()),
    },
    yAxis: [
      { type: 'value', name: '问答量' },
      { type: 'value', name: '比率 / 延迟', splitLine: { show: false } },
    ],
    series: [
      {
        name: '问答量',
        type: 'bar',
        data: buckets.map((bucket) => bucket.qa.query_count),
      },
      {
        name: '拒答率',
        type: 'line',
        yAxisIndex: 1,
        data: buckets.map((bucket) => bucket.qa.refusal_rate),
        connectNulls: false,
      },
      {
        name: '平均延迟(ms)',
        type: 'line',
        yAxisIndex: 1,
        data: buckets.map((bucket) => bucket.qa.avg_latency_ms),
        connectNulls: false,
      },
    ],
  }
})
</script>

<template>
  <div>
    <ErrorDetails
      v-if="statsQuery.isError.value"
      :error="statsQuery.error.value"
    />

    <el-card
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>统计区间</strong>
      </template>
      <div
        style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap"
      >
        <el-date-picker
          v-model="range"
          type="datetimerange"
          range-separator="至"
          start-placeholder="起点(含)"
          end-placeholder="终点(不含)"
          data-testid="stats-range"
        />
        <el-radio-group
          v-model="bucket"
          data-testid="stats-bucket"
        >
          <el-radio-button value="day">
            按自然日
          </el-radio-button>
          <el-radio-button value="hour">
            按自然小时
          </el-radio-button>
        </el-radio-group>
      </div>
      <p
        class="page-hint"
        style="margin-top: 8px"
      >
        统计区间为闭区间起点 / 开区间终点(起点含、终点不含),不选区间即至今全部;
        分桶粒度为 UTC 自然日 / 自然小时。没有问答的桶不出现,界面不补零桶。
        <span v-if="stats">
          本次区间:{{ stats.start ? new Date(stats.start).toLocaleString() : '不限' }}
          至 {{ stats.end ? new Date(stats.end).toLocaleString() : '至今全部' }}。
        </span>
      </p>
    </el-card>

    <el-card
      v-if="stats"
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>知识库规模</strong>
      </template>
      <el-row :gutter="12">
        <el-col
          :xs="12"
          :sm="8"
        >
          <el-statistic
            title="文档数"
            :value="stats.document_count"
          />
        </el-col>
        <el-col
          :xs="12"
          :sm="8"
        >
          <el-statistic
            title="已索引文档数"
            :value="stats.indexed_document_count"
          />
        </el-col>
        <el-col
          :xs="12"
          :sm="8"
        >
          <el-statistic
            title="切片数"
            :value="stats.chunk_count"
          />
        </el-col>
      </el-row>
    </el-card>

    <el-card
      v-if="qa"
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>问答健康度</strong>
      </template>
      <el-row :gutter="12">
        <el-col
          :xs="12"
          :sm="6"
        >
          <el-statistic
            title="问答量"
            :value="qa.query_count"
          />
        </el-col>
        <el-col
          :xs="12"
          :sm="6"
        >
          <el-statistic
            title="拒答数"
            :value="qa.refused_count"
          />
        </el-col>
        <el-col
          :xs="12"
          :sm="6"
        >
          <div class="page-hint">
            拒答率
          </div>
          <div
            style="font-size: 22px; font-weight: 600"
            data-testid="stats-refusal-rate"
          >
            {{ orNoSample(qa.refusal_rate) }}
          </div>
        </el-col>
        <el-col
          :xs="12"
          :sm="6"
        >
          <div class="page-hint">
            平均延迟
          </div>
          <div
            style="font-size: 22px; font-weight: 600"
            data-testid="stats-avg-latency"
          >
            {{ orNoSample(qa.avg_latency_ms, ' ms') }}
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-card
      v-if="cost"
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>估算成本</strong>
      </template>
      <el-descriptions
        :column="2"
        border
      >
        <el-descriptions-item label="prompt 成本">
          {{ cost.prompt_cost }} {{ cost.currency }}
        </el-descriptions-item>
        <el-descriptions-item label="completion 成本">
          {{ cost.completion_cost }} {{ cost.currency }}
        </el-descriptions-item>
        <el-descriptions-item label="总成本(估算)">
          {{ cost.total_cost }} {{ cost.currency }}
        </el-descriptions-item>
        <el-descriptions-item label="Token 用量">
          {{ qa?.token_usage.total_tokens ?? 0 }}
        </el-descriptions-item>
      </el-descriptions>
      <p
        class="page-hint"
        style="margin-top: 8px"
      >
        估算成本是 Token 用量按配置单价的折算,不是上游账单金额。
      </p>
    </el-card>

    <el-card
      v-if="stats && stats.buckets.length > 0"
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>分桶趋势</strong>
      </template>
      <VChart
        class="trend-chart"
        :option="trendOption"
        autoresize
      />
      <el-table
        :data="stats.buckets"
        style="width: 100%; margin-top: 16px"
      >
        <el-table-column
          label="桶起点"
          min-width="180"
        >
          <template #default="{ row }: { row: BucketRow }">
            {{ new Date(row.bucket_start).toLocaleString() }}
          </template>
        </el-table-column>
        <el-table-column
          label="问答量"
          width="110"
        >
          <template #default="{ row }: { row: BucketRow }">
            {{ row.qa.query_count }}
          </template>
        </el-table-column>
        <el-table-column
          label="拒答数"
          width="110"
        >
          <template #default="{ row }: { row: BucketRow }">
            {{ row.qa.refused_count }}
          </template>
        </el-table-column>
        <el-table-column
          label="拒答率"
          width="120"
        >
          <template #default="{ row }: { row: BucketRow }">
            {{ orNoSample(row.qa.refusal_rate) }}
          </template>
        </el-table-column>
        <el-table-column
          label="平均延迟"
          width="130"
        >
          <template #default="{ row }: { row: BucketRow }">
            {{ orNoSample(row.qa.avg_latency_ms, ' ms') }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card
      v-if="costQuery.data.value"
      class="page-section"
      shadow="never"
    >
      <template #header>
        <strong>成本明细(Token 用量)</strong>
      </template>
      <el-descriptions
        :column="3"
        border
      >
        <el-descriptions-item label="prompt Token">
          {{ costQuery.data.value.token_usage.prompt_tokens }}
        </el-descriptions-item>
        <el-descriptions-item label="completion Token">
          {{ costQuery.data.value.token_usage.completion_tokens }}
        </el-descriptions-item>
        <el-descriptions-item label="合计 Token">
          {{ costQuery.data.value.token_usage.total_tokens }}
        </el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>
