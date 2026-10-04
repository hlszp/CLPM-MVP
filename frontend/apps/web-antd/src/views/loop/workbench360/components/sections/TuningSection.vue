<!--
  参数整定剖面（workbench360 P4-1/P4-2；v3 §6.3，原型 renderSec("tuning")）

  构成：动作条（效果验证 / 创建处置项[保存后] / fitness 门禁徽章 / 状态徽标）
  + PID 对照三卡（当前 vs 推荐方案[↗ 效果验证抽屉] vs 模型）+ 四步流水。

  复用纪律（红线②，禁分叉）：
  - 四步流水整件复用 views/tuning 的 useTuningWorkbench ctx + ①identify /
    ②matrix / ③simulate 三个 section 组件（ctx-only 自包含）；
  - ④确认步为本地变体（WbConfirmCard）：旧 confirm-section 的「创建处置项」
    是 router.push 旧处置页（且旧页从不消费该 query），P4 改为页内弹窗
    OrderCreateModal（零跳转），保存逻辑仍走 ctx.savePlan() 同一实现。
  - 效果验证抽屉复用共享组件 tuning-verify-compare（含 afterTruncated 标注）。
  - 诊断→整定预填消费 use-tuning-prefill（带窗口辨识，下钻契约回显可清除）。
-->
<script setup lang="ts">
import type { TuningApi } from '#/api/tuning';

import { computed, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { message } from 'ant-design-vue';
import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

import { getHandlingOrdersApi } from '#/api/handling';
import { getTuningTasksApi } from '#/api/tuning';
import { fitnessTagToLabel } from '#/constants/clpm-ui';
import { formatLocalTime } from '#/utils/format';
import IdentifySection from '#/views/tuning/components/identify-section.vue';
import MatrixSection from '#/views/tuning/components/matrix-section.vue';
import SimulateSection from '#/views/tuning/components/simulate-section.vue';
import { useTuningWorkbench } from '#/views/tuning/composables/use-tuning-workbench';
import { fmtNum2, tuningAlgoLabel } from '#/views/tuning/constants';

import {
  clearTuningPrefill,
  useTuningPrefill,
} from '../../composables/use-tuning-prefill';
import OrderCreateModal from '../OrderCreateModal.vue';
import VerifyDrawer from '../VerifyDrawer.vue';

const props = defineProps<{
  /** 当前回路位号（卡片/弹窗标题） */
  loopTagName: null | string;
  /** 选中回路 ID */
  selectedLoopId: null | string;
}>();

const emit = defineEmits<{
  /** 旅程条/缩略卡数据已变化（保存方案/创建处置项后刷新页头摘要） */
  (e: 'journeyDirty'): void;
}>();

dayjs.extend(utc);

/* ── 四步流水 ctx（整件复用 use-tuning-workbench，红线②） ── */
const ctx = useTuningWorkbench();
const prefill = useTuningPrefill();

watch(
  () => props.selectedLoopId,
  (id) => {
    if (id) ctx.selectLoop(id);
    else ctx.clearLoop();
  },
  { immediate: true },
);

// 预填消费完成时机：辨识已发起 → 清除（对齐 use-diag-prefill 契约）
watch(
  () => ctx.identifying.value,
  (busy) => {
    if (busy && prefill.from === 'diag') clearTuningPrefill();
  },
);

/** 诊断带窗辨识：预填 ctx.timeRange（UTC ISO）并直接发起（同旧 identify handleRun 口径） */
function runPrefilledIdentify() {
  if (!prefill.tsStart || !prefill.tsEnd || !props.selectedLoopId) return;
  ctx.identifyPath.value = 'HISTORY';
  ctx.timeRange.value = [prefill.tsStart, prefill.tsEnd];
  ctx.showFitnessToast('identify');
  void ctx.runIdentify();
}

/* ── fitness 门禁徽章（判定逻辑单源 ctx：L0/L1 禁止整定 / L2 警告放行） ── */
const fitnessTagsText = computed(() =>
  (ctx.fitnessTags.value ?? []).map((t) => fitnessTagToLabel(t)).join('、'),
);
const gateBadge = computed(() => {
  const lv = ctx.fitnessLevel.value;
  if (!lv) return null;
  if (lv === 'L0' || lv === 'L1')
    return {
      cls: 't-danger',
      label: `适用性 ${lv} 禁止整定`,
      tip: fitnessTagsText.value || '适用性不足，先消除异常来源后再整定',
    };
  if (lv === 'L2')
    return {
      cls: 't-warn',
      label: '适用性 L2 警告放行',
      tip: `${fitnessTagsText.value || '条件异常'}：整定结果可能受控制状态干扰`,
    };
  return {
    cls: 't-info',
    label: `适用性 ${lv} 就绪`,
    tip: fitnessTagsText.value || '',
  };
});

/** 状态徽标（四步进度口径：辨识→矩阵→仿真→已保存） */
const phaseTag = computed(() => {
  if (ctx.savedRecordId.value)
    return { cls: 't-ok', label: '方案已保存 · 可创建处置项' };
  if (ctx.simResult.value)
    return { cls: 't-warn', label: '已仿真 · 待确认' };
  if (ctx.matrixRows.value.length > 0)
    return { cls: 't-info', label: '矩阵已就绪' };
  if (ctx.outcome.value)
    return { cls: 't-info', label: '辨识完成' };
  if (ctx.identifying.value) return { cls: 't-info', label: '辨识中…' };
  return null;
});

/* ── 最新整定记录（契约 §1.5 整定历史：首页首条 ≈ 最新；推荐卡/模型卡兜底） ── */
const latestTask = ref<null | TuningApi.TuningTaskItem>(null);
const latestTaskLoading = ref(false);
/** 最近 TUNING 工单提交时间（决策#5：效果验证默认时点反查） */
const latestTuningSubmitIso = ref<null | string>(null);

async function loadLatest() {
  const id = props.selectedLoopId;
  latestTask.value = null;
  latestTuningSubmitIso.value = null;
  if (!id) return;
  latestTaskLoading.value = true;
  try {
    const [tasks, orders] = await Promise.allSettled([
      getTuningTasksApi({ loopId: id, page: 1, pageSize: 1 }),
      getHandlingOrdersApi({
        loopId: id,
        actionType: 'TUNING',
        page: 1,
        pageSize: 50,
      }),
    ]);
    if (tasks.status === 'fulfilled')
      latestTask.value = tasks.value.items?.[0] ?? null;
    if (orders.status === 'fulfilled') {
      const withSubmit = (orders.value.items as typeof orders.value.items)
        .filter((it) => it.submittedAt)
        .toSorted((a, b) =>
          String(b.submittedAt).localeCompare(String(a.submittedAt)),
        );
      latestTuningSubmitIso.value = withSubmit[0]?.submittedAt ?? null;
    }
  } finally {
    latestTaskLoading.value = false;
  }
}

watch(
  () => props.selectedLoopId,
  () => {
    void loadLatest();
  },
  { immediate: true },
);

/* ── PID 对照三卡 ── */
const currentPidText = computed(() => {
  const p = ctx.currentPid.value;
  if (!p) return null;
  return `${fmtNum2(p.kp)} / ${fmtNum2(p.ti)} / ${fmtNum2(p.td)}`;
});

/** 推荐方案卡：本会话选定组优先，其次最新整定记录推荐 */
const recommendCard = computed(() => {
  const chosen = ctx.simCandidates.value.find(
    (c) => c.label === ctx.finalLabel.value && !c.isCurrent,
  );
  if (chosen && chosen.pid)
    return {
      algo: tuningAlgoLabel(chosen.algorithm ?? '') || chosen.label,
      createdAt: null as null | string,
      pid: chosen.pid,
      source: '本会话选定' as const,
    };
  const t = latestTask.value;
  if (t?.recommendedPid)
    return {
      algo: tuningAlgoLabel(t.algorithm),
      createdAt: t.createdAt,
      pid: t.recommendedPid,
      source: '最新整定记录' as const,
    };
  return null;
});

/** 模型卡：本会话辨识结果优先，其次最新整定记录模型参数 */
const modelCard = computed(() => {
  const o = ctx.outcome.value;
  if (o) {
    return { modelType: o.modelType, params: o.params, source: '本会话辨识' };
  }
  const t = latestTask.value;
  if (t?.modelParams)
    return {
      modelType: t.modelType,
      params: t.modelParams as TuningApi.ModelParams,
      source: '最新整定记录',
    };
  return null;
});

function modelParamText(p: TuningApi.ModelParams): string {
  const parts: string[] = [];
  if (p.K != null) parts.push(`K ${fmtNum2(p.K)}`);
  if (p.tau != null) parts.push(`τ ${fmtNum2(p.tau)}s`);
  if (p.T1 != null) parts.push(`T1 ${fmtNum2(p.T1)}s`);
  if (p.T2 != null) parts.push(`T2 ${fmtNum2(p.T2)}s`);
  if (p.theta != null) parts.push(`θ ${fmtNum2(p.theta)}s`);
  return parts.join(' · ');
}

/** 效果验证抽屉建议时点（决策#5：TUNING 工单 submittedAt → 记录 createdAt） */
const verifySuggested = computed(() => {
  if (latestTuningSubmitIso.value)
    return {
      pointTimeIso: latestTuningSubmitIso.value,
      sourceLabel: '处置工单提交时间',
    };
  if (latestTask.value?.createdAt)
    return {
      pointTimeIso: latestTask.value.createdAt,
      sourceLabel: '整定记录创建时间',
    };
  return null;
});

/* ── ④ 方案确认（本地变体：页内创建处置项，保存逻辑同 ctx） ── */
const saving = computed(() => ctx.saving.value);
const confirmOptions = computed(() =>
  ctx.simCandidates.value.filter((c) => !c.isCurrent).map((c) => c.label),
);

async function handleSave() {
  try {
    const id = await ctx.savePlan();
    if (id) {
      message.success('整定方案已保存（状态：已仿真）');
      emit('journeyDirty');
      void loadLatest();
    }
  } catch (error: any) {
    message.error(error?.message || '保存失败');
  }
}

/* ── 效果验证抽屉 / 创建处置项弹窗 ── */
const verifyOpen = ref(false);
const orderOpen = ref(false);
const router = useRouter();

/** C2：查看整定历史（此前剖面内只有最新 1 条，完整历史必须离开工作台走菜单） */
function goRecords() {
  if (props.selectedLoopId) {
    router.push({
      path: '/tuning/records',
      query: { loopId: props.selectedLoopId },
    });
  }
}

const tuningCtxForOrder = computed(() => {
  const t = latestTask.value;
  const saved = ctx.savedRecordId.value;
  if (!saved || !props.selectedLoopId) return null;
  const chosen = ctx.simCandidates.value.find(
    (c) => c.label === ctx.finalLabel.value && !c.isCurrent,
  );
  const algoRow = ctx.matrixRows.value.find(
    (r) => chosen && r.algorithm === chosen.algorithm,
  );
  return {
    algorithm:
      tuningAlgoLabel(algoRow?.algorithm ?? '') ||
      (chosen?.label ?? '') ||
      (t ? tuningAlgoLabel(t.algorithm) : ''),
    currentPid: ctx.currentPid.value ?? null,
    loopId: props.selectedLoopId,
    recommendedPid:
      chosen?.pid ??
      (t?.recommendedPid as TuningApi.PidParams | undefined) ??
      { kp: 0, ti: 0, td: 0 },
    tuningRecordId: saved,
  };
});

function onCreateOrder(_orderNo: string) {
  emit('journeyDirty');
}

const fmtTs = (iso?: null | string) => formatLocalTime(iso, 'MM-DD HH:mm');
</script>

<template>
  <div class="wb360-tuning">
    <!-- 动作条 -->
    <div class="act-bar">
      <button class="btn primary sm" type="button" @click="verifyOpen = true">
        效果验证
      </button>
      <button
        class="btn sm"
        type="button"
        title="查看该回路全部整定记录（整定记录页）"
        @click="goRecords"
      >
        整定历史
      </button>
      <button
        class="btn sm"
        :disabled="!ctx.savedRecordId.value"
        :title="
          ctx.savedRecordId.value
            ? '基于已保存的整定方案创建处置工单（页内）'
            : '先在④确认步保存方案，再创建处置项'
        "
        type="button"
        @click="orderOpen = true"
      >
        ④ 创建处置项
      </button>
      <span
        v-if="gateBadge"
        class="tag"
        :class="gateBadge.cls"
        :title="gateBadge.tip || undefined"
      >
        <span class="dot"></span>{{ gateBadge.label }}
      </span>
      <span
        v-if="phaseTag"
        class="tag"
        :class="phaseTag.cls"
      >
        <span class="dot"></span>{{ phaseTag.label }}
      </span>
      <span class="spacer"></span>
      <span class="dim">四步全流程页内完成，方案确认后转处置闭环</span>
    </div>

    <!-- 诊断剖面预填回显（use-tuning-prefill 契约：发起辨识后自动清除） -->
    <div v-if="prefill.from === 'diag'" class="prefill-box">
      <span>
        已从诊断结论带入辨识时间窗：
        <span class="mono"
          >{{ fmtTs(prefill.tsStart) }} ~ {{ fmtTs(prefill.tsEnd) }}</span
        >
        <template v-if="prefill.categoryLabel">
          （{{ prefill.categoryLabel
          }}<template v-if="prefill.primaryConfidence != null">
            · 置信 {{ Math.round(prefill.primaryConfidence * 100) }}%</template
          >）
        </template>
      </span>
      <button class="link" type="button" @click="runPrefilledIdentify">
        用此窗口开始辨识 →
      </button>
      <button
        class="link dim-link"
        type="button"
        @click="clearTuningPrefill()"
      >
        清除
      </button>
    </div>

    <!-- PID 对照三卡（原型 pid-compare） -->
    <div class="pid-compare">
      <div class="cell">
        <b>当前 PID（实时）</b>
        <span class="mono">{{
          currentPidText ?? (latestTaskLoading ? '加载中…' : '未绑定 P/I/D')
        }}</span>
      </div>
      <div class="cell rec">
        <button
          class="xbadge"
          title="展开效果验证详情（上一轮实施前后对比）"
          type="button"
          @click="verifyOpen = true"
        >
          ↗
        </button>
        <b>推荐方案</b>
        <span v-if="recommendCard" class="mono"
          >{{ fmtNum2(recommendCard.pid.kp) }} /
          {{ fmtNum2(recommendCard.pid.ti) }} /
          {{ fmtNum2(recommendCard.pid.td) }}</span
        >
        <span v-else class="dim">完成整定后生成</span>
        <span class="cell-note">{{
          recommendCard
            ? `${recommendCard.algo} · ${recommendCard.source}${
                recommendCard.createdAt
                  ? ` · ${fmtTs(recommendCard.createdAt)}`
                  : ''
              }`
            : '①辨识→②矩阵→③仿真→④确认'
        }}</span>
      </div>
      <div class="cell">
        <b>模型</b>
        <span v-if="modelCard" class="mono"
          >{{ modelCard.modelType }}
          {{ modelParamText(modelCard.params) }}</span
        >
        <span v-else class="dim">{{
          latestTaskLoading ? '加载中…' : '待辨识'
        }}</span>
        <span v-if="modelCard" class="cell-note">来源：{{ modelCard.source }}</span>
      </div>
    </div>

    <!-- 四步流水（①②③整件复用，与旧工作台同序同锁；④本地变体页内创建处置项） -->
    <IdentifySection :ctx="ctx" />
    <MatrixSection :ctx="ctx" />
    <SimulateSection :ctx="ctx" />

    <div class="confirm-card">
      <div class="confirm-head">
        <b>④ 方案确认</b>
        <span class="dim">选定 1 组最终方案保存（状态=已仿真）</span>
      </div>
      <div v-if="!ctx.simResult.value" class="confirm-empty dim">
        完成③仿真对比后在此确认最终方案
      </div>
      <template v-else>
        <div class="confirm-body">
          <label class="radio" v-for="label in confirmOptions" :key="label">
            <input
              v-model="ctx.finalLabel.value"
              :value="label"
              name="wb360-final-label"
              type="radio"
            />
            <span>{{ label }}</span>
          </label>
          <button
            class="btn primary sm"
            :disabled="!ctx.canConfirm.value || !!ctx.savedRecordId.value"
            type="button"
            @click="handleSave"
          >
            {{ saving ? '保存中…' : '保存方案' }}
          </button>
        </div>
        <div v-if="ctx.savedRecordId.value" class="saved-tip">
          方案已保存。请线下实施后在处置模块记录闭环（平台不直接下写 DCS 参数）
          <button class="link" type="button" @click="orderOpen = true">
            创建处置项 →（页内）
          </button>
        </div>
      </template>
    </div>

    <!-- 效果验证抽屉（P4-2） -->
    <VerifyDrawer
      v-model:open="verifyOpen"
      :loop-id="selectedLoopId"
      :loop-tag-name="loopTagName"
      :suggested="verifySuggested"
    />

    <!-- 创建处置项弹窗（页内，红线②） -->
    <OrderCreateModal
      v-model:open="orderOpen"
      :loop-id="selectedLoopId"
      :loop-tag-name="loopTagName"
      :tuning="tuningCtxForOrder"
      @created="onCreateOrder"
    />
  </div>
</template>

<style scoped>
.wb360-tuning {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

/* 动作条 */
.act-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.act-bar .spacer {
  flex: 1;
}

.btn.primary.sm {
  padding: 5px 14px;
  font-size: 12px;
  color: hsl(var(--primary-foreground));
  cursor: pointer;
  background: hsl(var(--primary));
  border: none;
  border-radius: 4px;
}

.btn.primary.sm:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.btn.sm {
  padding: 4px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.btn.sm:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.btn.sm:hover:not(:disabled) {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.dim {
  color: hsl(var(--muted-foreground) / 80%);
}

.mono {
  font-family: var(--font-mono, monospace);
}

/* 预填回显 */
.prefill-box {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 6px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  border: 1px solid hsl(var(--primary) / 35%);
  border-radius: 6px;
}

.prefill-box .mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

/* PID 对照三卡（原型 pid-compare） */
.pid-compare {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.pid-compare .cell {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  background: hsl(var(--accent) / 35%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.pid-compare .cell.rec {
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary) / 45%);
}

.pid-compare .cell b {
  font-size: 12px;
}

.pid-compare .cell > .mono {
  font-size: 13px;
  font-weight: 600;
}

.cell-note {
  font-size: 11px;
  color: hsl(var(--muted-foreground) / 80%);
}

.xbadge {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.xbadge:hover {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

/* ④ 确认卡 */
.confirm-card {
  padding: 10px 14px;
  background: hsl(var(--accent) / 30%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.confirm-head {
  display: flex;
  gap: 10px;
  align-items: baseline;
}

.confirm-head b {
  font-size: 13px;
}

.confirm-empty {
  padding: 4px 10px;
  margin-top: 8px;
  font-size: 12px;
  border-left: 3px solid hsl(var(--primary) / 40%);
}

.confirm-body {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
  margin-top: 8px;
}

.radio {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  font-size: 12px;
  cursor: pointer;
}

.saved-tip {
  padding-top: 8px;
  margin-top: 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  border-top: 1px solid hsl(var(--border) / 60%);
}

.link {
  padding: 0;
  margin-left: 8px;
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.link:hover {
  text-decoration: underline;
}

.dim-link {
  color: hsl(var(--muted-foreground) / 75%);
}

/* tag 徽标（与 DiagSection 同口径） */
.tag {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  padding: 1px 9px;
  font-size: 11px;
  white-space: nowrap;
  border: 1px solid transparent;
  border-radius: 10px;
}

.tag .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.t-ok {
  color: hsl(var(--success));
  background: hsl(var(--success) / 12%);
}

.t-ok .dot {
  background: hsl(var(--success));
}

.t-warn {
  color: hsl(var(--warning));
  background: hsl(var(--warning) / 14%);
}

.t-warn .dot {
  background: hsl(var(--warning));
}

.t-info {
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 12%);
}

.t-info .dot {
  background: hsl(var(--primary));
}

.t-danger {
  color: hsl(var(--destructive));
  background: hsl(var(--destructive) / 12%);
}

.t-danger .dot {
  background: hsl(var(--destructive));
}
</style>
