<script lang="ts" setup>
/**
 * 回路性能详情内容（P2-3 从 loop-performance.vue 抽取）
 *
 * 内容：回路基本信息 + 8 大 KPI + 诊断/扩展指标 + 评估信息 +
 * 历史快照子表（最近 10 条，withHistory 时按 record.loopId 自动加载）。
 * 纯内容组件（无抽屉壳）：原页面以 antd Drawer 包裹，workbench360 评估
 * 详情抽屉以 WbDrawer（v3 §7 拖宽规格）包裹，两处复用同一实现。
 */
import type { MetricApi } from '#/api/metric';
import type { KpiSnapshotItem } from '#/api/metric';

import { ref, watch } from 'vue';

import {
  Badge,
  Descriptions,
  DescriptionsItem,
  Table,
  Tag,
} from 'ant-design-vue';

import { getLoopSnapshotsApi } from '#/api/metric';
import { ClpmInfoTip } from '#/components/clpm';
import {
  LOOP_TYPE_LABEL_MAP,
  useLoopPalettes,
} from '#/composables/use-loop-palettes';
import { useScoreColor } from '#/composables/use-score-color';
import { KPI_TERM_EXPLANATIONS } from '#/constants/clpm-ui';

import {
  CONFIDENCE_COLOR_MAP,
  CONFIDENCE_LABEL_MAP,
  CONTROL_TYPE_MAP,
  DETAIL_HISTORY_COLUMNS,
  formatFullTime,
  formatNumber,
  formatRatio,
  formatTsRange,
  getMetricValue,
  GRADE_COLOR_MAP,
  GRADE_LABEL_MAP,
  type LoopPerformanceRow,
  STATUS_COLOR_MAP,
  STATUS_LABEL_MAP,
} from './loop-performance-shared';

defineOptions({ name: 'LoopPerformanceDetailContent' });

const props = defineProps<{
  /** 定级阈值（动态配置；空数组时 useScoreColor 降级默认阈值） */
  gradingThresholds?: MetricApi.GradingThresholdItem[];
  /** 目标行（快照 + 回路元数据；父层 v-if 保证非空） */
  record: LoopPerformanceRow;
  /** 是否渲染历史快照子表（原页详情抽屉 true；workbench360 剖面自带全量历史列表，传 false） */
  withHistory?: boolean;
}>();

const { modeLabelColor } = useLoopPalettes();

/** 等级（1~5；原页面 getGrade 判定链） */
function getGrade(score: null | number | undefined): null | number {
  const level = useScoreColor(score, props.gradingThresholds).level.value;
  return level === null ? null : Number(level);
}

/** 评分颜色（原页面 scoreColor 判定链） */
function scoreColor(val: null | number | undefined): string {
  return useScoreColor(val, props.gradingThresholds).color.value;
}

/** 历史快照子表（最近 10 条；原 loadDrawerHistory 逻辑搬移） */
const history = ref<KpiSnapshotItem[]>([]);
const historyLoading = ref(false);

async function loadHistory(loopId: string) {
  historyLoading.value = true;
  try {
    const result = await getLoopSnapshotsApi({
      loopId,
      latestOnly: false,
      page: 1,
      pageSize: 10,
    });
    history.value = result.items || [];
  } catch {
    history.value = [];
  } finally {
    historyLoading.value = false;
  }
}

watch(
  () => [props.record?.loopId, props.withHistory] as const,
  ([loopId, withHistory]) => {
    if (withHistory && loopId) {
      history.value = [];
      loadHistory(loopId);
    } else {
      history.value = [];
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="lp-detail-content">
    <!-- 回路基本信息 -->
    <div class="mb-2 text-sm font-medium">回路基本信息</div>
    <Descriptions
      :column="2"
      size="small"
      bordered
      :label-style="{ width: '120px' }"
    >
      <DescriptionsItem label="回路编号">
        {{ record.loopTagName || '—' }}
      </DescriptionsItem>
      <DescriptionsItem label="回路名称">
        {{ record.description || '—' }}
      </DescriptionsItem>
      <DescriptionsItem label="回路类型">
        {{ LOOP_TYPE_LABEL_MAP[record.loopType ?? 'OTHER'] ?? '—' }}
      </DescriptionsItem>
      <DescriptionsItem label="控制类型">
        {{
          record.controlType
            ? (CONTROL_TYPE_MAP[record.controlType] ?? record.controlType)
            : '—'
        }}
      </DescriptionsItem>
      <DescriptionsItem label="控制方式">
        <Tag
          v-if="record.controlMode"
          :color="modeLabelColor(record.controlMode)"
        >
          {{ record.controlMode }}
        </Tag>
        <span v-else>—</span>
      </DescriptionsItem>
      <DescriptionsItem label="评估等级">
        <Tag
          v-if="getGrade(record.score)"
          :color="GRADE_COLOR_MAP[getGrade(record.score)!]"
        >
          {{ GRADE_LABEL_MAP[getGrade(record.score)!] }}
        </Tag>
        <span v-else>—</span>
      </DescriptionsItem>
      <DescriptionsItem label="PV 量程">
        {{
          record.loopMeta?.pvRange
            ? `${record.loopMeta.pvRange.min ?? '—'} ~ ${
                record.loopMeta.pvRange.max ?? '—'
              }${record.loopMeta.pvUnit ? ` ${record.loopMeta.pvUnit}` : ''}`
            : '—'
        }}
      </DescriptionsItem>
      <DescriptionsItem label="OP 量程">
        {{
          record.loopMeta?.opRange
            ? `${record.loopMeta.opRange.min ?? '—'} ~ ${
                record.loopMeta.opRange.max ?? '—'
              }${record.loopMeta.opUnit ? ` ${record.loopMeta.opUnit}` : ''}`
            : '—'
        }}
      </DescriptionsItem>
    </Descriptions>

    <!-- 8 大性能评估 KPI -->
    <div class="mb-2 mt-4 text-sm font-medium">8 大性能评估 KPI 指标</div>
    <Descriptions
      :column="2"
      size="small"
      bordered
      :label-style="{ width: '120px' }"
    >
      <DescriptionsItem>
        <template #label>
          综合评分
          <ClpmInfoTip
            :term="KPI_TERM_EXPLANATIONS.compositeScore?.term"
            :tip="KPI_TERM_EXPLANATIONS.compositeScore?.short ?? ''"
            :detail="KPI_TERM_EXPLANATIONS.compositeScore?.detail"
          />
        </template>
        <span
          class="font-semibold"
          :style="{ color: scoreColor(record.score) }"
        >
          {{ formatNumber(record.score) }}
        </span>
      </DescriptionsItem>
      <DescriptionsItem>
        <template #label>
          准确率
          <ClpmInfoTip
            :term="KPI_TERM_EXPLANATIONS.accuracyScore?.term"
            :tip="KPI_TERM_EXPLANATIONS.accuracyScore?.short ?? ''"
            :detail="KPI_TERM_EXPLANATIONS.accuracyScore?.detail"
          />
        </template>
        {{ formatNumber(record.accuracyRate, '%') }}
      </DescriptionsItem>
      <DescriptionsItem>
        <template #label>
          快速率
          <ClpmInfoTip
            :term="KPI_TERM_EXPLANATIONS.responseScore?.term"
            :tip="KPI_TERM_EXPLANATIONS.responseScore?.short ?? ''"
            :detail="KPI_TERM_EXPLANATIONS.responseScore?.detail"
          />
        </template>
        {{ formatNumber(record.fastRate, '%') }}
      </DescriptionsItem>
      <DescriptionsItem>
        <template #label>
          平稳率
          <ClpmInfoTip
            :term="KPI_TERM_EXPLANATIONS.steadyScore?.term"
            :tip="KPI_TERM_EXPLANATIONS.steadyScore?.short ?? ''"
            :detail="KPI_TERM_EXPLANATIONS.steadyScore?.detail"
          />
        </template>
        {{ formatNumber(record.steadyRate, '%') }}
      </DescriptionsItem>
      <DescriptionsItem>
        <template #label>
          有效自控率
          <ClpmInfoTip
            :term="KPI_TERM_EXPLANATIONS.effectiveAutoRate?.term"
            :tip="KPI_TERM_EXPLANATIONS.effectiveAutoRate?.short ?? ''"
            :detail="KPI_TERM_EXPLANATIONS.effectiveAutoRate?.detail"
          />
        </template>
        {{ formatNumber(record.effectiveAutoRate, '%') }}
      </DescriptionsItem>
      <DescriptionsItem label="自控率">
        {{ formatNumber(record.autoModeRate, '%') }}
      </DescriptionsItem>
      <DescriptionsItem label="好值率">
        {{ formatNumber(record.goodValueRate, '%') }}
      </DescriptionsItem>
      <DescriptionsItem label="振荡率">
        {{ formatNumber(record.oscillationRate, '%') }}
      </DescriptionsItem>
    </Descriptions>

    <!-- 诊断与扩展指标（不参与评分） -->
    <div class="mb-2 mt-4 text-sm font-medium">诊断与扩展指标</div>
    <Descriptions
      :column="2"
      size="small"
      bordered
      :label-style="{ width: '120px' }"
    >
      <DescriptionsItem label="饱和率">
        {{ formatNumber(record.saturationRate, '%') }}
      </DescriptionsItem>
      <DescriptionsItem label="输出跳变率">
        {{ formatNumber(record.outputTravelIndex) }}
      </DescriptionsItem>
      <DescriptionsItem label="阀门粘滞指数">
        {{ formatNumber(record.stictionIndex) }}
      </DescriptionsItem>
      <DescriptionsItem label="理想稳定时间">
        {{ formatNumber(record.idealSettlingTime, 's') }}
      </DescriptionsItem>
      <DescriptionsItem label="稳定时间" :span="2">
        {{ formatNumber(record.settlingTime, 's') }}
      </DescriptionsItem>
    </Descriptions>

    <!-- 可信度 + 时间窗口 + 评估时间 -->
    <div class="mb-2 mt-4 text-sm font-medium">评估信息</div>
    <Descriptions
      :column="2"
      size="small"
      bordered
      :label-style="{ width: '120px' }"
    >
      <DescriptionsItem label="可信度">
        <Badge
          v-if="record.confidenceLevel"
          :color="CONFIDENCE_COLOR_MAP[record.confidenceLevel]"
          :text="CONFIDENCE_LABEL_MAP[record.confidenceLevel]"
        />
        <span v-else>—</span>
      </DescriptionsItem>
      <DescriptionsItem label="评估状态">
        <Tag :color="STATUS_COLOR_MAP[record.status] || 'default'">
          {{ STATUS_LABEL_MAP[record.status] || record.status }}
        </Tag>
      </DescriptionsItem>
      <DescriptionsItem label="时间窗口">
        <span class="font-mono text-xs">
          {{ formatTsRange(record.tsStart, record.tsEnd) }}
        </span>
      </DescriptionsItem>
      <DescriptionsItem label="评估时间">
        <span class="font-mono text-xs">
          {{ formatFullTime(record.tsEnd) }}
        </span>
      </DescriptionsItem>
      <DescriptionsItem label="有效数据率">
        {{ formatRatio(record.validRate) }}
      </DescriptionsItem>
      <DescriptionsItem label="算法版本">
        {{ record.algorithmVersion || '—' }}
      </DescriptionsItem>
    </Descriptions>

    <!-- 历史快照子表（该回路最近 10 条评估记录；withHistory=false 时不渲染） -->
    <template v-if="withHistory !== false">
      <div class="mb-2 mt-4 text-sm font-medium">历史快照（最近 10 条）</div>
      <Table
        :columns="DETAIL_HISTORY_COLUMNS"
        :data-source="history"
        :loading="historyLoading"
        :pagination="false"
        row-key="tsStart"
        size="small"
        :scroll="{ x: 680 }"
      >
        <template #bodyCell="{ column, record: row }">
          <template v-if="column.key === 'tsRange'">
            <span class="font-mono text-xs">
              {{
                formatTsRange(
                  (row as KpiSnapshotItem).tsStart,
                  (row as KpiSnapshotItem).tsEnd,
                )
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'score'">
            <span
              class="font-semibold"
              :style="{
                color: scoreColor((row as KpiSnapshotItem).score),
              }"
            >
              {{ formatNumber((row as KpiSnapshotItem).score) }}
            </span>
          </template>
          <template
            v-else-if="
              (
                [
                  'accuracyRate',
                  'fastRate',
                  'steadyRate',
                  'effectiveAutoRate',
                ] as string[]
              ).includes(column.key as string)
            "
          >
            <span class="font-mono text-xs">
              {{
                formatNumber(
                  getMetricValue(
                    row as KpiSnapshotItem,
                    column.dataIndex as string,
                  ),
                  '%',
                )
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidenceLevel'">
            <Badge
              v-if="(row as KpiSnapshotItem).confidenceLevel"
              :color="
                CONFIDENCE_COLOR_MAP[(row as KpiSnapshotItem).confidenceLevel!]
              "
              :text="
                CONFIDENCE_LABEL_MAP[(row as KpiSnapshotItem).confidenceLevel!]
              "
            />
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag
              :color="
                STATUS_COLOR_MAP[(row as KpiSnapshotItem).status] || 'default'
              "
              class="m-0"
            >
              {{
                STATUS_LABEL_MAP[(row as KpiSnapshotItem).status] ||
                (row as KpiSnapshotItem).status
              }}
            </Tag>
          </template>
        </template>
      </Table>
    </template>
  </div>
</template>
