<script lang="ts" setup>
/**
 * 可信度详情内容（P2-3 从 loop-performance.vue 抽取）
 *
 * 内容：评估概要（最新评估时间/数据源区间/状态/评分/可信度/有效数据率/算法版本）+
 * 12 子指标数值表（3+1+8）。数据源 GET /loops/{loopId}/confidence-latest；
 * 无记录时展示"暂无评估记录"空态（与原页面一致）。
 * 纯内容组件（无抽屉壳）：原页面 antd Drawer / workbench360 WbDrawer 共用。
 */
import type { LoopConfidenceLatestItem } from '#/api/metric';

import { computed, ref, watch } from 'vue';

import {
  Badge,
  Descriptions,
  DescriptionsItem,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import { getLoopConfidenceLatestApi } from '#/api/metric';
import { useScoreColor } from '#/composables/use-score-color';

import {
  CONF_METRIC_COLUMNS,
  CONFIDENCE_COLOR_MAP,
  CONFIDENCE_LABEL_MAP,
  CONFIDENCE_METRIC_META,
  formatFullTime,
  formatNumber,
  formatRatio,
  formatTsRange,
  STATUS_COLOR_MAP,
  STATUS_LABEL_MAP,
} from './loop-performance-shared';

defineOptions({ name: 'LoopConfidenceContent' });

const props = defineProps<{
  /** 回路 ID（空则不加载） */
  loopId?: null | string;
}>();

const loading = ref(false);
const detail = ref<LoopConfidenceLatestItem | null>(null);

/** 12 子指标表格行（原 confMetricRows 逻辑搬移） */
const metricRows = computed(() => {
  const metrics = detail.value?.metrics ?? {};
  return CONFIDENCE_METRIC_META.map((meta) => ({
    ...meta,
    value: metrics[meta.key]?.value ?? null,
  }));
});

/** 评分颜色（原 scoreColor 判定链） */
function scoreColor(val: null | number | undefined): string {
  return useScoreColor(val).color.value;
}

watch(
  () => props.loopId,
  async (loopId) => {
    detail.value = null;
    if (!loopId) return;
    loading.value = true;
    try {
      detail.value = await getLoopConfidenceLatestApi(loopId);
    } catch (error) {
      // 错误 toast 由 api/request.ts 拦截器统一弹出，抽屉内展示"暂无评估记录"
      console.error('加载可信度详情失败:', error);
    } finally {
      loading.value = false;
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="lp-conf-content">
    <Spin :spinning="loading">
      <template v-if="detail">
        <!-- 评估概要 -->
        <div class="mb-2 text-sm font-medium">评估概要</div>
        <Descriptions
          :column="2"
          size="small"
          bordered
          :label-style="{ width: '110px' }"
        >
          <DescriptionsItem label="最新评估时间">
            <span class="font-mono text-xs">
              {{ formatFullTime(detail.evalTime) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="数据源时间区间">
            <span class="font-mono text-xs">
              {{ formatTsRange(detail.dataTsStart, detail.dataTsEnd) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="评估状态">
            <Tag
              :color="STATUS_COLOR_MAP[detail.status] || 'default'"
              class="m-0"
            >
              {{ STATUS_LABEL_MAP[detail.status] || detail.status }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="综合评分">
            <span
              class="font-semibold"
              :style="{ color: scoreColor(detail.score) }"
            >
              {{ formatNumber(detail.score) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="可信度">
            <Badge
              v-if="detail.confidenceLevel"
              :color="CONFIDENCE_COLOR_MAP[detail.confidenceLevel]"
              :text="CONFIDENCE_LABEL_MAP[detail.confidenceLevel]"
            />
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="有效数据率">
            {{ formatRatio(detail.validRate) }}
          </DescriptionsItem>
          <DescriptionsItem label="算法版本" :span="2">
            {{ detail.algorithmVersion || '—' }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 12 子指标明细（可信度统一 Phase 2：仅展示计算值，可信度统一为回路级） -->
        <div class="mb-2 mt-4 text-sm font-medium">子指标数值（3+1+8）</div>
        <Table
          :columns="CONF_METRIC_COLUMNS"
          :data-source="metricRows"
          :pagination="false"
          row-key="key"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'value'">
              <span class="font-mono text-xs">
                {{ formatNumber(record.value, record.unit) }}
              </span>
            </template>
          </template>
        </Table>
      </template>
      <div v-else-if="!loading" class="py-12 text-center text-gray-400">
        暂无评估记录。该回路尚未执行过 KPI 评估，请前往「评估任务」页触发评估
      </div>
    </Spin>
  </div>
</template>
