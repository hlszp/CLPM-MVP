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

import { computed, ref, watch } from 'vue';

import {
  Badge,
  Descriptions,
  DescriptionsItem,
  Table,
  Tag,
  Tooltip,
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

/* withHistory 默认 true：Vue 对缺省 boolean prop 会强转 false，
 * 需 withDefaults 显式默认值保证原页面（不传该 prop）仍渲染历史子表 */
const props = withDefaults(
  defineProps<{
    /** 定级阈值（动态配置；空数组时 useScoreColor 降级默认阈值） */
    gradingThresholds?: MetricApi.GradingThresholdItem[];
    /** 目标行（快照 + 回路元数据；父层 v-if 保证非空） */
    record: LoopPerformanceRow;
    /** 是否渲染历史快照子表（原页详情抽屉 true；workbench360 剖面自带全量历史列表，传 false） */
    withHistory?: boolean;
  }>(),
  { gradingThresholds: () => [], withHistory: true },
);

const { modeLabelColor } = useLoopPalettes();

/** 等级（1~5；原页面 getGrade 判定链） */
function getGrade(score: null | number | undefined): null | number {
  const level = useScoreColor(score, props.gradingThresholds).level.value;
  return level === null ? null : Number(level);
}

// ===== R02-1（P3-D6b）：评估等级仅消费正式 P =====
// 对齐 08d classify_roster_state 的 scorable 判定：快照状态非 SUCCESS、
// score null（未计算）、或携带语义且 composite 非 COMPLETE（PARTIAL/
// UNASSESSABLE = P 证据不足）时不可定级——F 参考分不进等级。
const compositeState = computed(
  () =>
    props.record.metricsSemantic?.__composite__?.assessmentState ?? null,
);

/** 可定级的正式 P（不满足条件时 null → 等级显示 '—'） */
const gradeScore = computed<null | number>(() => {
  if (props.record.status !== 'SUCCESS') return null;
  const score = props.record.score;
  if (score === null || score === undefined) return null;
  if (compositeState.value && compositeState.value !== 'COMPLETE') {
    return null;
  }
  return score;
});

/** 等级不可用原因（'—' 单元格 Tooltip 说明，非 0 分） */
const gradeUnavailableReason = computed(() => {
  if (gradeScore.value !== null) return '';
  if (props.record.status !== 'SUCCESS') {
    return `快照状态 ${props.record.status}，无完整正式评分，不可定级（非 0 分）`;
  }
  if (props.record.score === null || props.record.score === undefined) {
    return '该窗口评分未计算（null，非 0 分），不可定级';
  }
  if (compositeState.value && compositeState.value !== 'COMPLETE') {
    return `综合评分 ${compositeState.value}（P 证据不足），不可定级`;
  }
  return '';
});

// ===== R02-2（P3-D6b，G3-R01 接通）：快速率正式/参考分行 =====
// record 携带 metricsSemantic.fast_rate 语义时拆"正式 F / 参考分"两行
// （参考行加徽标）；未携带（legacy 行）按原单值渲染。
// G3-R01 正式优先互斥守卫（08d 口径）：正式值在场（含真实 0 分）时残留
// referenceScore 不展示、不重复计参考——正式 0 是有效正式分。
const fastFormalValue = computed<null | number>(() => {
  const raw = props.record.metricsSemantic?.fast_rate?.value;
  return typeof raw === 'number' ? raw : null;
});
const fastReferenceScore = computed<null | number>(() => {
  if (fastFormalValue.value !== null) return null;
  const raw = props.record.metricsSemantic?.fast_rate?.details?.referenceScore;
  return typeof raw === 'number' ? raw : null;
});
/** 参考分行注记（R02-1：参考值不进等级，单独注记） */
const fastReferenceNote = computed(() =>
  fastReferenceScore.value === null
    ? ''
    : `快速性参考分 ${fastReferenceScore.value}% · 动态证据不足（静稳规则），不参与定级`,
);

// ===== G3-R01：语义链路状态（legacy 不可追溯 vs 新 profile 断链分开） =====
const SEMANTIC_LINK_LABELS: Record<string, { text: string; tone: string }> = {
  AVAILABLE: { text: '完整语义可追溯', tone: 'success' },
  NOT_TRACEABLE: {
    text: '历史不可追溯（该记录无完整语义明细）',
    tone: 'default',
  },
  UNLINKED: {
    text: '历史不可追溯（快照行未关联结果账本）',
    tone: 'default',
  },
  BROKEN: {
    text: '语义断链：该记录为新评估档位但缺少语义明细（链路缺陷，请上报）',
    tone: 'error',
  },
};
const semanticLink = computed(() => {
  const status = props.record.semanticLinkStatus ?? null;
  if (status === null) return null;
  return SEMANTIC_LINK_LABELS[status] ?? { text: status, tone: 'default' };
});

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
        <!-- R02-1：等级仅消费正式 P（SUCCESS + score 非 null + composite
             COMPLETE）；不满足时 '—' 并说明原因（非 0 分），F 参考不进等级 -->
        <Tag
          v-if="getGrade(gradeScore)"
          :color="GRADE_COLOR_MAP[getGrade(gradeScore)!]"
        >
          {{ GRADE_LABEL_MAP[getGrade(gradeScore)!] }}
        </Tag>
        <Tooltip v-else :title="gradeUnavailableReason">
          <span class="cursor-help text-gray-400">—</span>
        </Tooltip>
        <div
          v-if="fastReferenceNote"
          class="mt-1 text-xs text-gray-400"
        >
          {{ fastReferenceNote }}
        </div>
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
        <!-- R02-2：有语义（metricsSemantic.fast_rate.referenceScore 且无正式值）
             时拆"正式 F / 参考分"两行，参考行加徽标；正式值（含 0 分）在场
             优先单值渲染；未携带语义按原单值渲染 -->
        <template v-if="fastReferenceScore !== null">
          <div>
            <span class="text-gray-500">正式 F：</span>
            <span class="font-mono text-xs">{{
              formatNumber(fastFormalValue, '%')
            }}</span>
          </div>
          <div class="mt-1 flex items-center gap-1">
            <Tag color="default" class="m-0">参考</Tag>
            <span class="font-mono text-xs">
              参考分 {{ formatNumber(fastReferenceScore, '%') }}
            </span>
            <span class="text-xs text-gray-400">
              · 动态证据不足（静稳规则）
            </span>
          </div>
        </template>
        <template v-else>
          {{
            formatNumber(
              fastFormalValue !== null ? fastFormalValue : record.fastRate,
              '%',
            )
          }}
        </template>
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
      <!-- G3-R01：语义链路状态（legacy 不可追溯 / 新 profile 断链分开显示） -->
      <DescriptionsItem label="评估语义">
        <Tag v-if="semanticLink" :color="semanticLink.tone" class="m-0">
          {{ semanticLink.text }}
        </Tag>
        <span v-else class="text-gray-400">—</span>
        <Tooltip
          v-if="semanticLink"
          :title="`语义明细来源：不可变结果账本 record ${
            record.resultRecordId || '（未关联）'
          }；legacy 记录无完整语义属历史口径，新档位记录缺语义为链路缺陷`"
        >
          <span class="ml-1 cursor-help text-xs text-gray-400">?</span>
        </Tooltip>
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
