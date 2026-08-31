<script setup lang="ts">
import type { DiagnosisApi } from '#/api/diagnosis';

/**
 * 回路诊断档案抽屉（16 号文 F1 · D2=a：抽屉全屏化宽 80%，替换概览"历史"入口）。
 *
 * 结构（自上而下）：
 * ① 摘要行：回路/等级/最新主分类/最新置信度/累计次数/首末时间
 * ② 结论演变图：主分类泳道（CATEGORY_META 8 类色码，色块高度=严重度三档，
 *    DATA_INSUFFICIENT 灰斜纹）+ 置信度趋势 + KPI 迷你趋势（spark 无动画）
 *    + 处置/整定事件竖线（悬浮摘要）；时间窗 30d/90d/全部（all 截断 90d）
 * ③ 历史 run 倒序列表（迁移自 history-drawer，点击行打开该 run 详情）
 *
 * 降级（§5.4 隐藏而非置灰）：
 * - events.handlingEnabled=false → 工单事件竖线图层隐藏
 * - events.tuningEnabled=false → 整定事件竖线图层隐藏
 * - kpiTrend.available=false → KPI 趋势区隐藏，仅留置信度趋势
 *
 * 图表实现：单 SVG（viewBox 1000 宽，preserveAspectRatio=none，无动画），
 * 行标签/刻度用 HTML 绝对定位（避免非等比缩放拉伸文字），与 Spark 同纪律。
 */
import { computed, ref, watch } from 'vue';

import { Button, Drawer, Empty, RadioGroup, Spin } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopArchiveApi } from '#/api/diagnosis';

import {
  CATEGORY_META,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  REVIEW_STATUS_TEXT,
  RUN_STATUS_TEXT,
  SEVERITY_TEXT,
  TRIGGER_TYPE_TEXT,
} from '../constants';

const props = defineProps<{
  loopId: null | string;
  loopTagName?: null | string;
}>();

const emit = defineEmits<{
  /** 泳道色块/列表行点击 → 宿主页复用诊断详情弹窗打开该 run */
  openRun: [item: DiagnosisApi.LatestRunItem];
  /** 空态引导发起诊断 → 宿主页复用快捷诊断链路 */
  triggerDiagnosis: [loopId: string];
}>();

const open = defineModel<boolean>('open', { default: false });

const loading = ref(false);
const archive = ref<DiagnosisApi.LoopArchive | null>(null);
/** 时间窗（'all' 后端截断到 90d 并在 UI 提示） */
const win = ref<DiagnosisApi.ArchiveWindow>('90d');

const WINDOW_LABEL: Record<DiagnosisApi.ArchiveWindow, string> = {
  '30d': '30 天',
  '90d': '90 天',
  all: '全部',
};
const WINDOW_OPTIONS = (
  ['30d', '90d', 'all'] as DiagnosisApi.ArchiveWindow[]
).map((value) => ({ label: WINDOW_LABEL[value], value }));

// ===== 时间工具（naive UTC → 本地；对齐断流修复口径：补 Z 解析） =====

function withZone(naiveUtc?: null | string): null | string {
  if (!naiveUtc) return null;
  return /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveUtc) ? naiveUtc : `${naiveUtc}Z`;
}

function fmtLocal(naiveUtc?: null | string): string {
  const s = withZone(naiveUtc);
  return s ? dayjs(s).format('MM-DD HH:mm') : '—';
}

function fmtFull(naiveUtc?: null | string): string {
  const s = withZone(naiveUtc);
  return s ? dayjs(s).format('YYYY-MM-DD HH:mm') : '—';
}

function toMs(naiveUtc?: null | string): number {
  const s = withZone(naiveUtc);
  return s ? dayjs(s).valueOf() : Number.NaN;
}

// ===== 数据加载 =====

async function load(): Promise<void> {
  if (!props.loopId) return;
  loading.value = true;
  try {
    archive.value = await getLoopArchiveApi(props.loopId, win.value);
  } catch {
    archive.value = null; // 错误提示由请求拦截器统一弹出
  } finally {
    loading.value = false;
  }
}

watch(open, (v) => {
  if (v && props.loopId) load();
});

watch(win, () => {
  if (open.value && props.loopId) load();
});

// ===== 摘要行 / 空态 =====

const summary = computed(() => archive.value?.summary ?? null);

const latestCatMeta = computed(() => {
  const cat = summary.value?.latestCategory;
  return cat ? (CATEGORY_META[cat] ?? null) : null;
});

/** 从未诊断（引导发起诊断） */
const neverDiagnosed = computed(
  () =>
    !loading.value &&
    archive.value !== null &&
    (summary.value?.totalRuns ?? 0) === 0,
);

/** 当前窗口内有 run（决定图表与列表是否渲染） */
const hasRuns = computed(() => (archive.value?.runs.length ?? 0) > 0);

// ===== 事件与 KPI 能力（隐藏而非置灰） =====

const EVENT_META: Record<string, { color: string; label: string }> = {
  handling: { color: '#ea580c', label: '处置工单' },
  tuning: { color: '#0891b2', label: '整定批次' },
};

const visibleEvents = computed(() => {
  const ev = archive.value?.events;
  if (!ev) return [];
  return ev.items.filter((e) => {
    if (e.type === 'handling') return ev.handlingEnabled;
    if (e.type === 'tuning') return ev.tuningEnabled;
    return false;
  });
});

const kpiAvailable = computed(
  () => archive.value?.kpiTrend.available === true,
);

// ===== 图表几何（单 SVG；viewBox 宽 1000，高度按行动态） =====

const NEUTRAL = '#6c757d';
const V = {
  axisH: 30,
  confH: 36,
  kpiH: 32,
  laneH: 46,
  rowGap: 8,
  top: 6,
  width: 1000,
};
const PAD_X = 6;
/** 严重度三档 → 色块高度 */
function sevH(sev?: null | string): number {
  if (sev === 'HIGH') return 42;
  if (sev === 'MEDIUM') return 28;
  return 15;
}
const LANE_BLOCK_W = 10;

interface Row {
  key: 'conf' | 'lane' | 'osc' | 'score';
  label: string;
  y: number;
  h: number;
  color?: string;
}

const rows = computed<Row[]>(() => {
  const list: Row[] = [{ key: 'lane', label: '主分类', y: V.top, h: V.laneH }];
  let y = V.top + V.laneH + V.rowGap;
  list.push({ key: 'conf', label: '置信度', y, h: V.confH });
  y += V.confH + V.rowGap;
  if (kpiAvailable.value) {
    list.push({ key: 'score', label: '评分', y, h: V.kpiH, color: '#0891b2' });
    y += V.kpiH + V.rowGap;
    list.push({
      key: 'osc',
      label: '振荡率 %',
      y,
      h: V.kpiH,
      color: '#b45309',
    });
  }
  return list;
});

const laneRow = computed<null | Row>(
  () => rows.value.find((r) => r.key === 'lane') ?? null,
);
const confRow = computed<null | Row>(
  () => rows.value.find((r) => r.key === 'conf') ?? null,
);
const scoreRow = computed<null | Row>(
  () => rows.value.find((r) => r.key === 'score') ?? null,
);
const oscRow = computed<null | Row>(
  () => rows.value.find((r) => r.key === 'osc') ?? null,
);

const lastRowBottom = computed(() => {
  const last = rows.value.at(-1);
  return last ? last.y + last.h : 0;
});
const chartH = computed(() => lastRowBottom.value + V.axisH);
const axisY = computed(() => lastRowBottom.value + 8);

function xOf(t: number, d: { max: number; min: number }): number {
  return PAD_X + ((t - d.min) / (d.max - d.min)) * (V.width - PAD_X * 2);
}

/** 时间域：runs + 可见事件 + KPI 点取并集（同一 window 过滤口径） */
const domain = computed<null | { max: number; min: number }>(() => {
  const a = archive.value;
  if (!a || a.runs.length === 0) return null;
  const times: number[] = [];
  for (const r of a.runs) times.push(toMs(r.diagnosedAt));
  for (const e of visibleEvents.value) times.push(toMs(e.at));
  if (kpiAvailable.value) {
    for (const p of a.kpiTrend.series.score) times.push(toMs(p.t));
    for (const p of a.kpiTrend.series.oscillationRate) times.push(toMs(p.t));
  }
  const valid = times.filter((t) => Number.isFinite(t));
  if (valid.length === 0) return null;
  let min = Math.min(...valid);
  let max = Math.max(...valid);
  if (max - min < 2 * 3_600_000) {
    // 全部挤在同一时刻：给 2h 最小跨度避免除零
    const mid = (max + min) / 2;
    min = mid - 3_600_000;
    max = mid + 3_600_000;
  }
  const pad = (max - min) * 0.03;
  return { min: min - pad, max: max + pad };
});

// ===== 泳道色块 =====

interface LaneBlock {
  run: DiagnosisApi.ArchiveRunItem;
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
  striped: boolean;
}

function catColor(cat: DiagnosisApi.Category | null): string {
  return cat ? (CATEGORY_META[cat]?.color ?? NEUTRAL) : NEUTRAL;
}

function catLabel(cat: DiagnosisApi.Category | null): null | string {
  return cat ? (CATEGORY_META[cat]?.label ?? cat) : null;
}

const blocks = computed<LaneBlock[]>(() => {
  const a = archive.value;
  const d = domain.value;
  const lane = laneRow.value;
  if (!a || !d || !lane) return [];
  const baseY = lane.y + lane.h - 2;
  return a.runs.map((run) => {
    const h = sevH(run.severity);
    return {
      run,
      x: xOf(toMs(run.diagnosedAt), d) - LANE_BLOCK_W / 2,
      y: baseY - h,
      w: LANE_BLOCK_W,
      h,
      color: catColor(run.primaryCategory),
      striped: run.primaryCategory === 'DATA_INSUFFICIENT',
    };
  });
});

// ===== 趋势线（spark，无动画；null 断点断线） =====

function segmentsOf(
  series: Array<{ t: string; v: null | number }>,
  row: Row,
  fixed?: { max: number; min: number },
): string {
  const d = domain.value;
  if (!d) return '';
  const valid = series.filter(
    (p): p is { t: string; v: number } => p.v != null,
  );
  if (valid.length === 0) return '';
  let min = fixed?.min;
  let max = fixed?.max;
  if (min == null || max == null) {
    min = Math.min(...valid.map((p) => p.v));
    max = Math.max(...valid.map((p) => p.v));
  }
  const range = max - min || 1;
  const innerH = row.h - 8;
  const yOf = (v: number) => row.y + row.h - 4 - ((v - min) / range) * innerH;
  const segs: string[] = [];
  let cur: string[] = [];
  const flush = () => {
    if (cur.length > 1) segs.push(cur.join(' '));
    cur = [];
  };
  for (const p of series) {
    if (p.v == null) {
      flush();
      continue;
    }
    const x = xOf(toMs(p.t), d);
    if (!Number.isFinite(x)) continue;
    cur.push(
      `${cur.length === 0 ? 'M' : 'L'}${x.toFixed(1)},${yOf(p.v).toFixed(1)}`,
    );
  }
  flush();
  return segs.join(' ');
}

/** 置信度趋势（0~1 定标，来源 run 序列） */
const confPath = computed(() => {
  const a = archive.value;
  if (!a || !confRow.value) return '';
  return segmentsOf(
    a.runs.map((r) => ({ t: r.diagnosedAt, v: r.confidence })),
    confRow.value,
    { max: 1, min: 0 },
  );
});

const scorePath = computed(() => {
  const a = archive.value;
  if (!a || !scoreRow.value) return '';
  return segmentsOf(a.kpiTrend.series.score, scoreRow.value);
});

const oscPath = computed(() => {
  const a = archive.value;
  if (!a || !oscRow.value) return '';
  return segmentsOf(a.kpiTrend.series.oscillationRate, oscRow.value);
});

// ===== 事件竖线 + 刻度 =====

interface EventMark {
  ev: DiagnosisApi.ArchiveEventItem;
  color: string;
  label: string;
  x: number;
}

const eventMarks = computed<EventMark[]>(() => {
  const d = domain.value;
  if (!d) return [];
  return visibleEvents.value.flatMap((ev) => {
    const t = toMs(ev.at);
    const meta = EVENT_META[ev.type];
    if (!meta || !Number.isFinite(t) || t < d.min || t > d.max) return [];
    return [{ color: meta.color, ev, label: meta.label, x: xOf(t, d) }];
  });
});

interface Tick {
  label: string;
  x: number;
}

const ticks = computed<Tick[]>(() => {
  const d = domain.value;
  if (!d) return [];
  const span = d.max - d.min;
  const fmt = span > 48 * 3_600_000 ? 'MM-DD' : 'MM-DD HH:mm';
  const n = 5;
  return Array.from({ length: n }, (_, i) => {
    const t = d.min + (span * i) / (n - 1);
    return { label: dayjs(t).format(fmt), x: xOf(t, d) };
  });
});

// ===== 图例 =====

const legendCats = computed(() => {
  const a = archive.value;
  if (!a) return [];
  const seen = new Map<string, { color: string; label: string }>();
  for (const r of a.runs) {
    if (r.primaryCategory && !seen.has(r.primaryCategory)) {
      const m = CATEGORY_META[r.primaryCategory];
      seen.set(r.primaryCategory, {
        color: m?.color ?? NEUTRAL,
        label: `${m?.label ?? r.primaryCategory}${r.primaryCategory === 'DATA_INSUFFICIENT' ? '（斜纹）' : ''}`,
      });
    }
  }
  return [...seen.entries()].map(([key, v]) => ({ key, ...v }));
});

const legendEvents = computed(() => {
  const types = new Set(visibleEvents.value.map((e) => e.type));
  return [...types]
    .filter((t) => EVENT_META[t])
    .map((t) => ({
      color: EVENT_META[t]?.color ?? NEUTRAL,
      key: t,
      label: EVENT_META[t]?.label ?? t,
    }));
});

// ===== 悬浮摘要（自绘 tooltip，规避 antd Tooltip 对 SVG ref 的依赖） =====

interface Tip {
  color: string;
  hint?: string;
  lines: string[];
  title: string;
  x: number;
  y: number;
}

const tip = ref<null | Tip>(null);
const svgBoxEl = ref<HTMLDivElement | null>(null);

function locate(evt: MouseEvent): { x: number; y: number } {
  const rect = svgBoxEl.value?.getBoundingClientRect();
  if (!rect) return { x: 0, y: 0 };
  return { x: evt.clientX - rect.left, y: evt.clientY - rect.top };
}

function onBlockEnter(evt: MouseEvent, run: DiagnosisApi.ArchiveRunItem): void {
  const { x, y } = locate(evt);
  const lines = [
    `时间：${fmtFull(run.diagnosedAt)}`,
    `置信度：${run.confidence == null ? '—' : `${Math.round(run.confidence * 100)}%`}`,
    `严重度：${run.severity ? (SEVERITY_TEXT[run.severity] ?? run.severity) : '—'}`,
    `触发：${run.triggerType ? (TRIGGER_TYPE_TEXT[run.triggerType] ?? run.triggerType) : '手动诊断'}`,
    `状态：${RUN_STATUS_TEXT[run.status] ?? run.status}${run.reviewStatus ? ` · ${REVIEW_STATUS_TEXT[run.reviewStatus] ?? run.reviewStatus}` : ''}`,
  ];
  if (run.secondaryCategories?.length > 0) {
    lines.push(`并存分类：${run.secondaryCategories.length} 项`);
  }
  tip.value = {
    color: catColor(run.primaryCategory),
    hint: '点击查看该次诊断详情',
    lines,
    title: catLabel(run.primaryCategory) ?? '无结论',
    x,
    y,
  };
}

function onEventEnter(evt: MouseEvent, mark: EventMark): void {
  const { x, y } = locate(evt);
  tip.value = {
    color: mark.color,
    lines: [`时间：${fmtFull(mark.ev.at)}`],
    title: `${mark.label} · ${mark.ev.title}`,
    x,
    y,
  };
}

const tipStyle = computed(() => {
  if (!tip.value) return {};
  const w = svgBoxEl.value?.clientWidth ?? 800;
  const x = Math.min(Math.max(tip.value.x, 100), Math.max(w - 100, 100));
  return { left: `${x}px`, top: `${Math.max(tip.value.y - 8, 8)}px` };
});

// ===== 交互出口 =====

/** run → 详情弹窗入参（复用 DiagnosisDetailModal 的 LatestRunItem 契约） */
function openRun(run: DiagnosisApi.ArchiveRunItem): void {
  const a = archive.value;
  if (!a) return;
  tip.value = null;
  emit('openRun', {
    importanceLevel: a.loop.level ?? null,
    lastDiagnosedAt: run.diagnosedAt,
    loopDescription: a.loop.loopName || null,
    loopId: a.loop.loopId,
    loopTagName: props.loopTagName ?? a.loop.loopName,
    primaryCategory: run.primaryCategory,
    primaryCategoryLabel: catLabel(run.primaryCategory),
    primaryConfidence: run.confidence,
    reviewStatus: run.reviewStatus,
    runId: run.runId,
    severity: run.severity,
    status: run.status,
    triggerType: run.triggerType,
  });
}

function triggerDiagnosis(): void {
  if (props.loopId) emit('triggerDiagnosis', props.loopId);
}

/** 倒序列表（最新在顶部；迁移自 history-drawer） */
const runsDesc = computed(() =>
  [...(archive.value?.runs ?? [])].toReversed(),
);
</script>

<template>
  <Drawer
    v-model:open="open"
    :title="`诊断档案 · ${loopTagName ?? loopId ?? ''}`"
    width="80%"
    :destroy-on-close="true"
  >
    <Spin :spinning="loading">
      <!-- 加载失败 / 无档案数据 -->
      <Empty
        v-if="!loading && !archive"
        description="档案数据加载失败，请稍后重试"
      />

      <!-- 从未诊断：引导发起诊断（F1 空态） -->
      <div v-else-if="!loading && neverDiagnosed" class="archive-guide">
        <Empty description="该回路尚无诊断记录">
          <Button type="primary" @click="triggerDiagnosis">发起诊断</Button>
        </Empty>
      </div>

      <template v-else-if="archive">
        <!-- ① 摘要行 -->
        <div class="archive-summary">
          <span class="archive-summary__item">
            <span class="archive-summary__k">回路</span>
            <span class="archive-summary__v font-semibold">
              {{ loopTagName ?? archive.loop.loopName
              }}<template
                v-if="
                  archive.loop.loopName && archive.loop.loopName !== loopTagName
                "
              >
                · {{ archive.loop.loopName }}</template
              >
            </span>
          </span>
          <span
            v-if="archive.loop.loopType"
            class="archive-summary__item"
          >
            <span class="archive-summary__k">类型</span>
            <span class="archive-summary__v">{{ archive.loop.loopType }}</span>
          </span>
          <span class="archive-summary__item">
            <span class="archive-summary__k">等级</span>
            <span
              class="archive-summary__v"
              :style="{
                color: archive.loop.level
                  ? IMPORTANCE_LEVEL_COLOR[archive.loop.level]
                  : undefined,
              }"
            >
              {{
                archive.loop.level
                  ? (IMPORTANCE_LEVEL_TEXT[archive.loop.level] ?? '—')
                  : '—'
              }}
            </span>
          </span>
          <span class="archive-summary__item">
            <span class="archive-summary__k">最新主分类</span>
            <span
              class="archive-summary__v"
              :style="{ color: latestCatMeta?.color }"
            >
              {{ latestCatMeta?.label ?? '—' }}
            </span>
          </span>
          <span class="archive-summary__item">
            <span class="archive-summary__k">最新置信度</span>
            <span class="archive-summary__v tabular-nums">
              {{
                summary?.latestConfidence == null
                  ? '—'
                  : `${Math.round(summary.latestConfidence * 100)}%`
              }}
            </span>
          </span>
          <span class="archive-summary__item">
            <span class="archive-summary__k">累计诊断</span>
            <span class="archive-summary__v tabular-nums">
              {{ summary?.totalRuns ?? 0 }} 次
            </span>
          </span>
          <span class="archive-summary__item">
            <span class="archive-summary__k">首/末诊断</span>
            <span class="archive-summary__v tabular-nums">
              {{ fmtFull(summary?.firstDiagnosedAt) }} ~
              {{ fmtFull(summary?.lastDiagnosedAt) }}
            </span>
          </span>
        </div>

        <!-- ② 结论演变图（泳道 + 趋势 + 事件竖线；窗口切换右上固定） -->
        <div class="archive-chart-card">
          <div class="archive-chart-head">
            <span class="archive-chart-title">结论演变与干预事件</span>
            <span class="archive-window">
              <RadioGroup
                v-model:value="win"
                :options="WINDOW_OPTIONS"
                option-type="button"
                size="small"
              />
              <span v-if="win === 'all'" class="archive-window-note">
                全部窗口最多回看 90 天
              </span>
            </span>
          </div>

          <template v-if="hasRuns">
            <div class="archive-legend">
              <span
                v-for="c in legendCats"
                :key="c.key"
                class="archive-legend__item"
              >
                <span
                  class="archive-legend__sw"
                  :style="{ background: c.color }"
                ></span>
                {{ c.label }}
              </span>
              <span class="archive-legend__note">色块高度 = 严重度（高/中/低）</span>
              <span
                v-for="e in legendEvents"
                :key="e.key"
                class="archive-legend__item"
                :style="{ color: e.color }"
              >
                <span class="archive-legend__line"></span>
                {{ e.label }}
              </span>
            </div>

            <div class="archive-chart-wrap">
              <!-- 行标签列（HTML，避免 preserveAspectRatio=none 拉伸文字） -->
              <div
                class="archive-labels"
                :style="{ height: `${chartH}px` }"
                aria-hidden="true"
              >
                <span
                  v-for="row in rows"
                  :key="row.key"
                  class="archive-label"
                  :style="{ top: `${row.y + row.h / 2}px` }"
                >
                  {{ row.label }}
                </span>
              </div>

              <div
                ref="svgBoxEl"
                class="archive-svg-box"
                @mouseleave="tip = null"
              >
                <svg
                  class="archive-svg"
                  :viewBox="`0 0 ${V.width} ${chartH}`"
                  :style="{ height: `${chartH}px` }"
                  preserveAspectRatio="none"
                >
                  <defs>
                    <!-- DATA_INSUFFICIENT 灰斜纹（无结论态） -->
                    <pattern
                      id="clpm-archive-hatch"
                      width="6"
                      height="6"
                      patternUnits="userSpaceOnUse"
                      patternTransform="rotate(45)"
                    >
                      <rect
                        width="6"
                        height="6"
                        :fill="NEUTRAL"
                        opacity="0.14"
                      />
                      <line
                        x1="0"
                        y1="0"
                        x2="0"
                        y2="6"
                        :stroke="NEUTRAL"
                        stroke-width="2"
                        opacity="0.5"
                      />
                    </pattern>
                  </defs>

                  <!-- 行背景 -->
                  <rect
                    v-for="row in rows"
                    :key="row.key"
                    class="archive-rowbg"
                    x="0"
                    :y="row.y"
                    :width="V.width"
                    :height="row.h"
                    rx="3"
                  />

                  <!-- 刻度网格竖线 -->
                  <line
                    v-for="(tk, i) in ticks"
                    :key="`g${i}`"
                    class="archive-grid"
                    :x1="tk.x"
                    :y1="V.top"
                    :x2="tk.x"
                    :y2="lastRowBottom"
                  />

                  <!-- 事件竖线 + 顶部标记（图层随模块启用能力显隐） -->
                  <g v-for="(m, i) in eventMarks" :key="`e${i}`">
                    <line
                      :x1="m.x"
                      :y1="V.top + 2"
                      :x2="m.x"
                      :y2="lastRowBottom"
                      :stroke="m.color"
                      stroke-width="1.2"
                      stroke-dasharray="4 3"
                      opacity="0.8"
                    />
                    <circle :cx="m.x" :cy="V.top + 4" r="3" :fill="m.color" />
                    <rect
                      class="archive-ev-hit"
                      :x="m.x - 5"
                      :y="V.top"
                      width="10"
                      :height="lastRowBottom - V.top"
                      fill="transparent"
                      @mouseenter="onEventEnter($event, m)"
                    />
                  </g>

                  <!-- 泳道色块（点击打开该 run 详情） -->
                  <rect
                    v-for="(b, i) in blocks"
                    :key="`b${i}`"
                    class="archive-block"
                    :x="b.x"
                    :y="b.y"
                    :width="b.w"
                    :height="b.h"
                    rx="1.5"
                    :fill="b.striped ? 'url(#clpm-archive-hatch)' : b.color"
                    :stroke="b.striped ? NEUTRAL : 'none'"
                    stroke-opacity="0.6"
                    stroke-width="0.5"
                    @mouseenter="onBlockEnter($event, b.run)"
                    @click="openRun(b.run)"
                  />

                  <!-- 趋势线（spark 无动画） -->
                  <path
                    v-if="confPath"
                    class="archive-spark"
                    :d="confPath"
                    stroke="#0d6efd"
                  />
                  <path
                    v-if="scorePath"
                    class="archive-spark"
                    :d="scorePath"
                    :stroke="scoreRow?.color"
                  />
                  <path
                    v-if="oscPath"
                    class="archive-spark"
                    :d="oscPath"
                    :stroke="oscRow?.color"
                  />

                  <!-- 时间轴 -->
                  <line
                    class="archive-axisline"
                    x1="0"
                    :y1="axisY"
                    :x2="V.width"
                    :y2="axisY"
                  />
                  <line
                    v-for="(tk, i) in ticks"
                    :key="`t${i}`"
                    class="archive-axisline"
                    :x1="tk.x"
                    :y1="axisY"
                    :x2="tk.x"
                    :y2="axisY + 4"
                  />
                </svg>

                <!-- 刻度标签（HTML 百分比定位，与 SVG 同宽） -->
                <span
                  v-for="(tk, i) in ticks"
                  :key="`tl${i}`"
                  class="archive-tick"
                  :style="{
                    left: `${(tk.x / V.width) * 100}%`,
                    top: `${axisY + 6}px`,
                  }"
                >
                  {{ tk.label }}
                </span>

                <!-- 悬浮摘要 -->
                <div v-if="tip" class="archive-tip" :style="tipStyle">
                  <div
                    class="archive-tip__title"
                    :style="{ color: tip.color }"
                  >
                    {{ tip.title }}
                  </div>
                  <div
                    v-for="(l, i) in tip.lines"
                    :key="i"
                    class="archive-tip__line"
                  >
                    {{ l }}
                  </div>
                  <div v-if="tip.hint" class="archive-tip__hint">
                    {{ tip.hint }}
                  </div>
                </div>
              </div>
            </div>
          </template>
          <div v-else class="archive-chart-empty">
            当前时间窗（{{ WINDOW_LABEL[win] }}）内无诊断记录，可切换更长时间窗查看
          </div>
        </div>

        <!-- ③ 历史 run 倒序列表（迁移自 history-drawer；点击打开详情） -->
        <div v-if="hasRuns" class="archive-list">
          <div class="archive-sec-title">
            诊断记录（当前窗口 {{ archive.runs.length }} 次 · 倒序，点击查看详情）
          </div>
          <div class="diag-history">
            <div
              v-for="(rec, idx) in runsDesc"
              :key="rec.runId"
              class="diag-history__item"
              @click="openRun(rec)"
            >
              <div class="diag-history__rail">
                <span
                  class="diag-history__dot"
                  :class="{ 'diag-history__dot--latest': idx === 0 }"
                  :style="{ borderColor: catColor(rec.primaryCategory) }"
                ></span>
              </div>
              <div class="diag-history__body">
                <div class="diag-history__head">
                  <span v-if="idx === 0" class="diag-history__badge">最新</span>
                  <span class="diag-history__time">{{
                    fmtLocal(rec.diagnosedAt)
                  }}</span>
                </div>
                <div
                  class="diag-history__cat"
                  :style="{ color: catColor(rec.primaryCategory) }"
                >
                  {{ catLabel(rec.primaryCategory) ?? '无结论' }}
                  <span class="diag-history__conf">
                    置信度
                    {{
                      rec.confidence == null
                        ? '—'
                        : `${Math.round(rec.confidence * 100)}%`
                    }}
                  </span>
                </div>
                <div class="diag-history__meta">
                  {{
                    rec.triggerType
                      ? (TRIGGER_TYPE_TEXT[rec.triggerType] ?? rec.triggerType)
                      : '手动诊断'
                  }}
                  <template v-if="rec.status && rec.status !== 'SUCCESS'">
                    · {{ RUN_STATUS_TEXT[rec.status] ?? rec.status }}
                  </template>
                  <template v-if="rec.reviewStatus">
                    ·
                    {{
                      REVIEW_STATUS_TEXT[rec.reviewStatus] ?? rec.reviewStatus
                    }}
                  </template>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </Spin>
  </Drawer>
</template>

<style scoped>
/* ① 摘要行 */
.archive-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 24px;
  padding: 8px 12px;
  font-size: 12px;
  background: hsl(var(--accent) / 20%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.archive-summary__item {
  display: inline-flex;
  gap: 6px;
  align-items: baseline;
}

.archive-summary__k {
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 55%);
}

.archive-summary__v {
  font-weight: 500;
}

/* 空态引导 */
.archive-guide {
  padding: 32px 0;
}

/* ② 图表卡片 */
.archive-chart-card {
  padding: 10px 12px;
  margin-top: 12px;
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.archive-chart-head {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.archive-chart-title {
  font-size: 12px;
  font-weight: 600;
}

.archive-window {
  display: flex;
  gap: 8px;
  align-items: center;
}

.archive-window-note {
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 45%);
}

/* 图例 */
.archive-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  align-items: center;
  margin: 2px 0 6px;
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 70%);
}

.archive-legend__item {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.archive-legend__sw {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.archive-legend__line {
  width: 14px;
  border-top: 2px dashed currentcolor;
}

.archive-legend__note {
  color: hsl(var(--accent-foreground) / 45%);
}

/* 图表主体：左标签列 + SVG */
.archive-chart-wrap {
  position: relative;
  display: flex;
  gap: 6px;
}

.archive-labels {
  position: relative;
  flex: 0 0 56px;
}

.archive-label {
  position: absolute;
  right: 0;
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 55%);
  white-space: nowrap;
  transform: translateY(-50%);
}

.archive-svg-box {
  position: relative;
  flex: 1;
  min-width: 0;
}

.archive-svg {
  display: block;
  width: 100%;
}

.archive-rowbg {
  fill: hsl(var(--accent) / 22%);
}

.archive-grid {
  opacity: 0.8;
  stroke: hsl(var(--border));
  stroke-width: 1;
  stroke-dasharray: 2 4;
}

.archive-axisline {
  stroke: hsl(var(--border));
  stroke-width: 1;
}

.archive-block {
  cursor: pointer;
}

.archive-ev-hit {
  cursor: help;
}

.archive-spark {
  fill: none;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.archive-tick {
  position: absolute;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--accent-foreground) / 55%);
  white-space: nowrap;
  transform: translateX(-50%);
}

/* 悬浮摘要 */
.archive-tip {
  position: absolute;
  z-index: 20;
  min-width: 190px;
  padding: 6px 10px;
  pointer-events: none;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
  box-shadow: 0 4px 12px rgb(0 0 0 / 12%);
  transform: translate(-50%, -100%);
}

.archive-tip__title {
  margin-bottom: 2px;
  font-size: 12px;
  font-weight: 600;
}

.archive-tip__line {
  font-size: 11px;
  line-height: 1.5;
  color: hsl(var(--accent-foreground) / 70%);
}

.archive-tip__hint {
  margin-top: 2px;
  font-size: 11px;
  color: hsl(var(--primary));
}

.archive-chart-empty {
  padding: 18px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

/* ③ 历史 run 倒序列表（迁移自 history-drawer，追加点击态） */
.archive-list {
  margin-top: 12px;
}

.archive-sec-title {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
}

.diag-history {
  display: flex;
  flex-direction: column;
}

.diag-history__item {
  position: relative;
  display: flex;
  gap: 12px;
  padding-bottom: 20px;
  cursor: pointer;
  border-radius: 6px;
}

.diag-history__item:hover {
  background: hsl(var(--accent) / 30%);
}

.diag-history__rail {
  position: relative;
  display: flex;
  flex-shrink: 0;
  justify-content: center;
  width: 14px;
}

.diag-history__item:not(:last-child) .diag-history__rail::after {
  position: absolute;
  top: 14px;
  bottom: -6px;
  width: 2px;
  content: '';
  background: hsl(var(--border));
}

.diag-history__dot {
  z-index: 1;
  box-sizing: border-box;
  width: 12px;
  height: 12px;
  margin-top: 3px;
  background: #fff;
  border: 3px solid #6c757d;
  border-radius: 50%;
}

.diag-history__dot--latest {
  width: 14px;
  height: 14px;
  margin-top: 2px;
  box-shadow: 0 0 0 3px rgb(0 0 0 / 6%);
}

.diag-history__body {
  flex: 1;
  min-width: 0;
}

.diag-history__head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 2px;
}

.diag-history__badge {
  padding: 0 6px;
  font-size: 11px;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 10%);
  border-radius: 4px;
}

.diag-history__time {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--accent-foreground) / 55%);
}

.diag-history__cat {
  font-size: 13px;
  font-weight: 500;
}

.diag-history__conf {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: hsl(var(--accent-foreground) / 55%);
}

.diag-history__meta {
  margin-top: 2px;
  font-size: 12px;
  color: hsl(var(--accent-foreground) / 45%);
}
</style>
