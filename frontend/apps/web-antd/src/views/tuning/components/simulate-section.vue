<script lang="ts" setup>
/**
 * 整定工作台 · 锚点③ 仿真对比（09 设计方案 §4.3/§6.2）
 *
 * 当前 PID + 勾选推荐组（1~5 组）在同一辨识模型上做 SP 阶跃响应仿真：
 * 曲线叠加图（当前=灰，推荐组最多 5 条按序取色）+ 量化指标表
 * （上升时间/超调量/调节时间/ITAE）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningWorkbenchContext } from '../composables/use-tuning-workbench';

import { computed, nextTick, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Alert, Button, Card, Table, Tag, Tooltip } from 'ant-design-vue';

import {
  limitationDesc,
  limitationItemsOf,
  limitationTitle,
  robustOverallOf,
} from '#/api/tuning';
import { useClpmRoles } from '#/composables/use-clpm-roles';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

import { fmtNum2, tuningAlgoLabel } from '../constants';

const props = defineProps<{ ctx: TuningWorkbenchContext }>();
const { ctx } = props;

const { chartTextColor, chartSplitLineColor } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/** 候选配色（当前=灰；推荐组 5 色，与曲线顺序一致） */
const SERIES_COLORS = [
  '#6b7280', // 当前 PID（灰）
  '#1d4ed8', // 推荐 1（蓝）
  '#b45309', // 推荐 2（橙）
  '#0f766e', // 推荐 3（青）
  '#7c3aed', // 推荐 4（紫）
  '#be185d', // 推荐 5（玫红）
];

const simResult = computed(() => ctx.simResult.value);
const candidates = computed(
  () => ctx.simResult.value?.candidateResponses ?? [],
);

function render() {
  const res = simResult.value;
  if (!res) return;
  const series = (res.candidateResponses ?? []).map((c, i) => ({
    name: c.label,
    type: 'line' as const,
    showSymbol: false,
    data: c.response.pv,
    lineStyle: {
      width: c.label === '当前 PID' ? 1.5 : 2,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
    },
    itemStyle: { color: SERIES_COLORS[i % SERIES_COLORS.length] },
  }));
  nextTick(() => {
    renderEcharts({
      grid: { bottom: 48, left: 56, right: 16, top: 32 },
      legend: { textStyle: { color: chartTextColor.value }, top: 4 },
      series,
      tooltip: getTooltipPreset(),
      xAxis: {
        type: 'category',
        data: res.timestamps.map(String),
        name: '秒',
        axisLabel: { color: chartTextColor.value },
      },
      yAxis: {
        type: 'value',
        name: 'PV（增量）',
        axisLabel: { color: chartTextColor.value },
        splitLine: { lineStyle: { color: chartSplitLineColor.value } },
      },
    });
  });
}

watch(simResult, render);

const metricColumns = [
  { dataIndex: 'label', key: 'label', title: '参数组' },
  { dataIndex: 'riseTime', key: 'riseTime', title: '上升时间 (s)' },
  { dataIndex: 'overshoot', key: 'overshoot', title: '超调量 (%)' },
  { dataIndex: 'settlingTime', key: 'settlingTime', title: '调节时间 (s)' },
  { dataIndex: 'itae', key: 'itae', title: 'ITAE' },
];

const metricRows = computed(() =>
  candidates.value.map((c) => ({
    key: c.label,
    label: c.label,
    riseTime: c.metrics.riseTime ?? '—',
    overshoot: c.metrics.overshoot ?? '—',
    settlingTime: c.metrics.settlingTime ?? '—',
    itae: c.metrics.itae ?? '—',
  })),
);

/** P2 IA优化：入口按钮 Tooltip 文案（L0/L1 禁用原因优先；L2 警告；空=无 tooltip） */
const simulateBtnTooltip = computed(() => {
  if (ctx.tuningDisabledReason.value) return ctx.tuningDisabledReason.value;
  if (ctx.tuningWarningReason.value) return ctx.tuningWarningReason.value;
  return '';
});

/** P2 IA优化：Toast + 仿真运行封装（G3：点按钮弹 fitness Toast） */
// W8 绑定语义（P4-02 尾批）：开始仿真按 tuning:simulate 码显隐
// （种子四角色 == 后端 /tuning/simulate|compare require_perms，零行为变化）
const { hasPerm } = useClpmRoles();
const canSimulate = computed(() => hasPerm('tuning:simulate'));

function handleSimulate() {
  ctx.showFitnessToast('simulate');
  ctx.runSimulate();
}

/* ===== ③b 鲁棒验证证据（G3 增量纠偏 R01/R02/R03 页面接线） ===== */
// 鲁棒验证与推荐/矩阵同门禁（后端 /tuning/tune/robustness require_perms tuning:tune）
const canRobust = computed(() => hasPerm('tuning:tune'));

const robustReport = computed(() => ctx.robustReport.value);
/** R01 总体三态（robustOverallOf 单源：窗内证据不足不得显示总体通过） */
const robustOverall = computed(() => robustOverallOf(robustReport.value));
const OVERALL_VIEW = {
  FAIL: { color: 'error', text: 'FAIL · 未通过' },
  INCONCLUSIVE: {
    color: 'warning',
    text: 'INCONCLUSIVE · 证据不足（不得视为通过）',
  },
  PASS: { color: 'success', text: 'PASS · 通过' },
  UNKNOWN: { color: 'default', text: '状态未知' },
} as const;
const overallView = computed(() => OVERALL_VIEW[robustOverall.value]);

const robustEvidence = computed(
  () => robustReport.value?.simulationEvidence ?? null,
);
const robustThresholds = computed(
  () => robustReport.value?.frozenThresholds ?? null,
);
const robustSources = computed(() => robustReport.value?.sources ?? null);
const setBasis = computed(() => robustReport.value?.setBasis ?? null);
const setConstruction = computed(
  () => setBasis.value?.setConstruction ?? null,
);
/** R03 按值判空（null/缺键/空数组 → []，不制造警告） */
const robustLimitations = computed(() =>
  limitationItemsOf(robustReport.value?.applicabilityLimitations),
);
const lowSnr = computed(() => robustReport.value?.lowSnrDisclaimer ?? null);

/** R02 原因码中文标签（原始码同显——码为稳定身份） */
const SET_REASON_LABELS: Record<string, string> = {
  ALL_DIMENSIONS_FIXED: '全部维度固定（名义单点集合）',
  DEGENERATE_INTERVALS_FIXED: '退化区间按固定维处理（固定于名义值）',
  FILL_ATTEMPT_BUDGET_EXHAUSTED: '补样尝试预算耗尽',
};

const robustPidText = computed(() => {
  const p = robustReport.value?.verifiedPid;
  if (!p) return '—';
  return `P ${fmtNum2(p.kp)} / I ${fmtNum2(p.ti)} / D ${fmtNum2(p.td)}`;
});

/** 证据不足成员序号（超 8 个显式截断标注，不静默截断） */
const insufficientIndexesText = computed(() => {
  const idx = robustEvidence.value?.insufficientMemberIndexes;
  if (!Array.isArray(idx) || idx.length === 0) return '';
  const shown = idx.slice(0, 8).join('、');
  return idx.length > 8 ? `${shown} …（共 ${idx.length} 个）` : shown;
});

const indicatorRows = computed(() => {
  const ind = robustReport.value?.indicators;
  if (!ind) return [];
  const rows: {
    extra: string;
    key: string;
    label: string;
    pass: boolean;
    value: string;
  }[] = [];
  if (ind.gainMargin) {
    rows.push({
      key: 'gm',
      label: '增益裕度 GM',
      value: `最坏 ${fmtNum2(ind.gainMargin.worst)} ≥ ${fmtNum2(ind.gainMargin.threshold)}`,
      pass: !!ind.gainMargin.pass,
      extra: '',
    });
  }
  if (ind.phaseMargin) {
    rows.push({
      key: 'pm',
      label: '相位裕度 PM',
      value: `最坏 ${fmtNum2(ind.phaseMargin.worst)}° ≥ ${fmtNum2(ind.phaseMargin.threshold)}°`,
      pass: !!ind.phaseMargin.pass,
      extra: '',
    });
  }
  if (ind.overshoot) {
    const n = ind.overshoot.evidenceInsufficientMembers ?? 0;
    rows.push({
      key: 'os',
      label: '超调量',
      value: `最坏 ${fmtNum2(ind.overshoot.worstPct)}% ≤ ${fmtNum2(ind.overshoot.thresholdPct)}%`,
      pass: !!ind.overshoot.pass,
      extra:
        n > 0
          ? `超调状态 ${ind.overshoot.status ?? '—'} · 证据不足成员 ${n} 个`
          : `超调状态 ${ind.overshoot.status ?? '—'}`,
    });
  }
  if (ind.opLimits) {
    rows.push({
      key: 'op',
      label: 'OP 限幅',
      value: `最坏饱和 ${ind.opLimits.worstSaturationSteps ?? '—'} 步`,
      pass: !!ind.opLimits.pass,
      extra: '',
    });
  }
  return rows;
});
</script>

<template>
  <Card id="tuning-anchor-simulate" size="small" class="tuning-section">
    <template #title>
      <span class="section-title">③ 仿真对比</span>
      <span class="ml-2 text-xs font-normal text-neutral-400">
        SP 阶跃响应（RK4 闭环仿真）
      </span>
    </template>
    <template #extra>
      <Tooltip
        title="在可信模型集合（终验模型 ±95% CI，≤64 成员）上按 R15 冻结指标验证推荐组：④已选定组优先，否则首个勾选算法行"
        placement="bottomRight"
      >
        <Button
          v-if="canRobust"
          size="small"
          class="mr-2"
          :loading="ctx.robustLoading.value"
          :disabled="!ctx.canRunRobustness.value"
          data-testid="robust-run-btn"
          @click="ctx.runRobustness()"
        >
          鲁棒验证
        </Button>
      </Tooltip>
      <Tooltip
        v-if="simulateBtnTooltip"
        :title="simulateBtnTooltip"
        placement="bottomRight"
      >
        <Button
          v-if="canSimulate"
          type="primary"
          size="small"
          :loading="ctx.simulating.value"
          :disabled="!ctx.canSimulate.value"
          @click="handleSimulate"
        >
          开始仿真
        </Button>
      </Tooltip>
      <Button
        v-else-if="canSimulate"
        type="primary"
        size="small"
        :loading="ctx.simulating.value"
        :disabled="!ctx.canSimulate.value"
        @click="handleSimulate"
      >
        开始仿真
      </Button>
    </template>

    <Alert
      v-if="ctx.simError.value"
      class="mb-2"
      type="error"
      :message="ctx.simError.value"
      show-icon
    />
    <Alert
      v-else-if="!ctx.canSimulate.value && !simResult"
      type="info"
      message="请先在②整定矩阵勾选 1~5 组推荐参数"
      show-icon
    />

    <template v-if="simResult">
      <EchartsUI ref="chartRef" style="width: 100%; height: 320px" />
      <Table
        class="mt-3"
        :columns="metricColumns"
        :data-source="metricRows"
        :pagination="false"
        size="small"
      />
    </template>

    <!-- R01/R02/R03：可信集合鲁棒验证证据（G3 增量纠偏页面接线；与仿真对比相互独立） -->
    <Alert
      v-if="ctx.robustError.value"
      class="mt-3"
      type="error"
      :message="ctx.robustError.value"
      show-icon
    />
    <div v-if="robustReport" class="robust-panel" data-testid="robust-panel">
      <div class="robust-head">
        <span class="robust-title">鲁棒性验证（可信模型集合 · R15 冻结指标）</span>
        <Tag :color="overallView.color" data-testid="robust-overall-status">{{
          overallView.text
        }}</Tag>
        <span class="text-xs text-neutral-400">
          验证 {{ tuningAlgoLabel(robustReport.algorithm) }} ·
          {{ robustPidText }}
          <template v-if="robustReport.verifiedPidSource"
            >（{{ robustReport.verifiedPidSource }}）</template
          >
        </span>
      </div>
      <div class="mt-1 text-xs text-neutral-400">
        验证范围：被测可信集合成员（≤{{ robustThresholds?.maxSetMembers ?? '—' }}）与名义扰动响应；不扩大为高阶模型或真实对象的全工况保证
      </div>

      <Alert
        v-if="robustOverall === 'INCONCLUSIVE'"
        class="mt-2"
        type="warning"
        data-testid="robust-inconclusive-note"
        :message="
          robustEvidence?.note ||
          '窗内证据不足：窗内最大超调不能代表全时域峰值，不得视为总体通过'
        "
        :description="
          insufficientIndexesText
            ? `证据不足成员序号：${insufficientIndexesText}`
            : undefined
        "
        show-icon
      />

      <!-- 指标判定（冻结阈值） -->
      <div v-if="indicatorRows.length > 0" class="robust-block" data-testid="robust-indicators">
        <div class="robust-block__title">指标判定（R15 冻结阈值）</div>
        <div v-for="row in indicatorRows" :key="row.key" class="robust-line">
          <span class="k">{{ row.label }}</span>
          <span class="clpm-num">{{ row.value }}</span>
          <Tag :color="row.pass ? 'success' : 'error'">{{
            row.pass ? '达标' : '未达标'
          }}</Tag>
          <span v-if="row.extra" class="text-xs text-neutral-400">{{
            row.extra
          }}</span>
        </div>
      </div>

      <!-- 记录/模型身份与预算（R01：随证据同显） -->
      <div class="robust-block" data-testid="robust-identity">
        <div class="robust-block__title">身份与预算</div>
        <div class="robust-line">
          <span class="k">来源记录</span
          ><span class="v">{{ robustSources?.sourceRecordId ?? '—' }}</span>
        </div>
        <div class="robust-line">
          <span class="k">模型版本</span
          ><span class="v">{{ robustSources?.modelVersionId ?? '—' }}</span>
        </div>
        <div class="robust-line">
          <span class="k">数据快照</span
          ><span class="v">{{ robustSources?.datasetSnapshotId ?? '—' }}</span>
        </div>
        <div class="robust-line">
          <span class="k">算法版本</span
          ><span class="v">{{ robustSources?.algorithmVersion ?? '—' }}</span>
        </div>
        <div class="robust-line">
          <span class="k">仿真引擎</span
          ><span class="v">{{
            robustSources?.simulationEngineVersion ?? '—'
          }}</span>
        </div>
        <div class="robust-line">
          <span class="k">预算</span
          ><span class="v"
            >成员上限 {{ robustThresholds?.maxSetMembers ?? '—' }} · 单成员仿真
            {{ robustEvidence?.simDurationSeconds ?? '—' }}s · 集合成员
            {{ robustReport.memberCount ?? '—' }}</span
          >
        </div>
      </div>

      <!-- R02：可信集合构成（请求/实际成员、退化维、次数/预算、原因码） -->
      <div
        v-if="setBasis"
        class="robust-block"
        data-testid="robust-set-construction"
      >
        <div class="robust-block__title">可信集合构成</div>
        <div class="robust-line">
          <span class="k">集合来源</span
          ><span class="v">{{ setBasis.source ?? '—' }}</span>
        </div>
        <div class="robust-line">
          <span class="k">成员（实际/请求）</span
          ><span class="clpm-num"
            >{{
              setConstruction?.actualMemberCount ?? setBasis.memberCount ?? '—'
            }}
            / {{ setConstruction?.requestedMembers ?? '—' }}</span
          >
        </div>
        <div class="robust-line">
          <span class="k">补样尝试</span
          ><span class="clpm-num">{{ setConstruction?.fillAttempts ?? '—' }}</span>
        </div>
        <div
          v-if="setConstruction?.degenerateKeysFixed?.length"
          class="robust-line"
        >
          <span class="k">退化维（固定于名义值）</span
          ><span class="v">{{
            setConstruction.degenerateKeysFixed.join('、')
          }}</span>
        </div>
        <div v-if="setConstruction?.reasonCodes?.length" class="robust-line">
          <span class="k">原因码</span>
          <span>
            <Tag
              v-for="code in setConstruction.reasonCodes"
              :key="code"
              color="warning"
              class="mr-1"
              >{{ code }} ·
              {{ SET_REASON_LABELS[code] ?? '未登记原因码' }}</Tag
            >
          </span>
        </div>
        <div v-if="setConstruction?.note" class="mt-1 text-xs text-neutral-500">
          {{ setConstruction.note }}
        </div>
        <div v-if="setBasis.note" class="mt-1 text-xs text-neutral-500">
          {{ setBasis.note }}
        </div>
        <!-- R03：uncalibrated 与 membershipScope 不得隐藏 -->
        <Alert
          v-if="setBasis.calibrationStatus || setBasis.membershipScope"
          class="mt-2"
          type="warning"
          data-testid="robust-set-calibration"
          :message="`集合校准状态：${setBasis.calibrationStatus ?? '未标注'}`"
          :description="setBasis.membershipScope ?? undefined"
          show-icon
        />
      </div>

      <!-- R03：适用性限制（按值判空；null/空不渲染不制造警告） -->
      <div
        v-if="robustLimitations.length > 0"
        class="robust-block"
        data-testid="robust-limitations"
      >
        <div class="robust-block__title">适用性限制</div>
        <Alert
          v-for="item in robustLimitations"
          :key="item.reasonCode"
          class="mt-1"
          type="warning"
          :message="limitationTitle(item)"
          :description="limitationDesc(item)"
          show-icon
        />
      </div>
      <div v-if="lowSnr" class="robust-block" data-testid="robust-lowsnr">
        <div class="robust-block__title">低 SNR 知悉传递（不进门禁）</div>
        <div class="text-xs text-neutral-500">
          SNR {{ lowSnr.snrDb ?? '—' }} dB（阈值
          {{ lowSnr.thresholdDb ?? '—' }} dB · {{ lowSnr.mode ?? '—'
          }}）<template v-if="lowSnr.disclaimer"
            >：{{ lowSnr.disclaimer }}</template
          >
        </div>
      </div>
    </div>
  </Card>
</template>

<style scoped>
.section-title {
  font-size: 13px;
  font-weight: 600;
}

/* 鲁棒验证证据面板（R01/R02/R03） */
.robust-panel {
  padding: 10px 12px;
  margin-top: 12px;
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.robust-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.robust-title {
  font-size: 13px;
  font-weight: 600;
}

.robust-block {
  padding-top: 8px;
  margin-top: 8px;
  border-top: 1px dashed hsl(var(--border) / 70%);
}

.robust-block__title {
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.robust-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 2px;
  font-size: 12px;
}

.robust-line .k {
  min-width: 120px;
  color: hsl(var(--muted-foreground));
}
</style>
