<!--
  单回路作业核心 SingleLoopWorkbench（P6-02a O02-CORE，2026-10-14）

  由旧 workbench360 index.vue 单文件巨页拆出（01a §4.2 + 09 §3）：
  - 输入仅 props：context（WorkbenchContext，loopId 必需）+ presentation
    （page/drawer/panel 尺寸外观适配，不改业务规则）+ readonly（只限制不授予）；
  - 不读 route/query/hash/window.location，不写宿主 URL；对外仅发
    result-changed / navigate-request 事件，宿主构造路由；
  - 不加载全厂目录（单对象读取经 use-wb360-loop）；左脊柱由全回路宿主
    经 #spine 插槽注入（本核心不复制目录实现）；
  - 每实例独立会话（use-wb360-session）：诊断/整定预填、请求代次、实时兴趣
    （owner=会话实例，经 Slice B 冻结 registerInterest）；
  - 对象不存在/删除/越权：明确失败展示原因，不回退任何其他回路；
    禁用剖面/非法上下文项给出原因不静默换；readonly 不授予任何能力。
-->
<script setup lang="ts">
import type { Component } from 'vue';

import type {
  Wb360NavigateRequest,
  Wb360Presentation,
  Wb360ResultChangedPayload,
  Wb360SectionExternal,
  WorkbenchContext,
} from '../workbench-context';
import type {
  SeriesVisible,
  TrendEventMark,
} from './TrendChart/types';
import type { CustomRange } from './TrendChart/use-trend-data';

import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  toRef,
  watch,
} from 'vue';

import { DatePicker, message } from 'ant-design-vue';
import dayjs from 'dayjs';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useLoopFollow } from '#/composables/use-loop-follow';
import { resolveModeLabel } from '#/composables/use-loop-realtime';
import {
  WB360_DEFAULT_WINDOW_KEY,
  WB360_SECTIONS,
  WB360_TREND_PALETTE_DARK,
  WB360_TREND_PALETTE_LIGHT,
  WB360_WINDOW_PRESETS,
  type WB360SectionKey,
} from '#/constants/clpm-ui';
import { categoryOrJudgementLabel } from '#/views/diagnosis/components/assessment-labels';

import {
  deriveLossSummary,
  useAssessHistory,
} from '../composables/use-assess-history';
import { diagWithZone, useDiagData } from '../composables/use-diag-data';
import { useJourneySummary } from '../composables/use-journey-summary';
import {
  MINI_DEFAULT_H,
  useWb360Layout,
} from '../composables/use-wb360-layout';
import { useWb360Loop } from '../composables/use-wb360-loop';
import { useWb360Session } from '../composables/use-wb360-session';
import AttentionDrawer from './AttentionDrawer.vue';
import JourneyRail from './JourneyRail.vue';
import LoopHeader from './LoopHeader.vue';
import AssessSection from './sections/AssessSection.vue';
import DiagSection from './sections/DiagSection.vue';
import HandlingSection from './sections/HandlingSection.vue';
import TuningSection from './sections/TuningSection.vue';
import SplitBar from './SplitBar.vue';
import StatusBar from './StatusBar.vue';
import ThumbStrip from './ThumbStrip.vue';
import TrendChart from './TrendChart/index.vue';
import { useTrendData } from './TrendChart/use-trend-data';

defineOptions({ name: 'SingleLoopWorkbench' });

const props = withDefaults(
  defineProps<{
    /** 统一上下文（loopId 必需；其余键参与会话身份） */
    context: WorkbenchContext;
    /** 呈现方式（仅尺寸/外观适配，不改变业务规则） */
    presentation?: Wb360Presentation;
    /** 只读进一步限制（不授予任何能力；剖面动作仍由 API 权限最终校验） */
    readonly?: boolean;
  }>(),
  { presentation: 'page', readonly: false },
);

const emit = defineEmits<{
  /** 数据变化（保存方案/创建/转单/流转/诊断完成）：宿主定向刷新相关对象 */
  (e: 'resultChanged', payload: Wb360ResultChangedPayload): void;
  /** 结构化导航/上下文更新请求：宿主构造路由，本组件不写 URL */
  (e: 'navigateRequest', payload: Wb360NavigateRequest): void;
}>();

/* ── 实例会话（预填/代次/实时兴趣 owner；对象·证据身份变化换代次清预填） ── */
const contextRef = toRef(props, 'context');
const session = useWb360Session(contextRef);
const layout = useWb360Layout();

/* ── 初始剖面：对外名 → 内部键（diagnosis→diag）；禁用剖面显式原因不静默换 ── */
const SECTION_EXTERNAL_TO_INTERNAL: Record<Wb360SectionExternal, WB360SectionKey> = {
  assess: 'assess',
  diagnosis: 'diag',
  handling: 'handling',
  tuning: 'tuning',
};
const sectionNotice = ref<null | string>(null);
function applyInitialSection() {
  const s = props.context.section;
  if (!s) return;
  const key = SECTION_EXTERNAL_TO_INTERNAL[s];
  if (layout.isSectionAvailable(key)) {
    layout.openSection(key);
    sectionNotice.value = null;
  } else {
    const label =
      WB360_SECTIONS.find((x) => x.key === key)?.label ?? s;
    sectionNotice.value = `「${label}」剖面不可用：对应模块未启用（未静默切换到其他剖面）`;
  }
}
applyInitialSection();

/* ── 回路上下文（单对象读取；禁止全厂清单） ── */
const requestedLoopId = computed(() => props.context.loopId || null);
const loop = useWb360Loop(requestedLoopId, session);
/** 已验证对象 ID（单对象读取成功前为 null：剖面/历史/摘要零请求） */
const validLoopId = computed(() => loop.current.value?.loopId ?? null);
const trend = useTrendData();
/** 趋势左轴域：PV 满量程优先（量程缺失退数据域） */
const trendYDomain = computed(() => {
  const r = loop.ranges.value.pvRange;
  return r && r.hi > r.lo ? r : trend.yDomain.value;
});
/** 评估历史（剖面/旅程条/页头徽章共用，provide 给剖面消费） */
const assessHistory = useAssessHistory(validLoopId);
/** 诊断数据（最新结论 + 历史，provide 给剖面消费；旅程条/缩略卡共用） */
const diagData = useDiagData(validLoopId);
/** 整定/处置摘要（旅程条/缩略卡/状态栏/事件层共用；模块禁用零请求） */
const journey = useJourneySummary(
  () => validLoopId.value,
  {
    enabled: () => ({
      handling: layout.isSectionAvailable('handling'),
      tuning: layout.isSectionAvailable('tuning'),
    }),
  },
);
const { isDark } = useClpmTheme();

const mainRef = ref<HTMLElement | null>(null);

/* ── 趋势窗口档位 ── */
const windowKey = ref(WB360_DEFAULT_WINDOW_KEY);
const windowPresets = WB360_WINDOW_PRESETS;
const activePreset = computed(
  () =>
    windowPresets.find((p) => p.key === windowKey.value) ?? windowPresets[0]!,
);

/* ── 自定义时间窗（起止选择器，同走 monitor 链路） ── */
/** 选择器面板开关（点"自定义"按钮切换；应用/收起后关闭） */
const customPanelVisible = ref(false);
/** RangePicker 绑定值（本地时区 Dayjs；应用时转 UTC ISO 下发） */
const customRangeValue = ref<[dayjs.Dayjs, dayjs.Dayjs] | undefined>();
/** 已生效的自定义范围（null=预设档） */
const customRange = ref<CustomRange | null>(null);

/* context.window 消费（携带即回显并真实生效）：初始化为自定义窗口 */
function applyContextWindow() {
  const w = props.context.window;
  if (!w) return;
  customRange.value = { tsStart: w.tsStart, tsEnd: w.tsEnd };
  windowKey.value = 'custom';
  trend.live.value = false; // 固定历史区间，不追跟实时
}
applyContextWindow();

/** 状态栏窗口标签：自定义档展示起止摘要 */
const windowLabel = computed(() => {
  if (windowKey.value === 'custom' && customRange.value) {
    const fmt = (iso: string) => dayjs(iso).format('MM-DD HH:mm');
    return `自定义 ${fmt(customRange.value.tsStart)}~${fmt(customRange.value.tsEnd)}`;
  }
  return activePreset.value.label;
});

function reloadTrend() {
  const loopId = validLoopId.value;
  if (!loopId) return;
  if (activePreset.value.custom && !customRange.value) return;
  trend.loadWindow(
    loopId,
    activePreset.value,
    activePreset.value.custom && customRange.value
      ? { customRange: customRange.value }
      : undefined,
  );
}

/** 本地窗口操作取代携带窗口：从上下文移除 window（单项清除更新上下文） */
function dropCarriedWindow() {
  if (!props.context.window) return;
  const next: WorkbenchContext = { ...props.context };
  delete next.window;
  emit('navigateRequest', { context: next, target: 'update-context' });
}

function selectWindow(key: string) {
  if (key === 'custom') {
    // 打开（或收起）起止选择器；确认前保持当前趋势不变
    customPanelVisible.value = !customPanelVisible.value;
    if (customPanelVisible.value && customRange.value) {
      customRangeValue.value = [
        dayjs(customRange.value.tsStart),
        dayjs(customRange.value.tsEnd),
      ];
    }
    return;
  }
  if (key === windowKey.value) return;
  dropCarriedWindow();
  customPanelVisible.value = false;
  customRange.value = null;
  windowKey.value = key;
  reloadTrend();
}

function applyCustomRange() {
  const v = customRangeValue.value;
  if (!v?.[0] || !v?.[1]) {
    message.warning('请选择起止时间');
    return;
  }
  if (!v[1].isAfter(v[0])) {
    message.warning('结束时间必须晚于开始时间');
    return;
  }
  if (v[1].diff(v[0], 'day', true) > 30) {
    message.warning('自定义时间窗不能超过 30 天');
    return;
  }
  dropCarriedWindow();
  customRange.value = {
    tsStart: v[0].toISOString(),
    tsEnd: v[1].toISOString(),
  };
  windowKey.value = 'custom';
  customPanelVisible.value = false;
  setLive(false); // 自定义窗口=固定历史区间，不追跟实时
  reloadTrend();
}

function cancelCustomRange() {
  customPanelVisible.value = false;
}

/* ── 上下文携带项回显与单项清除（window/record/work/scope/profile/configRevision） ── */
const WORK_KIND_LABEL: Record<string, string> = {
  action_item: '行动项',
  handling_order: '工单',
  investigation: '核查',
};
const contextChips = computed(() => {
  const c = props.context;
  const chips: Array<{ key: string; label: string }> = [];
  if (c.window) {
    const fmt = (iso: string) => dayjs(iso).format('MM-DD HH:mm');
    chips.push({
      key: 'window',
      label: `窗口 ${fmt(c.window.tsStart)}~${fmt(c.window.tsEnd)} · ${c.window.timezone}`,
    });
  }
  (c.evidenceRefs ?? []).forEach((e, i) => {
    chips.push({
      key: `evidence:${i}`,
      label: `记录 ${e.kind}/${e.id}${e.version ? `@${e.version}` : ''}`,
    });
  });
  if (c.workRef) {
    chips.push({
      key: 'work',
      label: `${WORK_KIND_LABEL[c.workRef.kind] ?? c.workRef.kind} ${c.workRef.id}`,
    });
  }
  if (c.scope?.nodeId) {
    chips.push({ key: 'scope', label: `范围节点 ${c.scope.nodeId}` });
  }
  if (c.profileRef) chips.push({ key: 'profile', label: `profile ${c.profileRef}` });
  if (c.configRevision) {
    chips.push({ key: 'config', label: `配置版本 ${c.configRevision}` });
  }
  return chips;
});

/** 单项清除：更新上下文（navigate-request 由宿主落 URL/状态），本地同步生效 */
function clearContextItem(key: string) {
  const c = props.context;
  const next: WorkbenchContext = { ...c };
  if (key === 'window') {
    delete next.window;
    windowKey.value = WB360_DEFAULT_WINDOW_KEY;
    customRange.value = null;
    customPanelVisible.value = false;
    reloadTrend();
  } else if (key.startsWith('evidence:')) {
    const i = Number(key.slice('evidence:'.length));
    next.evidenceRefs = (c.evidenceRefs ?? []).filter((_, j) => j !== i);
    if (next.evidenceRefs.length === 0) delete next.evidenceRefs;
  } else switch (key) {
 case 'config': {
    delete next.configRevision;
  
 break;
 }
 case 'profile': {
    delete next.profileRef;
  
 break;
 }
 case 'scope': {
    delete next.scope;
  
 break;
 }
 case 'work': {
    delete next.workRef;
  
 break;
 }
 // No default
 }
  emit('navigateRequest', { context: next, target: 'update-context' });
}

/* ── 诊断→趋势联动 + 导出 + SP 容差带 ── */
const mainTrendRef = ref<null | {
  exportPng: (title: string) => null | string;
  locate: (t0: number, t1: number) => void;
}>(null);
const spToleranceInput = ref<'' | null | number>(null);
const spTolerance = computed(() => {
  const v = spToleranceInput.value;
  return typeof v === 'number' && v > 0 ? v : null;
});
/** 左轴量程%（仅量程域有效；数据域兜底时 % 无意义不显示） */
const trendSpanPct = computed(() => !!loop.ranges.value.pvRange);

const p2 = (n: number) => (n < 10 ? `0${n}` : `${n}`);
function fmtLocal(ts: number) {
  const dt = new Date(ts);
  return `${p2(dt.getMonth() + 1)}-${p2(dt.getDate())} ${p2(dt.getHours())}:${p2(dt.getMinutes())}`;
}

/** 诊断结论/历史行 → 主趋势视口定位该时间窗（切历史模式；超出当前窗口提示换档） */
function onLocateTrend(p: { tsEnd: string; tsStart: string }) {
  const a = Date.parse(p.tsStart);
  const b = Date.parse(p.tsEnd);
  if (!Number.isFinite(a) || !Number.isFinite(b) || !(b > a)) {
    message.warning('该记录时间窗无效，无法定位趋势');
    return;
  }
  const d = trend.domain.value;
  if (d && (a > d.t1 || b < d.t0)) {
    message.warning(
      '该时间窗超出当前趋势窗口范围，请先切换更大的时间窗（如 3D / 7D）',
    );
    return;
  }
  setLive(false);
  if (layout.readonlyState.panelState !== 'half') {
    layout.setPanel('half');
  }
  mainTrendRef.value?.locate(a, b);
  message.success(
    `趋势已定位到 ${fmtLocal(a)} ~ ${fmtLocal(b)}（历史模式，切回"实时"恢复追跟）`,
  );
}

function trendFileBase() {
  const tag =
    loop.current.value?.tagName ?? loop.current.value?.loopId ?? 'loop';
  return `wb360-${tag}-${activePreset.value.label}`;
}

function exportTrendPng() {
  const url = mainTrendRef.value?.exportPng(
    `${loop.current.value?.tagName ?? ''} 趋势 · ${activePreset.value.label} · ${fmtLocal(Date.now())} 导出`,
  );
  if (!url) {
    message.warning('当前无趋势画布可导出');
    return;
  }
  const a = document.createElement('a');
  a.download = `${trendFileBase()}.png`;
  a.href = url;
  a.click();
}

function exportTrendCsv() {
  const fr = trend.frames.value;
  if (fr.length === 0) {
    message.warning('当前无趋势数据可导出');
    return;
  }
  const mm = loop.current.value?.modeMapping ?? null;
  const rows = ['timestamp,pv,sp,op,mode,quality'];
  for (const f of fr) {
    rows.push(
      [
        new Date(f.ts).toISOString(),
        f.pv ?? '',
        f.sp ?? '',
        f.op ?? '',
        resolveModeLabel(f.mode, mm) ?? f.mode ?? '',
        f.quality ?? '',
      ].join(','),
    );
  }
  const blob = new Blob([`\uFEFF${rows.join('\n')}`], {
    type: 'text/csv;charset=utf-8',
  });
  const a = document.createElement('a');
  a.download = `${trendFileBase()}.csv`;
  a.href = URL.createObjectURL(blob);
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ── 历史 / 实时切换 ── */
function setLive(on: boolean) {
  trend.live.value = on;
  if (!on) {
    reloadTrend();
  } else if (!trend.domain.value) {
    reloadTrend();
  }
}

/* ── 图例显隐 ── */
const seriesVisible = reactive<SeriesVisible>({
  mode: true,
  op: true,
  pv: true,
  sp: true,
});
const palette = computed(() =>
  isDark.value ? WB360_TREND_PALETTE_DARK : WB360_TREND_PALETTE_LIGHT,
);

/* ── 数据时钟（旅程条；实时=最近 WS 消息，历史=窗口末点） ── */
const clockText = computed(() => {
  const t = trend.live.value
    ? (loop.lastMessageAt.value?.getTime() ?? trend.domain.value?.t1)
    : trend.domain.value?.t1;
  if (!t) return '数据截至 --';
  const d = new Date(t);
  const q2 = (n: number) => (n < 10 ? `0${n}` : `${n}`);
  return `数据截至 ${q2(d.getMonth() + 1)}-${q2(d.getDate())} ${q2(d.getHours())}:${q2(d.getMinutes())}`;
});

/* ── 评估摘要（清单行真实数据 + 最新快照失分摘要，供旅程条/缩略卡） ── */
const assessSummary = computed(() => {
  const c = loop.current.value;
  if (!c) return null;
  return {
    kpiStatus: c.kpiStatus ?? null,
    score: c.score ?? null,
    scoreDelta: c.scoreDelta ?? null,
    lossSummary: deriveLossSummary(assessHistory.latest.value),
  };
});

/* ── 诊断摘要（最新诊断真实数据，供旅程条/缩略卡） ── */
const diagSummary = computed(() => {
  const l = diagData.latest.value;
  if (!l) return null;
  const ts = diagWithZone(l.lastDiagnosedAt ?? null);
  return {
    // R06 同款：主分类 NULL 不兜底"未见异常"——按科学结论显示
    categoryLabel: categoryOrJudgementLabel(l.primaryCategoryLabel, l.assessment),
    lastDiagnosedText: ts ? dayjs(ts).format('MM-DD HH:mm') : null,
    runCount: l.runCount ?? null,
  };
});

/* ── 适用性（G1 受阻显式提示 + 接入位）：读取快照 fitnessLevel，
 * G1 落地（端点回填该字段）后自动点亮等级徽章，未落地时显式提示，禁编造 */
const fitness = computed(() => ({
  level: assessHistory.latest.value?.fitnessLevel ?? null,
}));

/* ── 剖面渲染 ── */
const sectionComponents: Record<string, Component> = {
  assess: AssessSection,
  diag: DiagSection,
  handling: HandlingSection,
  tuning: TuningSection,
};

/** 各剖面 props（整定/处置剖面回路上下文） */
const sectionProps = computed<Record<string, Record<string, unknown>>>(() => ({
  assess: {
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: validLoopId.value,
  },
  diag: {
    fitnessLevel: loop.current.value?.fitnessLevel ?? null,
    fitnessTags: loop.current.value?.fitnessTags ?? [],
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: validLoopId.value,
  },
  handling: {
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: validLoopId.value,
  },
  tuning: {
    loopTagName: loop.current.value?.tagName ?? null,
    selectedLoopId: validLoopId.value,
  },
}));

function onSectionOpen(key: string) {
  layout.openSection(key as never);
}

/* ── 失分→诊断联动：切诊断剖面 + 预填时间窗（实例会话预填，不发新请求） ── */
function onDiagnoseWindow(win: { tsEnd: string; tsStart: string }) {
  if (!layout.isSectionAvailable('diag')) {
    message.warning('诊断模块未启用，无法跳转诊断剖面');
    return;
  }
  session.setDiagPrefill(win.tsStart, win.tsEnd);
  layout.openSection('diag');
  message.info('已切换到诊断剖面并预填该时间窗');
}

/* ── 诊断→整定页内动线：切整定剖面 + 预填辨识窗（实例会话预填） ── */
function onGoTuning(payload: {
  categoryLabel: null | string;
  loopId: string;
  primaryConfidence: null | number;
  tsEnd: string;
  tsStart: string;
}) {
  if (!layout.isSectionAvailable('tuning')) {
    message.warning('整定模块未启用，无法切换到整定剖面');
    return;
  }
  if (payload.tsStart && payload.tsEnd) {
    session.setTuningPrefill({
      categoryLabel: payload.categoryLabel,
      primaryConfidence: payload.primaryConfidence,
      tsEnd: payload.tsEnd,
      tsStart: payload.tsStart,
    });
    layout.openSection('tuning');
    message.info('已切换到参数整定剖面，并预填该结论的辨识时间窗');
  } else {
    layout.openSection('tuning');
    message.info('已切换到参数整定剖面');
  }
}

function onEventMarkClick(mark: TrendEventMark) {
  if (mark.section) layout.openSection(mark.section);
}

/* ── 事件标注层：诊断 ▼（页面级诊断历史首页）+ 整定 ◆ / 工单实施 ▮（摘要页） ── */
const trendEvents = computed<TrendEventMark[]>(() => {
  const marks: TrendEventMark[] = [];
  for (const row of diagData.rows.value) {
    const local = diagWithZone(row.createdAt);
    if (!local) continue;
    marks.push({
      glyph: '▼',
      key: `diag-${row.id}`,
      label: '诊断',
      section: 'diag',
      ts: new Date(local).getTime(),
    });
  }
  for (const seed of journey.eventSeeds.value) {
    const local = diagWithZone(seed.tsIso);
    if (!local) continue;
    marks.push({
      glyph: seed.glyph,
      key: seed.key,
      label: seed.label,
      section: seed.section,
      ts: new Date(local).getTime(),
    });
  }
  return marks;
});

/* ── 关注抽屉（页头 🔔 唤起，全局通知性质例外） ── */
const attentionOpen = ref(false);

/* ── 页头 ★ 个人关注（P6-05 关注入口；与回路监视列表/我的工作同一共享集） ── */
const follow = useLoopFollow();
const followLoadedOnce = ref(false);

async function ensureFollowLoaded() {
  if (followLoadedOnce.value) return;
  followLoadedOnce.value = true; // 失败不重复弹提示；刷新经共享集 refresh
  const ok = await follow.ensureLoaded();
  if (!ok) {
    message.warning('关注状态加载失败，星标初始态可能不准确（点击仍以服务端结果为准）');
  }
}

async function onToggleFollow() {
  const loopId = validLoopId.value;
  if (!loopId) return;
  const outcome = await follow.toggle(
    loopId,
    loop.current.value?.tagName ?? null,
  );
  if (outcome.ok) {
    // 关注状态变化 → 通知宿主定向刷新（兑现路由 Adapter 预留注释）
    emit('resultChanged', { changeKind: 'loop-follow-changed', loopId });
  }
}

/* ── 剖面内数据变化 → 刷新页头摘要 + 通知宿主定向刷新（不关闭工单/不冒充闭环） ── */
function onJourneyDirty() {
  journey.refresh();
  const loopId = validLoopId.value;
  if (loopId) {
    emit('resultChanged', { changeKind: 'section-data-dirty', loopId });
  }
}

/* ── 分屏拖拽（主列高度上下文） ── */
let thumbsDragUp = 0;
function onSplitDragDelta(dy: number) {
  const bodyH = mainRef.value?.clientHeight ?? 0;
  const st = layout.readonlyState;
  if (st.panelState === 'max') {
    const cur = st.miniHeightPx ?? MINI_DEFAULT_H;
    layout.resizeMiniHeight(cur - dy, bodyH);
    return;
  }
  if (st.panelState === 'half') {
    const pct = st.wsHeightPct - (dy / Math.max(bodyH, 1)) * 100;
    layout.resizeWsPct(pct);
    return;
  }
  // thumbs 态：累计上拖 >24px 展开
  thumbsDragUp += -dy;
  if (thumbsDragUp > 24) {
    thumbsDragUp = 0;
    layout.setPanel('half');
  }
}

/* ── 生命周期与实时联动 ── */
let offRealtime: (() => void) | null = null;

onMounted(() => {
  loop.startRealtime();
  offRealtime = loop.onRealtimePoint((p) => {
    trend.appendRealtimePoint(p.collectTime, p.role, p.value, p.quality);
  });
  void ensureFollowLoaded();
});

onBeforeUnmount(() => {
  offRealtime?.();
});

// 已验证对象就绪/切换 → 重载趋势窗口（immediate：直达场景 selectedLoopId 初值即命中）
watch(
  validLoopId,
  (id) => {
    if (!id) return;
    reloadTrend();
  },
  { immediate: true },
);

// 上下文身份防御性重放（宿主未 key 时 loopId 不变但证据/窗口/剖面变化的场景）：
// 会话代次递增由 session 自身 watch 保证；这里重放初始剖面与携带窗口
watch(session.contextKey, () => {
  sectionNotice.value = null;
  applyInitialSection();
  applyContextWindow();
});

const wsStyle = computed(() => {
  const st = layout.readonlyState;
  if (st.panelState === 'thumbs') return {};
  return { '--ws-h': `${st.wsHeightPct}%` };
});
const miniStyle = computed(() => {
  const st = layout.readonlyState;
  return { '--mini-h': `${st.miniHeightPx ?? MINI_DEFAULT_H}px` };
});

const wsName = computed(
  () => layout.activeSectionMeta.value?.label ?? '剖面工作区',
);
</script>

<template>
  <div
    class="wb360"
    :class="[`wb360--${presentation}`, { 'wb360--readonly': readonly }]"
  >
    <!-- 对象不可用（不存在/删除/越权）：明确失败展示原因，不回退任何其他回路 -->
    <div
      v-if="loop.loadError.value"
      class="wb360-state wb360-state--error"
      role="alert"
    >
      <div class="wb360-state-title">无法打开回路工作台</div>
      <div class="wb360-state-body">{{ loop.loadError.value }}</div>
    </div>

    <!-- 对象加载中（单对象读取在途；不渲染剖面防误发请求） -->
    <div v-else-if="!loop.current.value" class="wb360-state">
      回路对象加载中…
    </div>

    <template v-else>
      <LoopHeader
        :connection-status="loop.connectionStatus.value"
        :fitness-level="fitness.level"
        :follow-busy="follow.isBusy(validLoopId ?? '')"
        :is-followed="
          validLoopId ? follow.isFollowed(validLoopId) : false
        "
        :last-message-at="loop.lastMessageAt.value"
        :loop="loop.current.value"
        @open-attention="attentionOpen = true"
        @toggle-follow="onToggleFollow"
      />

      <!-- 携带上下文回显条（window/record/work/scope/profile/configRevision，
           单项清除更新上下文）+ 禁用剖面原因 + 只读徽标 -->
      <div
        v-if="contextChips.length > 0 || sectionNotice || readonly"
        class="wb360-ctxbar"
      >
        <span v-if="readonly" class="ctx-chip ctx-chip--readonly">只读</span>
        <span v-for="chip in contextChips" :key="chip.key" class="ctx-chip">
          {{ chip.label }}
          <button
            :aria-label="`清除${chip.label}`"
            class="ctx-chip-x"
            type="button"
            @click="clearContextItem(chip.key)"
          >
            ×
          </button>
        </span>
        <span v-if="sectionNotice" class="ctx-notice" role="alert">
          {{ sectionNotice }}
        </span>
      </div>

      <!-- monitor 端点不可用（如 SPONSOR 硬角色拒绝）：如实显示，不绕过权限 -->
      <div v-if="loop.monitorError.value" class="wb360-banner" role="alert">
        {{ loop.monitorError.value }}（基本信息与各专业剖面按各自接口权限呈现）
      </div>

      <div class="wb360-body">
        <!-- 左脊柱插槽：全回路宿主注入（本核心不复制目录实现）；单回路宿主不注入 -->
        <slot name="spine"></slot>

        <!-- 主列 -->
        <main
          ref="mainRef"
          class="wb360-main"
          :class="{ max: layout.readonlyState.panelState === 'max' }"
        >
          <!-- 旅程状态条 -->
          <JourneyRail
            :active-section="layout.activeSection.value"
            :assess="assessSummary"
            :diag="diagSummary"
            :handling="journey.handlingSummary.value"
            :sections="layout.availableSections.value"
            :tuning="journey.tuningSummary.value"
            @open="onSectionOpen"
          >
            <template #clock>
              <span class="mono">{{ clockText }}</span>
            </template>
          </JourneyRail>

          <!-- 监视画布（常驻；最大化态隐藏） -->
          <section aria-label="监视画布" class="wb360-canvas">
            <div class="cv-bar">
              <div class="segctl" aria-label="历史/实时切换">
                <button
                  :class="{ on: !trend.live.value }"
                  type="button"
                  @click="setLive(false)"
                >
                  历史
                </button>
                <button
                  :class="{ on: trend.live.value }"
                  type="button"
                  @click="setLive(true)"
                >
                  实时
                </button>
              </div>
              <div class="segctl" aria-label="时间窗">
                <button
                  v-for="p in windowPresets"
                  :key="p.key"
                  :class="{ on: windowKey === p.key || (p.custom && customPanelVisible) }"
                  type="button"
                  @click="selectWindow(p.key)"
                >
                  {{ p.label }}
                </button>
              </div>
              <!-- 自定义起止选择器：同走 monitor 链路 tsStart/tsEnd -->
              <div v-if="customPanelVisible" aria-label="自定义时间窗" class="custom-win">
                <DatePicker.RangePicker
                  v-model:value="customRangeValue"
                  :allow-clear="false"
                  :disabled-date="(d: dayjs.Dayjs) => d.isAfter(dayjs())"
                  :show-time="{ format: 'HH:mm' }"
                  format="YYYY-MM-DD HH:mm"
                  size="small"
                />
                <button type="button" @click="applyCustomRange">应用</button>
                <button type="button" @click="cancelCustomRange">取消</button>
              </div>
              <div aria-label="SP 容差带" class="segctl tol" title="设定 SP 容差带宽（工程值），0/空=不显示">
                <span class="tol-l">SP±</span>
                <input
                  v-model.number="spToleranceInput"
                  :placeholder="loop.ranges.value.pvUnit ?? '工程值'"
                  min="0"
                  step="any"
                  type="number"
                />
              </div>
              <div aria-label="导出趋势" class="segctl">
                <button type="button" @click="exportTrendPng">PNG</button>
                <button type="button" @click="exportTrendCsv">CSV</button>
              </div>
              <div class="legend">
                <span
                  class="lg"
                  :class="{ off: !seriesVisible.pv }"
                  @click="seriesVisible.pv = !seriesVisible.pv"
                >
                  <span class="sw" :style="{ background: palette.pv }"></span>PV
                </span>
                <span
                  class="lg"
                  :class="{ off: !seriesVisible.sp }"
                  @click="seriesVisible.sp = !seriesVisible.sp"
                >
                  <span class="sw" :style="{ background: palette.sp }"></span>SP
                </span>
                <span
                  class="lg"
                  :class="{ off: !seriesVisible.op }"
                  @click="seriesVisible.op = !seriesVisible.op"
                >
                  <span class="sw" :style="{ background: palette.op }"></span
                  >OP（右轴 %）
                </span>
                <span
                  class="lg"
                  :class="{ off: !seriesVisible.mode }"
                  @click="seriesVisible.mode = !seriesVisible.mode"
                >
                  <span class="mk">⏸</span>MODE 手动段
                </span>
              </div>
            </div>
            <div class="cv-body">
            <TrendChart
              ref="mainTrendRef"
              :domain="trend.domain.value"
              :events="trendEvents"
              :frames="trend.frames.value"
              :live="trend.live.value"
              :mode-mapping="loop.current.value?.modeMapping ?? null"
              :op-domain="loop.ranges.value.opRange"
              :pv-unit="loop.ranges.value.pvUnit"
              :series-visible="seriesVisible"
              :span-pct="trendSpanPct"
              :sp-tolerance="spTolerance"
              :y-domain="trendYDomain"
              @event-click="onEventMarkClick"
            />
              <div v-if="trend.loading.value" class="cv-overlay">
                趋势数据加载中…
              </div>
              <div
                v-else-if="trend.error.value"
                class="cv-overlay cv-overlay-error"
              >
                {{ trend.error.value }}
              </div>
            </div>
          </section>

          <!-- 迷你趋势（最大化态，恒全域视口） -->
          <div :style="miniStyle" class="wb360-cv-mini">
            <TrendChart
              :domain="trend.domain.value"
              :events="trendEvents"
              :frames="trend.frames.value"
              :live="trend.live.value"
              :mode-mapping="loop.current.value?.modeMapping ?? null"
              :series-visible="seriesVisible"
              :y-domain="trendYDomain"
              mini
              @event-click="onEventMarkClick"
            />
          </div>

          <!-- 分屏条（小屏隐藏） -->
          <SplitBar
            class="page-split"
            :panel-state="layout.readonlyState.panelState"
            @drag-delta="onSplitDragDelta"
            @set-panel="layout.setPanel($event)"
          />

          <!-- 工作区（缩略态 / 展开态） -->
          <section
            :style="wsStyle"
            aria-label="剖面工作区"
            class="wb360-ws"
            :class="
              layout.readonlyState.panelState === 'thumbs' ? 'thumbs' : 'open'
            "
          >
            <ThumbStrip
              v-if="layout.readonlyState.panelState === 'thumbs'"
              :assess="assessSummary"
              :diag="diagSummary"
              :handling="journey.handlingSummary.value"
              :sections="layout.availableSections.value"
              :tuning="journey.tuningSummary.value"
              @open="onSectionOpen"
            />
            <template v-else>
              <div class="ws-head">
                <span class="wname">{{ wsName }}</span>
                <span class="ws-hint">剖面 · 页内完成，不跳转</span>
                <span class="spacer"></span>
                <button class="ws-close" type="button" @click="layout.closeWs()">
                  收起 ▾
                </button>
              </div>
              <div class="ws-body">
                <component
                  :is="
                    sectionComponents[layout.activeSection.value ?? 'assess'] ??
                    AssessSection
                  "
                  v-bind="sectionProps[layout.activeSection.value ?? 'assess']"
                  @diagnose-window="onDiagnoseWindow"
                  @go-tuning="onGoTuning"
                  @journey-dirty="onJourneyDirty"
                  @locate-trend="onLocateTrend"
                />
              </div>
            </template>
          </section>

          <!-- 状态栏（深色钉底） -->
          <StatusBar
            :connection-status="loop.connectionStatus.value"
            :diag-count="layout.isSectionAvailable('diag') ? diagData.total.value : null"
            :downsampled="trend.downsampled.value"
            :handling-open-count="
              layout.isSectionAvailable('handling')
                ? (journey.handlingSummary.value?.inFlightCount ?? 0)
                : null
            "
            :last-message-at="loop.lastMessageAt.value"
            :point-count="trend.pointCount.value"
            :snapshot-count="assessHistory.total.value"
            :source="trend.source.value"
            :unit-label="loop.current.value?.unitName ?? null"
            :window-label="windowLabel"
          />
        </main>
      </div>

      <!-- 本回路关注抽屉（页头 🔔 唤起） -->
      <AttentionDrawer
        v-model:open="attentionOpen"
        :loop-id="validLoopId"
        :loop-tag-name="loop.current.value?.tagName ?? null"
      />
    </template>
  </div>
</template>

<style scoped>
/* 整页固定视口（桌面应用式，禁止整页滚动）。
 * 高度对齐旧版回路工作台先例（calc(100vh - 110px) = vben header+tabs+间距），
 * vben 内容容器为 min-height 流式布局，height:100% 无法形成固定视口。 */
.wb360 {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 110px);
  overflow: hidden;
}

/* drawer / panel 呈现：容器高度由宿主给定，去掉整页视口假设 */
.wb360.wb360--drawer,
.wb360.wb360--panel {
  flex: 1;
  height: 100%;
  min-height: 0;
}

/* 对象不可用 / 加载态（整区显式呈现，不回退其他回路） */
.wb360-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  padding: 24px;
  font-size: 13px;
  color: hsl(var(--muted-foreground));
}

.wb360-state--error {
  color: hsl(var(--destructive));
}

.wb360-state-title {
  font-size: 15px;
  font-weight: 700;
}

.wb360-state-body {
  max-width: 640px;
  line-height: 1.7;
  text-align: center;
  word-break: break-all;
}

/* 携带上下文回显条（单项清除更新上下文） */
.wb360-ctxbar {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  padding: 4px 12px;
  font-size: 11px;
  border-bottom: 1px solid hsl(var(--border));
  background: hsl(var(--accent) / 30%);
}

.ctx-chip {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  padding: 1px 6px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.ctx-chip--readonly {
  font-weight: 700;
  color: hsl(var(--primary));
  border-color: hsl(var(--primary) / 50%);
}

.ctx-chip-x {
  padding: 0 2px;
  font-size: 12px;
  line-height: 1;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: transparent;
  border: none;
}

.ctx-chip-x:hover {
  color: hsl(var(--destructive));
}

.ctx-notice {
  color: hsl(var(--destructive));
}

/* monitor 不可用横幅（硬角色拒绝等，如实显示） */
.wb360-banner {
  flex: none;
  padding: 5px 12px;
  font-size: 12px;
  color: hsl(45 93% 30%);
  background: hsl(45 93% 90% / 60%);
  border-bottom: 1px solid hsl(45 93% 60% / 60%);
}

.wb360-body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.wb360-main {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

/* 最大化态：旧画布整体隐藏，迷你趋势贴旅程条正下方 */
.wb360-main.max .wb360-canvas {
  display: none;
}

.wb360-main.max .wb360-ws.open {
  flex: 1;
  height: auto;
}

.wb360-main.max .wb360-cv-mini {
  display: flex;
}

.wb360-cv-mini {
  display: none;
  flex: none;
  flex-direction: column;
  gap: 2px;
  height: var(--mini-h, 152px);
  padding: 2px 8px;
  border-bottom: 1px solid hsl(var(--border));
}

/* 监视画布 */
.wb360-canvas {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  background: hsl(var(--card));
}

.cv-bar {
  display: flex;
  flex: none;
  flex-wrap: wrap;
  gap: 10px;
  row-gap: 4px;
  align-items: center;
  padding: 7px 12px;
  border-bottom: 1px solid hsl(var(--border));
}

.segctl {
  display: inline-flex;
  overflow: hidden;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

/* SP 容差带输入 */
.segctl.tol {
  gap: 2px;
  align-items: center;
  padding: 0 6px;
}

.segctl.tol .tol-l {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.segctl.tol input {
  width: 64px;
  padding: 3px 2px;
  font-size: 12px;
  color: inherit;
  outline: none;
  background: transparent;
  border: none;
}

.segctl.tol input:focus {
  border-bottom: 1px solid hsl(var(--primary) / 60%);
}

.segctl button {
  padding: 3px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: hsl(var(--card));
  border-right: 1px solid hsl(var(--border));
}

.segctl button:last-child {
  border-right: none;
}

.segctl button.on {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
}

/* 自定义时间窗选择条：与工具栏同排，含起止选择器 */
.custom-win {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 0 6px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
}

.custom-win button {
  padding: 3px 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: transparent;
  border: 1px solid hsl(var(--border));
}

.custom-win button:first-of-type {
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.lg {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.lg.off {
  opacity: 0.35;
}

.lg .sw {
  width: 14px;
  height: 3px;
  border-radius: 2px;
}

.lg .mk {
  font-size: 11px;
  color: hsl(var(--destructive));
}

.cv-body {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 4px 8px 0;
}

.cv-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--card) / 55%);
}

.cv-overlay-error {
  align-items: flex-start;
  justify-content: center;
  padding: 0 24px;
  color: hsl(var(--destructive));
  text-align: center;
  background: hsl(var(--card) / 85%);
}

/* 工作区：缩略态 / 展开态 */
.wb360-ws {
  display: flex;
  flex: none;
  min-height: 0;
  background: hsl(var(--card));
}

.wb360-ws.thumbs {
  gap: 10px;
  align-items: stretch;
  height: 96px;
  padding: 8px 12px;
}

.wb360-ws.open {
  flex-direction: column;
  height: var(--ws-h, 46%);
}

.ws-head {
  display: flex;
  flex: none;
  gap: 10px;
  align-items: center;
  padding: 6px 12px;
  border-bottom: 1px solid hsl(var(--border));
}

.ws-head .wname {
  font-size: 14px;
  font-weight: 700;
}

.ws-hint {
  padding: 1px 8px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--accent) / 60%);
  border-radius: 4px;
}

.ws-head .spacer {
  flex: 1;
}

.ws-close {
  padding: 2px 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
}

.ws-close:hover {
  color: hsl(var(--primary));
  border-color: hsl(var(--primary));
}

.ws-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 10px 12px;
  overflow: auto;
}

.mono {
  font-family: var(--font-mono, monospace);
}

/* 小屏降级：展开态工作区 → 底部上拉抽屉，分屏条隐藏 */
@media (max-height: 760px) {
  .wb360-ws.open {
    position: absolute;
    right: 0;
    bottom: 26px;
    left: 0;
    z-index: 40;
    height: 52%;
    border-top: 2px solid hsl(var(--primary));
    box-shadow: 0 8px 32px rgb(16 24 40 / 16%);
  }

  .page-split {
    display: none;
  }
}
</style>
