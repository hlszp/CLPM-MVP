<script lang="ts" setup>
/**
 * 可信度详情内容（P2-3 从 loop-performance.vue 抽取）
 *
 * 内容：评估概要（最新评估时间/数据源区间/状态/评分/算法版本）+
 * 数据质量与动态证据分列（P3-D6b R02-4：好值率/有效数据率 ≠ 可信度）+
 * 12 子指标数值表（3+1+8）+ 评估历史分页表（P3-D6b R02-3：latest 与
 * 历史并列标注窗口/算法版本，历史不再被 latest 替代）。
 * 数据源 GET /loops/{loopId}/confidence-latest + GET /performance/loops/snapshots
 * （latestOnly=false 分页，消费评估记录账本）；无记录时展示"暂无评估记录"空态。
 * 纯内容组件（无抽屉壳）：原页面 antd Drawer / workbench360 WbDrawer 共用。
 */
import type { KpiSnapshotItem, LoopConfidenceLatestItem } from '#/api/metric';

import { computed, ref, watch } from 'vue';

import {
  Badge,
  Descriptions,
  DescriptionsItem,
  Spin,
  Table,
  Tag,
} from 'ant-design-vue';

import { getLoopConfidenceLatestApi, getLoopSnapshotsApi } from '#/api/metric';
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

/** R02-4：数据质量区的"好值率"与 12 子指标表同源同格式 */
const goodValueRate = computed<null | number>(
  () => detail.value?.metrics?.good_value_rate?.value ?? null,
);

// ===== R02-3（P3-D6b）：评估历史分页（消费评估记录账本，latest 不再替代历史） =====
const HISTORY_PAGE_SIZE = 10;
const historyRows = ref<KpiSnapshotItem[]>([]);
const historyTotal = ref(0);
const historyPage = ref(1);
const historyLoading = ref(false);

async function loadHistory(loopId: string, page = 1) {
  historyLoading.value = true;
  try {
    const result = await getLoopSnapshotsApi({
      loopId,
      latestOnly: false,
      page,
      pageSize: HISTORY_PAGE_SIZE,
    });
    historyRows.value = result.items || [];
    historyTotal.value = result.total ?? 0;
    historyPage.value = page;
  } catch {
    historyRows.value = [];
    historyTotal.value = 0;
  } finally {
    historyLoading.value = false;
  }
}

const historyColumns = [
  { key: 'tsRange', title: '时间窗', width: 150 },
  { key: 'score', dataIndex: 'score', title: '综合评分', width: 90 },
  {
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    title: '可信度',
    width: 80,
  },
  { key: 'status', dataIndex: 'status', title: '状态', width: 80 },
  {
    key: 'algorithmVersion',
    dataIndex: 'algorithmVersion',
    title: '算法版本',
    width: 110,
  },
];

/** 评分颜色（原 scoreColor 判定链） */
function scoreColor(val: null | number | undefined): string {
  return useScoreColor(val).color.value;
}

watch(
  () => props.loopId,
  async (loopId) => {
    detail.value = null;
    historyRows.value = [];
    historyTotal.value = 0;
    historyPage.value = 1;
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
    // R02-3：latest 与历史并列（历史失败不阻塞 latest 概要展示）
    loadHistory(loopId, 1);
  },
  { immediate: true },
);
</script>

<template>
  <div class="lp-conf-content">
    <Spin :spinning="loading">
      <template v-if="detail">
        <!-- 评估概要（最新一条 latest 记录；R02-3：窗口/算法版本随行标注） -->
        <div class="mb-2 text-sm font-medium">评估概要（最新一条 latest）</div>
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
          <DescriptionsItem label="算法版本" :span="2">
            {{ detail.algorithmVersion || '—' }}
          </DescriptionsItem>
        </Descriptions>

        <!--
          R02-4（P3-D6b）：数据质量与动态证据分列（对齐 R11 语义）——
          好值率/有效数据率是数据质量；可信度（D/E=证据不足）反映动态
          证据充分性，两者不混展示、互不冒充。
        -->
        <div class="mb-2 mt-4 text-sm font-medium">
          数据质量与动态证据（分列）
        </div>
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Descriptions
            title="数据质量"
            :column="1"
            size="small"
            bordered
            :label-style="{ width: '110px' }"
          >
            <DescriptionsItem label="好值率">
              {{ formatNumber(goodValueRate, '%') }}
            </DescriptionsItem>
            <DescriptionsItem label="有效数据率">
              {{ formatRatio(detail.validRate) }}
            </DescriptionsItem>
          </Descriptions>
          <Descriptions
            title="动态证据充分性（可信度）"
            :column="1"
            size="small"
            bordered
            :label-style="{ width: '110px' }"
          >
            <DescriptionsItem label="可信度">
              <Badge
                v-if="detail.confidenceLevel"
                :color="CONFIDENCE_COLOR_MAP[detail.confidenceLevel]"
                :text="CONFIDENCE_LABEL_MAP[detail.confidenceLevel]"
              />
              <span v-else>—</span>
            </DescriptionsItem>
            <DescriptionsItem label="说明" :span="1">
              <span class="text-xs text-gray-400">
                可信度反映动态证据充分性（D/E
                =证据不足），不等于数据质量问题；数据质量看好值率/有效数据率
              </span>
            </DescriptionsItem>
          </Descriptions>
        </div>

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

        <!-- R02-3（P3-D6b）：评估历史（分页，历史版本可见，不再被 latest 替代） -->
        <div class="mb-2 mt-4 text-sm font-medium">
          评估历史（每回路全部评估记录，最新在前）
        </div>
        <Table
          :columns="historyColumns"
          :data-source="historyRows"
          :loading="historyLoading"
          :pagination="{
            current: historyPage,
            pageSize: HISTORY_PAGE_SIZE,
            total: historyTotal,
            size: 'small',
            showTotal: (t: number) => `共 ${t} 条`,
          }"
          row-key="tsStart"
          size="small"
          :scroll="{ x: 520 }"
          @change="
            (pag: { current?: number }) => {
              if (props.loopId) loadHistory(props.loopId, pag.current ?? 1);
            }
          "
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'tsRange'">
              <span class="font-mono text-xs">
                {{ formatTsRange(record.tsStart, record.tsEnd) }}
              </span>
            </template>
            <template v-else-if="column.key === 'score'">
              <span
                class="font-semibold"
                :style="{ color: scoreColor(record.score) }"
              >
                {{ formatNumber(record.score) }}
              </span>
            </template>
            <template v-else-if="column.key === 'confidenceLevel'">
              <Badge
                v-if="record.confidenceLevel"
                :color="CONFIDENCE_COLOR_MAP[record.confidenceLevel]"
                :text="CONFIDENCE_LABEL_MAP[record.confidenceLevel]"
              />
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'status'">
              <Tag
                :color="STATUS_COLOR_MAP[record.status] || 'default'"
                class="m-0"
              >
                {{ STATUS_LABEL_MAP[record.status] || record.status }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'algorithmVersion'">
              <span class="font-mono text-xs">
                {{ record.algorithmVersion || '—' }}
              </span>
            </template>
          </template>
          <template #emptyText>
            <span class="text-xs text-gray-400">
              该回路暂无历史评估记录（仅有上述 latest 一条或从未评估）
            </span>
          </template>
        </Table>
      </template>
      <div v-else-if="!loading" class="py-12 text-center text-gray-400">
        暂无评估记录。该回路尚未执行过 KPI 评估，请前往「评估任务」页触发评估
      </div>
    </Spin>
  </div>
</template>
