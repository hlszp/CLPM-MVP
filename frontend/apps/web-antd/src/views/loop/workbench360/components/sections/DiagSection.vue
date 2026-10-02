<!--
  回路诊断剖面（workbench360 P3，P3-1~P3-5；v3 §6.2 / 原型 renderSec("diag")）

  动作条（发起诊断 + F5 预检徽标 + L0~L4 门禁徽标 + 异步进度）+ 最新结论卡（↗
  全量抽屉）+ 负向指标条 + 诊断历史表（行详情→抽屉）+ 结论演变时间线入口。

  复用纪律（禁分叉）：
  - 结论渲染复用 views/diagnosis/components/diagnosis-result-panel（section=all）；
  - 发起链路复用旧工作台 API 与语义（trigger /diagnosis/run + use-diagnosis-runner
    轮询 + L0 阻断/L1L2 警告放行，2026-10-01 裁决口径）；
  - 复核复用 review-drawer、复核表单+前后对比复用 diagnosis-detail-modal、
    演变时间线复用 loop-archive-drawer（零跳转页内可用）；
  - 预填消费 use-diag-prefill 单一事实源（评估剖面「诊断此窗」）。
-->
<script setup lang="ts">
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, ref, watch } from 'vue';

import { message } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisPrecheckApi, getDiagnosisRunDetailApi } from '#/api/diagnosis';
import { fitnessTagToLabel } from '#/constants/clpm-ui';
import DiagnosisDetailModal from '#/views/diagnosis/components/diagnosis-detail-modal.vue';
import DiagnosisResultPanel from '#/views/diagnosis/components/diagnosis-result-panel.vue';
import LoopArchiveDrawer from '#/views/diagnosis/components/loop-archive-drawer.vue';
import DiagnosisPrecheckBadge from '#/views/diagnosis/components/precheck-badge.vue';
import ReviewDrawer from '#/views/diagnosis/components/review-drawer.vue';
import { useDiagnosisRunner } from '#/views/diagnosis/composables/use-diagnosis-runner';
import {
  CATEGORY_META,
  RUN_STATUS_TEXT,
  SEVERITY_TEXT,
  TRIGGER_TYPE_TEXT,
} from '#/views/diagnosis/constants';

import { diagWithZone, useInjectedDiagData } from '../../composables/use-diag-data';
import {
  clearDiagPrefill,
  useDiagPrefill,
} from '../../composables/use-diag-prefill';
import DiagTriggerModal from '../DiagTriggerModal.vue';
import WbDrawer from '../WbDrawer.vue';

const props = defineProps<{
  /** fitness 等级（monitor 清单行；与旧工作台门禁同数据源） */
  fitnessLevel: null | string;
  /** fitness 原因标签（中文映射经 fitnessTagToLabel 单源） */
  fitnessTags: string[];
  /** 当前回路位号（弹窗/抽屉标题） */
  loopTagName: null | string;
  /** 选中回路 ID（发起诊断目标） */
  selectedLoopId: null | string;
}>();

const emit = defineEmits<{
  /** 基于此结论发起整定（页内动线）：切整定剖面并预填辨识窗（P4 消费上下文） */
  (
    e: 'goTuning',
    payload: {
      categoryLabel: null | string;
      loopId: string;
      primaryConfidence: null | number;
      tsEnd: string;
      tsStart: string;
    },
  ): void;
  (e: 'locateTrend', payload: { tsEnd: string; tsStart: string }): void;
}>();

const prefill = useDiagPrefill();

/* ── 数据（页面级 provide 实例，无则本地降级） ── */
const data = useInjectedDiagData(computed(() => props.selectedLoopId));
const latest = data.latest;

/* ── 任务运行器（复用旧诊断工作台 composable：3s 轮询/隐藏暂停/终态拉结果） ── */
const runner = useDiagnosisRunner({
  onFinished(items) {
    if (items.length > 0) {
      message.success('诊断完成，结论已就地刷新');
    } else {
      message.warning('诊断完成但未产生结果记录');
    }
    data.refresh();
  },
});

const progressPct = computed(() => Math.round(runner.progress.value * 100));

/* ── F5 预检徽标（单回路；评估禁用时徽标隐藏） ── */
const precheckItem = ref<DiagnosisApi.PrecheckItem | undefined>(undefined);
const precheckAssessEnabled = ref(true);

watch(
  () => props.selectedLoopId,
  async (id) => {
    precheckItem.value = undefined;
    if (!id) return;
    try {
      const res = await getDiagnosisPrecheckApi([id]);
      precheckAssessEnabled.value = res.assessEnabled;
      if (!res.assessEnabled) return;
      precheckItem.value = res.items?.[0];
    } catch {
      // 预检失败降级：不显示徽标（事前提示不可用不影响发起，§4 F5.3）
    }
  },
  { immediate: true },
);

/* ── fitness 门禁（对齐旧页 passFitnessGate：L0 阻断 / L1 L2 警告放行 / 缺省放行） ── */
const fitnessTagsText = computed(() =>
  props.fitnessTags.map((t) => fitnessTagToLabel(t)).join('、'),
);
const gateBadge = computed(() => {
  const lv = props.fitnessLevel;
  if (!lv) return null;
  if (lv === 'L0')
    return {
      cls: 't-danger',
      label: `适用性 ${lv} 不适用`,
      tip: fitnessTagsText.value || '数据严重不足，诊断已被门禁阻断',
    };
  if (lv === 'L1' || lv === 'L2')
    return {
      cls: 't-warn',
      label: `适用性 ${lv} 警告放行`,
      tip: `${fitnessTagsText.value || '条件异常'}：允许发起，结论将附带条件警告`,
    };
  return {
    cls: 't-info',
    label: `适用性 ${lv} 就绪`,
    tip: fitnessTagsText.value || '',
  };
});
/** L0 阻断常驻告警（对齐旧页 fitnessBlocked Alert：列原因，可关闭） */
const blockedVisible = ref(false);
const blockedReason = computed(
  () => fitnessTagsText.value || '数据严重不足，适用性不足 L0',
);

/* ── L2 条件异常横幅（D5：后端权威 conditionWarning + 结果行标记） ── */
const l2Warnings = ref<DiagnosisApi.TriggerResult['conditionWarning']>([]);
const l2WarningVisible = computed(() => {
  if (l2Warnings.value && l2Warnings.value.length > 0) return true;
  return runner.resultItems.value.some((r) => r.conditionWarning);
});

watch(
  () => props.selectedLoopId,
  () => {
    blockedVisible.value = false;
    l2Warnings.value = [];
    runner.reset();
  },
);

/* ── 发起诊断（门禁 → 弹窗 → runner；body 口径同旧页 submitDiagnosis） ── */
const triggerOpen = ref(false);

function onTriggerClick() {
  // L0 阻断（2026-10-01 裁决口径：仅 L0 阻断，L1/L2 警告放行）
  if (props.fitnessLevel === 'L0') {
    blockedVisible.value = true;
    message.error('适用性 L0（数据严重不足），已阻止发起诊断');
    return;
  }
  triggerOpen.value = true;
}

async function onTrigger(payload: {
  operators?: string[];
  timeWindow: { end?: string; preset?: string; start?: string };
}) {
  const id = props.selectedLoopId;
  if (!id) {
    message.warning('尚未选中回路，无法发起诊断');
    return;
  }
  try {
    const res = await runner.trigger({
      loopIds: [id],
      timeWindow: payload.timeWindow as never,
      operatorGroup: 'full',
      ...(payload.operators ? { operators: payload.operators } : {}),
    });
    // D5（2026-10-01）：合并后端权威 L2 条件警告
    if (res.conditionWarning?.length) l2Warnings.value = res.conditionWarning;
    message.info(
      l2WarningVisible.value
        ? '诊断任务已提交（回路存在 L2 条件异常，结论将附带警告）'
        : '诊断任务已提交，进度见本剖面',
    );
    // 预填已消费：按 use-diag-prefill 契约发起后清除
    if (prefill.from === 'assess') clearDiagPrefill();
  } catch (error) {
    message.error(`发起诊断失败：${(error as Error).message}`);
  }
}

/* ── 最新结论卡 / 负向指标（P3-2） ── */
const confPercent = (conf?: null | number) =>
  conf == null ? '—' : `${Math.round(conf * 100)}%`;

const severityTagCls = (sev?: null | string) =>
  sev === 'HIGH' ? 't-danger' : (sev === 'MEDIUM' ? 't-warn' : 't-gray');

const catColor = (cat?: DiagnosisApi.Category | null) =>
  cat ? (CATEGORY_META[cat]?.color ?? null) : null;

const latestTimeText = computed(() => {
  const l = latest.value;
  if (!l?.lastDiagnosedAt) return null;
  const s = diagWithZone(l.lastDiagnosedAt);
  return s ? dayjs(s).format('MM-DD HH:mm') : null;
});

const latestWindowText = computed(() => {
  const l = latest.value;
  const s = diagWithZone(l?.timeWindowStart ?? null);
  const e = diagWithZone(l?.timeWindowEnd ?? null);
  if (!s || !e) return null;
  return `${dayjs(s).format('MM-DD HH:mm')} ~ ${dayjs(e).format('MM-DD HH:mm')}`;
});

/** 负向指标行（metricSummary.negative；%-口径条形，秒/指数仅文本，禁虚构阈值） */
interface NegRow {
  bar: null | number;
  key: string;
  label: string;
  source?: string;
  text: string;
}

const NEG_META: Array<{
  key: keyof DiagnosisApi.MetricSummary['negative'];
  label: string;
  pct: boolean;
  unit: string;
}> = [
  { key: 'oscillationRate', label: '振荡率', pct: true, unit: '%' },
  { key: 'badValueRate', label: '坏值率', pct: true, unit: '%' },
  { key: 'saturationRate', label: '饱和率', pct: true, unit: '%' },
  { key: 'stictionIndex', label: '粘滞系数', pct: true, unit: '%' },
  { key: 'settlingTime', label: '稳定时间', pct: false, unit: ' s' },
  { key: 'outputTravelIndex', label: '行程指数', pct: false, unit: '' },
];

const negativeRows = computed<NegRow[]>(() => {
  const ms = latest.value?.metricSummary;
  if (!ms?.negative) return [];
  return NEG_META.map((m) => {
    const v = ms.negative![m.key];
    return {
      bar: m.pct && typeof v === 'number' ? Math.min(100, Math.max(0, v)) : null,
      key: m.key,
      label: m.label,
      source: ms.source?.[m.key],
      text:
        v == null
          ? '—'
          : `${Number(v).toFixed(1)}${m.unit}${m.key === 'settlingTime' ? '（窗口均值）' : ''}`,
    };
  }).filter((r) => r.source !== 'none' || r.text !== '—');
});

/* ── 历史表（P3-3） ── */
const totalPages = computed(() =>
  Math.max(1, Math.ceil(data.total.value / data.pageSize)),
);

function changePage(next: number) {
  if (next < 1 || next > totalPages.value || next === data.page.value) return;
  data.loadHistory(next);
}

function fmtTs(naiveIso?: null | string): string {
  const s = diagWithZone(naiveIso ?? null);
  return s ? dayjs(s).format('MM-DD HH:mm') : '—';
}

function statusCls(status?: null | string): string {
  if (status === 'SUCCESS') return 't-ok';
  if (status === 'PARTIAL' || status === 'RUNNING') return 't-warn';
  if (status === 'FAILED') return 't-danger';
  return 't-gray';
}

/* ── 行详情抽屉（P3-3：WbDrawer 壳 + diagnosis-result-panel 复用） ── */
const detailOpen = ref(false);
const detailLoading = ref(false);
const detail = ref<DiagnosisApi.RunDetail | null>(null);

async function openDetail(runId: null | string) {
  if (!runId) return;
  detailOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  try {
    detail.value = await getDiagnosisRunDetailApi(runId);
  } catch (error) {
    message.error(
      `诊断详情加载失败：${error instanceof Error ? error.message : '未知错误'}`,
    );
  } finally {
    detailLoading.value = false;
  }
}

/* ── 复核（P3-5：复用 review-drawer，LatestRunItem 适配） ── */
const reviewOpen = ref(false);
const reviewItem = ref<DiagnosisApi.LatestRunItem | null>(null);

function toRunRefItem(
  src:
    | DiagnosisApi.LatestRunItem
    | DiagnosisApi.RunDetail
    | DiagnosisApi.RunListItem,
): DiagnosisApi.LatestRunItem {
  const runId = 'runId' in src ? src.runId : src.id;
  return {
    loopId: src.loopId,
    loopTagName: src.loopTagName ?? props.loopTagName ?? '',
    primaryCategory: src.primaryCategory ?? null,
    primaryCategoryLabel: src.primaryCategoryLabel ?? null,
    reviewResults: src.reviewResults ?? [],
    reviewStatus: src.reviewStatus ?? null,
    runId,
  };
}

function openReview() {
  if (detail.value) reviewItem.value = toRunRefItem(detail.value);
  else if (latest.value?.runId) reviewItem.value = toRunRefItem(latest.value);
  if (!reviewItem.value?.runId) {
    message.warning('当前无诊断结论可复核');
    return;
  }
  reviewOpen.value = true;
}

/* ── 复核表单+前后对比全量弹窗（P3-5：复用 diagnosis-detail-modal） ── */
const detailModalOpen = ref(false);
const detailModalItem = ref<DiagnosisApi.LatestRunItem | null>(null);

function openDetailModal() {
  if (detail.value) detailModalItem.value = toRunRefItem(detail.value);
  else if (latest.value?.runId) detailModalItem.value = toRunRefItem(latest.value);
  if (!detailModalItem.value?.runId) {
    message.warning('当前无诊断结论可对比');
    return;
  }
  detailModalOpen.value = true;
}

async function onReviewed() {
  // 复核提交后就地刷新（最新卡/历史行/打开中的详情）
  await data.refresh();
  if (detailOpen.value && detail.value) await openDetail(detail.value.id);
}

/* ── 演变时间线（P3-4：复用 loop-archive-drawer） ── */
const archiveOpen = ref(false);

function onArchiveOpenRun(item: DiagnosisApi.LatestRunItem) {
  openDetail(item.runId);
}

/* ── 趋势定位（终验优化：结论卡/历史行 → 主趋势视口跳该时间窗） ── */
function emitLocateTrend(tsStart?: null | string, tsEnd?: null | string) {
  if (!tsStart || !tsEnd) {
    message.warning('该记录无时间窗，无法定位趋势');
    return;
  }
  emit('locateTrend', { tsEnd, tsStart });
}

/* ── 基于此结论发起整定（页内动线；P4：预填辨识窗 = 该结论时间窗） ── */
function onGoTuning() {
  const id = props.selectedLoopId;
  if (!id) return;
  // 上下文优先级：打开中的行详情 > 最新结论
  const src = detail.value ?? latest.value;
  const tsStart = src?.timeWindowStart;
  const tsEnd = src?.timeWindowEnd;
  if (!tsStart || !tsEnd) {
    message.warning('该结论无时间窗，无法预填辨识窗口（可直接在整定剖面手动选择）');
    emit('goTuning', {
      categoryLabel: null,
      loopId: id,
      primaryConfidence: null,
      tsEnd: '',
      tsStart: '',
    });
    return;
  }
  emit('goTuning', {
    categoryLabel:
      (src?.primaryCategoryLabel as null | string | undefined) ??
      (src?.primaryCategory as null | string | undefined) ??
      null,
    loopId: id,
    primaryConfidence: (src?.primaryConfidence as null | number | undefined) ?? null,
    tsEnd,
    tsStart,
  });
}
</script>

<template>
  <div class="wb360-diag">
    <!-- 动作条（P3-1） -->
    <div class="act-bar">
      <button
        class="btn primary sm"
        :disabled="runner.running.value || !selectedLoopId"
        type="button"
        @click="onTriggerClick"
      >
        发起诊断
      </button>
      <DiagnosisPrecheckBadge
        v-if="precheckAssessEnabled && precheckItem"
        :item="precheckItem"
      />
      <span
        v-if="gateBadge"
        class="tag"
        :class="gateBadge.cls"
        :title="gateBadge.tip || undefined"
      >
        <span class="dot"></span>{{ gateBadge.label }}
      </span>
      <span
        v-if="runner.running.value"
        class="tag t-info"
        title="任务执行中，完成后本剖面自动刷新"
      >
        <span class="dot"></span>诊断中 {{ progressPct }}% ·
        {{ runner.stage.value || '执行中' }}
      </span>
      <span
        v-else-if="runner.errorMessage.value"
        class="tag t-danger"
        :title="runner.errorMessage.value"
      >
        <span class="dot"></span>上次诊断失败
      </span>
      <span class="spacer"></span>
      <span class="dim">发起后页内轮询进度，完成后自动刷新</span>
    </div>

    <!-- 任务进度条（运行中） -->
    <div v-if="runner.running.value" class="progress-line">
      <div class="bar">
        <i :style="{ width: `${Math.max(progressPct, 4)}%` }"></i>
      </div>
      <span class="mono"
        >{{ runner.stage.value || '执行中' }} · {{ progressPct }}%</span
      >
    </div>

    <!-- L0 阻断常驻告警（对齐旧页 fitnessBlocked） -->
    <div v-if="blockedVisible" class="blocked-alert">
      <b>已阻止发起诊断：适用性 L0（数据严重不足）</b>
      <p>原因：{{ blockedReason }}。请先在「数据管理 → 历史数据导入」补齐本回路历史数据，适用性恢复后可发起。</p>
      <button
        class="link"
        type="button"
        @click="blockedVisible = false"
      >
        知道了
      </button>
    </div>

    <!-- L2 条件异常横幅（D5 后端权威 + 结果行标记） -->
    <div v-if="l2WarningVisible" class="l2-alert">
      <b>L2 条件异常（警告放行）</b>
      <p>
        本回路当前适用性 L2，诊断结论与置信度需结合条件标签解读（{{
          fitnessTagsText || '条件异常'
        }}）。
      </p>
    </div>

    <!-- 评估剖面预填回显（use-diag-prefill 单一事实源；发起后自动清除） -->
    <div v-if="prefill.from === 'assess'" class="prefill-box">
      <span>
        已从评估剖面带入诊断时间窗：
        <span class="mono"
          >{{ prefill.tsStart ?? '—' }} ~ {{ prefill.tsEnd ?? '—' }}</span
        >
      </span>
      <button class="link" type="button" @click="triggerOpen = true">
        按预填窗口发起 →
      </button>
      <button class="link dim-link" type="button" @click="clearDiagPrefill()">
        清除
      </button>
    </div>

    <!-- 摘要双卡（P3-2） -->
    <div class="cards">
      <div class="card">
        <button
          class="xbadge"
          title="展开诊断详情（分类定性 · 证据链 · 处置建议）"
          type="button"
          @click="openDetail(latest?.runId ?? null)"
        >
          ↗
        </button>
        <template v-if="latest?.runId">
          <div class="kv-row">
            <b class="card-title">最新结论</b>
            <span
              v-if="latest.primaryCategory"
              class="tag tag-cat"
              :style="catColor(latest.primaryCategory)
                ? { color: catColor(latest.primaryCategory)! }
                : undefined
              "
            >
              <span class="dot"></span
              >{{ latest.primaryCategoryLabel ?? latest.primaryCategory }}
            </span>
            <span v-else class="tag t-gray"><span class="dot"></span>未见异常</span>
            <span
              v-if="latest.severity"
              class="tag"
              :class="severityTagCls(latest.severity)"
              ><span class="dot"></span>严重度
              {{ SEVERITY_TEXT[latest.severity] ?? latest.severity }}</span
            >
            <span class="mono dim">置信 {{ confPercent(latest.primaryConfidence) }}</span>
            <span
              class="tag"
              :class="latest.reviewStatus === 'REVIEWED' ? 't-ok' : 't-warn'"
            >
              <span class="dot"></span
              >{{ latest.reviewStatus === 'REVIEWED' ? '已复核' : '待复核' }}
            </span>
            <span class="dim">第 {{ latest.runCount ?? '—' }} 次诊断</span>
          </div>
          <div class="note">
            {{ latestTimeText ?? '—' }} · {{ latest.triggerTypeLabel ?? (latest.triggerType ? TRIGGER_TYPE_TEXT[latest.triggerType] : '手动') }} · 时间窗
            {{ latestWindowText ?? '—' }}
          </div>
          <div class="links">
            <button
              class="link"
              type="button"
              @click="openDetail(latest?.runId ?? null)"
            >
              全量证据与建议 →
            </button>
            <button
              class="link"
              type="button"
              @click="emitLocateTrend(latest?.timeWindowStart, latest?.timeWindowEnd)"
            >
              趋势定位此窗
            </button>
            <button class="link" type="button" @click="archiveOpen = true">
              结论演变时间线
            </button>
          </div>
        </template>
        <template v-else>
          <div class="kv-row"><b class="card-title">最新结论</b></div>
          <div class="note">
            本回路尚未诊断（或最新结论加载失败）——点「发起诊断」生成首份结论。
          </div>
          <div class="links">
            <button class="link" type="button" @click="archiveOpen = true">
              历史结论演变时间线 →
            </button>
          </div>
        </template>
      </div>

      <div class="card">
        <div class="kv-row"><b class="card-title">负向指标</b></div>
        <template v-if="negativeRows.length > 0">
          <div
            v-for="m in negativeRows"
            :key="m.key"
            class="neg-row"
            :title="`数据来源：${m.source === 'kpi' ? 'KPI 窗口均值' : (m.source === 'operator' ? '算子特征' : '无数据')}`"
          >
            <span class="neg-label">{{ m.label }}</span>
            <div v-if="m.bar !== null" class="neg-bar">
              <i :style="{ width: `${m.bar}%` }"></i>
            </div>
            <span class="neg-val mono">{{ m.text }}</span>
          </div>
          <div class="note dim">最新诊断时间窗口径（0~100 为刻度，非判定阈值）</div>
        </template>
        <div v-else class="note">
          暂无负向指标汇总（未诊断或该次运行未产出 metricSummary）。
        </div>
      </div>
    </div>

    <!-- 历史表（P3-3） -->
    <div v-if="data.error.value" class="error-line">
      {{ data.error.value }}
      <button
        class="link"
        type="button"
        @click="data.loadHistory(data.page.value)"
      >
        重试
      </button>
    </div>
    <table class="tbl">
      <thead>
        <tr>
          <th>时间</th>
          <th>主分类</th>
          <th>严重度</th>
          <th>置信</th>
          <th>触发</th>
          <th>复核</th>
          <th style="width: 70px">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="data.loading.value && data.rows.value.length === 0">
          <td class="dim" colspan="7">诊断历史加载中…</td>
        </tr>
        <tr v-else-if="data.rows.value.length === 0">
          <td class="dim" colspan="7">暂无诊断记录（发起后异步生成，或由定时/预警自动触发）</td>
        </tr>
        <template v-else>
          <tr v-for="row in data.rows.value" :key="row.id">
            <td>
              <span
                class="st-dot"
                :class="statusCls(row.status)"
                :title="RUN_STATUS_TEXT[row.status] ?? row.status"
              ></span>
              <span class="mono">{{ fmtTs(row.createdAt) }}</span>
            </td>
            <td>
              <span
                v-if="row.primaryCategory"
                :style="{
                  color: catColor(row.primaryCategory) ?? undefined,
                  fontWeight: 600,
                }"
              >
                {{ row.primaryCategoryLabel ?? row.primaryCategory }}
              </span>
              <span v-else class="dim">—</span>
            </td>
            <td>
              <span
                v-if="row.severity"
                class="tag"
                :class="severityTagCls(row.severity)"
                >{{ SEVERITY_TEXT[row.severity] ?? row.severity }}</span
              >
              <span v-else class="dim">—</span>
            </td>
            <td class="num">{{ confPercent(row.primaryConfidence) }}</td>
            <td class="dim">
              {{ row.triggerTypeLabel ?? (row.triggerType ? TRIGGER_TYPE_TEXT[row.triggerType] : row.triggeredBy) }}
            </td>
            <td>
              <span
                class="tag"
                :class="row.reviewStatus === 'REVIEWED' ? 't-ok' : 't-warn'"
                >{{ row.reviewStatus === 'REVIEWED' ? '已复核' : '待复核' }}</span
              >
            </td>
            <td>
              <button class="link" type="button" @click="openDetail(row.id)">
                详情
              </button>
              <button
                class="link"
                type="button"
                @click="emitLocateTrend(row.timeWindowStart, row.timeWindowEnd)"
              >
                定位
              </button>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <div class="tbl-foot">
      <span class="dim"
        >共 {{ data.total.value }} 条 · 第 {{ data.page.value }} /
        {{ totalPages }} 页</span
      >
      <span class="pager">
        <button
          :disabled="data.page.value <= 1 || data.loading.value"
          type="button"
          @click="changePage(data.page.value - 1)"
        >
          上一页
        </button>
        <button
          :disabled="data.page.value >= totalPages || data.loading.value"
          type="button"
          @click="changePage(data.page.value + 1)"
        >
          下一页
        </button>
      </span>
    </div>

    <!-- 就地发起弹窗（P3-1） -->
    <DiagTriggerModal
      v-model:open="triggerOpen"
      :fitness-level="fitnessLevel"
      :fitness-tags="fitnessTags"
      :loop-tag-name="loopTagName"
      :precheck-item="precheckAssessEnabled ? precheckItem : undefined"
      @trigger="onTrigger"
    />

    <!-- 行详情/全量抽屉（P3-3：WbDrawer 壳 + diagnosis-result-panel 复用） -->
    <WbDrawer
      :aria-label="`诊断详情 ${loopTagName ?? ''}`"
      :open="detailOpen"
      :title="`诊断详情 · ${loopTagName ?? ''}`"
      @close="detailOpen = false"
    >
      <template #header>
        <template v-if="detail">
          <span
            v-if="detail.primaryCategory"
            class="tag tag-cat"
            :style="catColor(detail.primaryCategory)
              ? { color: catColor(detail.primaryCategory)! }
              : undefined
            "
          >
            <span class="dot"></span
            >{{ detail.primaryCategoryLabel ?? detail.primaryCategory }}
          </span>
          <span v-else class="tag t-ok"><span class="dot"></span>未见异常</span>
          <span
            v-if="detail.severity"
            class="tag"
            :class="severityTagCls(detail.severity)"
            ><span class="dot"></span>严重度
            {{ SEVERITY_TEXT[detail.severity] ?? detail.severity }}</span
          >
          <span
            class="tag"
            :class="detail.reviewStatus === 'REVIEWED' ? 't-ok' : 't-warn'"
          >
            <span class="dot"></span
            >{{ detail.reviewStatus === 'REVIEWED' ? '已复核' : '待复核' }}
          </span>
        </template>
      </template>
      <div v-if="detailLoading" class="dim">诊断详情加载中…</div>
      <template v-else-if="detail">
        <div class="detail-meta">
          <span class="mono dim" :title="detail.id">run {{ detail.id.slice(0, 8) }}</span>
          <span class="dim"
            >{{ fmtTs(detail.createdAt) }} ·
            {{
              detail.triggerTypeLabel ??
              (detail.triggerType
                ? TRIGGER_TYPE_TEXT[detail.triggerType]
                : detail.triggeredBy)
            }}</span
          >
          <span class="mono dim"
            >窗口 {{ fmtTs(detail.timeWindowStart) }} ~
            {{ fmtTs(detail.timeWindowEnd) }}</span
          >
          <span class="dim">算子组 {{ detail.operatorGroup }}</span>
        </div>
        <DiagnosisResultPanel :detail="detail" section="all" />
      </template>
      <div v-else class="dim">未加载到诊断详情（该记录可能已被清理）。</div>
      <template #footer>
        <button class="btn sm" type="button" @click="openReview">
          提交复核结论 →
        </button>
        <button class="btn sm" type="button" @click="openDetailModal">
          前后对比 / 处置建议（详情弹窗）→
        </button>
        <button class="btn primary sm" type="button" @click="onGoTuning">
          基于此结论发起整定 →（页内）
        </button>
      </template>
    </WbDrawer>

    <!-- 人工复核（P3-5：复用 review-drawer） -->
    <ReviewDrawer v-model:open="reviewOpen" :item="reviewItem" @done="onReviewed" />

    <!-- 复核表单+前后对比全量弹窗（P3-5：复用 diagnosis-detail-modal） -->
    <DiagnosisDetailModal
      v-model:open="detailModalOpen"
      :item="detailModalItem"
      @reviewed="onReviewed"
    />

    <!-- 结论演变时间线（P3-4：复用 loop-archive-drawer） -->
    <LoopArchiveDrawer
      v-model:open="archiveOpen"
      :loop-id="selectedLoopId"
      :loop-tag-name="loopTagName"
      @open-run="onArchiveOpenRun"
      @trigger-diagnosis="onTriggerClick"
    />
  </div>
</template>

<style scoped>
.wb360-diag {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

/* 动作条 */
.act-bar {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.act-bar .spacer {
  flex: 1;
}

.btn.primary.sm {
  background: hsl(var(--primary));
  border: none;
  border-radius: 4px;
  color: hsl(var(--primary-foreground));
  cursor: pointer;
  font-size: 12px;
  padding: 5px 14px;
}

.btn.primary.sm:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.btn.sm {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 12px;
  padding: 4px 12px;
}

.btn.sm:hover {
  border-color: hsl(var(--primary));
  color: hsl(var(--primary));
}

/* 进度条 */
.progress-line {
  align-items: center;
  display: flex;
  gap: 10px;
}

.progress-line .bar {
  background: hsl(var(--accent) / 60%);
  border-radius: 3px;
  flex: 1;
  height: 6px;
  overflow: hidden;
}

.progress-line .bar i {
  background: hsl(var(--primary));
  display: block;
  height: 100%;
  transition: width 0.4s;
}

.progress-line .mono {
  color: hsl(var(--muted-foreground));
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  white-space: nowrap;
}

/* 告警 / 预填 */
.blocked-alert {
  border: 1px solid hsl(var(--destructive) / 40%);
  border-radius: 6px;
  padding: 10px 14px;
}

.blocked-alert b {
  color: hsl(var(--destructive));
  font-size: 13px;
}

.blocked-alert p {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 1.7;
  margin: 4px 0 6px;
}

.l2-alert {
  border: 1px solid hsl(var(--warning) / 45%);
  border-radius: 6px;
  padding: 8px 14px;
}

.l2-alert b {
  color: hsl(var(--warning));
  font-size: 12px;
}

.l2-alert p {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 1.6;
  margin: 2px 0 0;
}

.prefill-box {
  align-items: center;
  border: 1px solid hsl(var(--primary) / 35%);
  border-radius: 6px;
  color: hsl(var(--muted-foreground));
  display: flex;
  font-size: 12px;
  gap: 12px;
  padding: 6px 12px;
}

.prefill-box .mono {
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

/* 摘要双卡 */
.cards {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
}

.card {
  background: hsl(var(--accent) / 35%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px;
  position: relative;
}

.xbadge {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 12px;
  position: absolute;
  right: 8px;
  top: 8px;
}

.xbadge:hover {
  border-color: hsl(var(--primary));
  color: hsl(var(--primary));
}

.kv-row {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.card-title {
  font-size: 13px;
}

.note {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  line-height: 1.6;
}

.dim {
  color: hsl(var(--muted-foreground) / 75%);
}

.links {
  display: flex;
  gap: 14px;
  margin-top: 2px;
}

.link {
  background: none;
  border: none;
  color: hsl(var(--primary));
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.link:hover {
  text-decoration: underline;
}

.dim-link {
  color: hsl(var(--muted-foreground) / 75%);
}

/* 负向指标条 */
.neg-row {
  align-items: center;
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.neg-label {
  color: hsl(var(--muted-foreground));
  flex: none;
  font-size: 12px;
  width: 56px;
}

.neg-bar {
  background: hsl(var(--accent) / 60%);
  border-radius: 3px;
  flex: 1;
  height: 6px;
  overflow: hidden;
}

.neg-bar i {
  background: hsl(var(--primary));
  display: block;
  height: 100%;
}

.neg-val {
  flex: none;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  text-align: right;
  width: 110px;
}

/* 历史表 */
.tbl {
  border-collapse: collapse;
  font-size: 12px;
  width: 100%;
}

.tbl th {
  border-bottom: 1px solid hsl(var(--border));
  color: hsl(var(--muted-foreground));
  font-weight: 500;
  padding: 6px 10px;
  text-align: left;
}

.tbl td {
  border-bottom: 1px solid hsl(var(--border) / 55%);
  padding: 6px 10px;
}

.tbl .num {
  font-family: var(--font-mono, monospace);
  font-weight: 600;
}

.tbl .mono {
  font-family: var(--font-mono, monospace);
}

.st-dot {
  border-radius: 50%;
  display: inline-block;
  height: 6px;
  margin-right: 6px;
  vertical-align: middle;
  width: 6px;
}

.st-dot.t-ok {
  background: hsl(var(--success));
}

.st-dot.t-warn {
  background: hsl(var(--warning));
}

.st-dot.t-gray {
  background: hsl(var(--destructive));
}

.tbl-foot {
  align-items: center;
  display: flex;
  font-size: 12px;
  gap: 10px;
  justify-content: space-between;
}

.pager button {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  font-size: 12px;
  margin-left: 6px;
  padding: 2px 10px;
}

.pager button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.error-line {
  border: 1px solid hsl(var(--destructive) / 35%);
  border-radius: 4px;
  color: hsl(var(--destructive));
  font-size: 12px;
  padding: 8px 12px;
}

/* 详情抽屉 */
.detail-meta {
  color: hsl(var(--muted-foreground));
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 12px;
  margin-bottom: 10px;
}

.mono {
  font-family: var(--font-mono, monospace);
}

/* tag 徽标（原型 .tag 口径，与 AssessSection 一致） */
.tag {
  align-items: center;
  border: 1px solid transparent;
  border-radius: 10px;
  display: inline-flex;
  font-size: 11px;
  gap: 5px;
  padding: 1px 9px;
  white-space: nowrap;
}

.tag .dot {
  border-radius: 50%;
  height: 5px;
  width: 5px;
}

.t-ok {
  background: hsl(var(--success) / 12%);
  color: hsl(var(--success));
}

.t-ok .dot {
  background: hsl(var(--success));
}

.t-warn {
  background: hsl(var(--warning) / 14%);
  color: hsl(var(--warning));
}

.t-warn .dot {
  background: hsl(var(--warning));
}

.t-info {
  background: hsl(var(--primary) / 12%);
  color: hsl(var(--primary));
}

.t-info .dot {
  background: hsl(var(--primary));
}

.t-gray {
  background: hsl(var(--muted-foreground) / 12%);
  color: hsl(var(--muted-foreground));
}

.t-gray .dot {
  background: hsl(var(--muted-foreground));
}

.t-danger {
  background: hsl(var(--destructive) / 12%);
  color: hsl(var(--destructive));
}

.t-danger .dot {
  background: hsl(var(--destructive));
}

/* 主分类徽标：色值来自 diagnosis/constants CATEGORY_META（单源），描边/圆点随色 */
.tag-cat {
  background: hsl(var(--card) / 65%);
  border-color: currentcolor;
  color: hsl(var(--muted-foreground));
}

.tag-cat .dot {
  background: currentcolor;
}
</style>
