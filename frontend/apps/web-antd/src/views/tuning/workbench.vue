<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

/**
 * 整定工作台（整定模块主入口，09 设计方案 §6.2）
 *
 * 布局对齐回路工作台/诊断工作台：
 * - 左脊柱：装置树 + 回路清单（选中装置节点过滤）+ 整定建议列表（TUNING 类
 *   处置建议，待处理优先）
 * - 右主区：未选回路时显示该节点下所有回路的总览表格（回路编号/名称/等级/
 *   性能评分/性能等级/诊断结论/处置建议摘要/P·I·D 实时参数），点击行进入
 *   单页 4 锚点流程：① 过程辨识 → ② 整定矩阵 → ③ 仿真对比 → ④ 方案确认
 *
 * P/I/D 初值来自回路详情（并行拉取），随后由全局实时 WS 推送更新。
 * 入口上下文：?loopId=xx&from=diagnosis（诊断 TUNING 类建议「去整定」）。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { HandlingApi } from '#/api/handling';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  message,
  Spin,
  Table,
  Tag,
  Tooltip,
  Tree,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisRunsLatestApi } from '#/api/diagnosis';
import { getHandlingOrdersApi } from '#/api/handling';
import {
  getLoopDetailApi,
  getLoopListApi,
  getLoopMonitorListApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { useLoopRealtime } from '#/composables/use-loop-realtime';

import { SEVERITY_COLOR } from '../diagnosis/constants';
import ConfirmSection from './components/confirm-section.vue';
import IdentifySection from './components/identify-section.vue';
import MatrixSection from './components/matrix-section.vue';
import SimulateSection from './components/simulate-section.vue';
import { useTuningWorkbench } from './composables/use-tuning-workbench';
import {
  fmtNum2,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  scoreGrade,
} from './constants';

defineOptions({ name: 'TuningWorkbench' });
/** P2 IA优化：fitness tag 中文映射（与其他模块共用） */
const TUNING_ENTRY_TAG_CN: Record<string, string> = {
  T_UNKNOWN: '未知',
  T_LOCAL_DATA_MISSING: '本地无历史数据',
  T_LOW_COVERAGE_7D: '近 7 日覆盖不足 50%',
  T_LOW_COVERAGE_30D: '近 30 日覆盖不足 50%',
  T_BAD_QUALITY: '数据质量差（PV 坏值/不确定）',
  T_MODE_NOT_AUTO: '当前处于手动控制模式',
  T_SETPOINT_MISSING: 'OPC 未绑定 SP 位号',
  T_OUTPUT_MISSING: 'OPC 未绑定 OP 位号',
  T_PID_PARAMS_INCOMPLETE: 'OPC 未绑定 P/I/D 位号',
  T_CONSTANT_SETPOINT: 'SP 长时间未变（如 30 天全恒定）',
  T_OOS_PV: 'PV 量程外点比例过高',
  T_BAD_OP_RANGE: 'OP 长期顶边或贴底（<5% / >95%）',
  T_DAMPED_OSC: '存在阻尼振荡趋势',
  T_SUSTAINED_OSC: '存在持续振荡趋势',
  T_VALVE_STICTION: '阀门疑似粘滞',
  T_DEADTIME_HIGH: '纯滞后/惯性比偏高',
  T_DRIFT: 'SP-PV 长期偏移（均值偏差）',
  T_HIGH_PV_NOISE: 'PV 高频噪声过大',
};
const tuningTagToCn = (t: string) => TUNING_ENTRY_TAG_CN[t] ?? t;
const tuningTagsToText = (tags: string[]) => tags.map((t) => tuningTagToCn(t)).join('、');

/** P2 IA优化：总览表格「调参优化」入口按钮点击处理
 *  —— 先查 fitness，L0/L1 阻止并弹 error；L2 弹 warning Toast；L3+/未评定 正常进整定。
 */
async function handleGoTuning(record: OverviewRow) {
  const loopId = record.loopId;
  const tagName = record.tagName || loopId;
  let level: null | string;
  let tags: string[];
  try {
    const res = await getLoopMonitorListApi({ loopId, page: 1, pageSize: 1 });
    const item = res.items?.[0];
    level = (item?.fitnessLevel as null | string) ?? null;
    tags = Array.isArray(item?.fitnessTags) ? (item.fitnessTags as string[]) : [];
  } catch {
    level = null;
    tags = [];
  }
  if (level === 'L0' || level === 'L1') {
    const reason = tags.length > 0 ? tuningTagsToText(tags) : '适用性不足';
    message.error({
      content: `回路「${tagName}」适用性不足（${level}），不建议做整定：${reason}。先消除异常来源后再操作。`,
      duration: 6,
    });
    return;
  }
  // Toast 提示（G3 要求）
  if (level === 'L2') {
    const reason = tags.length > 0 ? tuningTagsToText(tags) : '控制条件异常';
    message.warning({
      content: `【调参优化】L2 条件异常：${reason}。当前控制状态可能影响整定结论，建议先修正再做整定。`,
      duration: 5,
    });
  } else if (level === 'L3' || level === 'L4' || level === 'L5') {
    message.success(`【调参优化】当前适用性等级 = ${level}，可正常整定。`);
  } else {
    message.info(`【调参优化】尚未评定适用性等级。`);
  }
  ctx.selectLoop(loopId);
}

const route = useRoute();
const router = useRouter();
const ctx = useTuningWorkbench();

const fromDiagnosis = ref(false);

const anchors = [
  { href: '#tuning-anchor-identify', label: '① 过程辨识' },
  { href: '#tuning-anchor-matrix', label: '② 整定矩阵' },
  { href: '#tuning-anchor-simulate', label: '③ 仿真对比' },
  { href: '#tuning-anchor-confirm', label: '④ 方案确认' },
];

function scrollTo(href: string) {
  document
    .querySelector(href)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ===== 左脊柱：装置树 =====
/** ant Tree 节点约定为 {key, title}（TreeSelect 才是 {value, label}） */
interface PlantTreeNode {
  children?: PlantTreeNode[];
  key: string;
  title: string;
}

const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
const plantTreeExpandedKeys = ref<string[]>([]);
const plantTreeSelectedKeys = ref<string[]>([]);
const selectedPlantNodeId = ref<string | undefined>(undefined);

function buildTreeNodes(nodes: PlantNodeApi.PlantNode[]): PlantTreeNode[] {
  return nodes.map((n) => ({
    key: n.id,
    title: n.name,
    children: n.children?.length ? buildTreeNodes(n.children) : undefined,
  }));
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildTreeNodes(tree);
    plantTreeExpandedKeys.value = tree.map((n) => n.id);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

/** 装置节点选中：清除回路选择（右侧回到总览），重拉回路清单/建议/总览 */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  selectedPlantNodeId.value = key || undefined;
  ctx.clearLoop();
  reloadForNode();
}

// ===== 左脊柱：回路清单（选中装置节点下的回路，单选进入整定） =====
const loopItems = ref<LoopApi.LoopListItem[]>([]);
const loopLoading = ref(false);
const loopKeyword = ref('');

const filteredLoops = computed(() => {
  const kw = loopKeyword.value.trim().toLowerCase();
  if (!kw) return loopItems.value;
  return loopItems.value.filter(
    (l) =>
      l.tagName.toLowerCase().includes(kw) ||
      (l.description ?? '').toLowerCase().includes(kw),
  );
});

async function loadLoops(): Promise<void> {
  loopLoading.value = true;
  try {
    const res = await getLoopListApi({
      page: 1,
      pageSize: 100, // 后端 /loops pageSize 上限 le=100
      plantNodeId: selectedPlantNodeId.value,
    });
    loopItems.value = res.items;
  } catch {
    loopItems.value = [];
  } finally {
    loopLoading.value = false;
  }
}

// ===== 左脊柱：整定建议列表（TUNING 类在途处置工单，待排程优先） =====
const openItems = ref<HandlingApi.OrderItem[]>([]);
const openLoading = ref(false);

/** 状态排序权重：待排程 → 重开（验证失败需返工）→ 执行中 */
const SUGG_STATUS_ORDER: Record<string, number> = {
  PENDING: 0,
  REOPENED: 1,
  EXECUTING: 2,
};

const tuningSuggestions = computed(() =>
  openItems.value
    .filter((i) => i.actionType === 'TUNING')
    .toSorted(
      (a, b) =>
        (SUGG_STATUS_ORDER[a.status] ?? 9) - (SUGG_STATUS_ORDER[b.status] ?? 9) ||
        String(b.updatedAt ?? '').localeCompare(String(a.updatedAt ?? '')),
    ),
);

async function loadOpenItems(): Promise<void> {
  openLoading.value = true;
  try {
    // 工单口径状态映射（v1.x PENDING,HANDLING,REOPENED → PENDING,EXECUTING,REOPENED）；
    // 后端 /orders status 为单值，按状态并行请求后合并
    const statuses: HandlingApi.OrderStatus[] = [
      'PENDING',
      'EXECUTING',
      'REOPENED',
    ];
    const results = await Promise.all(
      statuses.map((status) =>
        getHandlingOrdersApi({
          page: 1,
          pageSize: 100, // 后端 /handling/orders pageSize 上限 le=100
          status,
          plantNodeId: selectedPlantNodeId.value,
        }),
      ),
    );
    openItems.value = results.flatMap((r) => r.items);
  } catch {
    openItems.value = [];
  } finally {
    openLoading.value = false;
  }
}

// ===== 右主区：回路总览表格 =====
interface OverviewRow {
  loopId: string;
  tagName: string;
  description: null | string;
  importanceLevel: null | number;
  latestScore: null | number;
  primaryCategoryLabel: null | string;
  suggCount: number;
  suggFirst: null | string;
  /** 原始最新诊断概览（诊断基线条消费，含 metricSummary） */
  rawLatest: DiagnosisApi.LatestRunItem;
  /** 实时值容器（P/I/D 经 WS 推送更新；结构对齐 useLoopRealtime） */
  currentValues: {
    mode: null | number;
    modeLabel: null | string;
    op: null | number;
    pidD: null | number;
    pidI: null | number;
    pidP: null | number;
    pv: null | number;
    pvQuality: null | string;
    readAt: null | string;
    sp: null | number;
  };
}

const overviewLoading = ref(false);
const overviewRows = ref<OverviewRow[]>([]);

const overviewColumns: TableColumnsType = [
  { key: 'tagName', title: '回路编号', width: 130 },
  { key: 'description', title: '回路名称', ellipsis: true },
  { key: 'importanceLevel', title: '等级', width: 56, align: 'center' },
  { key: 'latestScore', title: '性能评分', width: 80, align: 'center' },
  { key: 'scoreGrade', title: '性能等级', width: 76, align: 'center' },
  { key: 'diagnosis', title: '诊断结论', width: 120, ellipsis: true },
  { key: 'suggestion', title: '处置建议摘要', ellipsis: true },
  { key: 'pid', title: 'P / I / D 参数', width: 130, align: 'center' },
  { key: 'action', title: '操作', width: 72 },
];

async function loadOverview(): Promise<void> {
  overviewLoading.value = true;
  try {
    const latest = await getDiagnosisRunsLatestApi(selectedPlantNodeId.value);
    // 开放处置工单按回路分组（最新在前）
    const byLoop = new Map<string, HandlingApi.OrderItem[]>();
    for (const it of openItems.value) {
      const arr = byLoop.get(it.loopId) ?? [];
      arr.push(it);
      byLoop.set(it.loopId, arr);
    }
    overviewRows.value = latest.items.map((l) => {
      const items = (byLoop.get(l.loopId) ?? []).toSorted((a, b) =>
        String(b.updatedAt ?? '').localeCompare(String(a.updatedAt ?? '')),
      );
      return {
        loopId: l.loopId,
        tagName: l.loopTagName,
        description: l.loopDescription ?? null,
        importanceLevel: l.importanceLevel ?? null,
        latestScore: l.latestScore ?? null,
        primaryCategoryLabel: l.primaryCategoryLabel ?? null,
        suggCount: items.length,
        suggFirst: items[0]?.title ?? null,
        rawLatest: l,
        currentValues: {
          mode: null,
          modeLabel: null,
          op: null,
          pidD: null,
          pidI: null,
          pidP: null,
          pv: null,
          pvQuality: null,
          readAt: null,
          sp: null,
        },
      };
    });
    // P/I/D 初值：并行拉回路详情（量级=装置范围内回路数；单回路失败不阻断）
    await Promise.allSettled(
      overviewRows.value.map(async (row) => {
        const d = await getLoopDetailApi(row.loopId);
        const rp = (d.runtimeParams ?? {}) as {
          pidD?: null | number;
          pidI?: null | number;
          pidP?: null | number;
        };
        row.currentValues.pidP = rp.pidP ?? null;
        row.currentValues.pidI = rp.pidI ?? null;
        row.currentValues.pidD = rp.pidD ?? null;
      }),
    );
  } catch {
    overviewRows.value = [];
  } finally {
    overviewLoading.value = false;
  }
}

function fmtPid(v: null | number | undefined): string {
  return fmtNum2(v);
}

/** 选中回路的位号（总览/清单缓存中查找，用于流程区标题） */
const selectedLoopTag = computed(() => {
  const id = ctx.loopId.value;
  if (!id) return '';
  return (
    overviewRows.value.find((r) => r.loopId === id)?.tagName ??
    loopItems.value.find((l) => l.loopId === id)?.tagName ??
    ''
  );
});

// ===== 诊断基线（选中回路最新诊断指标，2026-08-19）=====
// 整定目标即改善振荡率/稳定时间等负向指标：展示最新诊断基线供整定前后对照。
// 优先取总览缓存（装置级 latest 已含 metricSummary）；跨装置跳入等场景
// 总览不含该回路时，单回路 latest 兜底拉取。
const fallbackDiagnosis = ref<DiagnosisApi.LatestRunItem | null>(null);

watch(
  () => ctx.loopId.value,
  async (id) => {
    fallbackDiagnosis.value = null;
    if (!id) return;
    if (overviewRows.value.some((r) => r.loopId === id && r.rawLatest.runId)) {
      return;
    }
    const data = await getDiagnosisRunsLatestApi(undefined, id).catch(() => null);
    fallbackDiagnosis.value = data?.items[0] ?? null;
  },
);

const selectedDiagnosis = computed<DiagnosisApi.LatestRunItem | null>(() => {
  const id = ctx.loopId.value;
  if (!id) return null;
  return (
    overviewRows.value.find((r) => r.loopId === id)?.rawLatest ??
    fallbackDiagnosis.value ??
    null
  );
});

/** 诊断基线 meta：置信度 · 复核状态 · 时间（naive UTC 补 Z 转本地） */
const diagBaselineMeta = computed(() => {
  const d = selectedDiagnosis.value;
  if (!d?.runId) return '';
  const parts: string[] = [];
  if (d.primaryConfidence != null)
    parts.push(`置信度 ${Math.round(d.primaryConfidence * 100)}%`);
  parts.push(d.reviewStatus === 'REVIEWED' ? '已复核' : '待复核');
  if (d.lastDiagnosedAt) {
    const s = d.lastDiagnosedAt;
    const iso = /[Zz]|[+-]\d{2}:?\d{2}$/.test(s) ? s : `${s}Z`;
    parts.push(dayjs(iso).format('MM-DD HH:mm'));
  }
  return parts.join(' · ');
});

/** 诊断基线负向指标紧凑文本（率类 + 非率类原值透传） */
const diagBaselineMetrics = computed(() => {
  const neg = selectedDiagnosis.value?.metricSummary?.negative;
  if (!neg) return '';
  const parts: string[] = [];
  const defs: Array<{ key: keyof typeof neg; label: string; unit: string }> = [
    { key: 'oscillationRate', label: '振荡率', unit: '%' },
    { key: 'badValueRate', label: '坏值率', unit: '%' },
    { key: 'saturationRate', label: '饱和率', unit: '%' },
    { key: 'stictionIndex', label: '粘滞系数', unit: '%' },
    { key: 'settlingTime', label: '稳定时间', unit: ' s' },
    { key: 'outputTravelIndex', label: '行程指数', unit: '' },
  ];
  for (const d of defs) {
    const v = neg[d.key];
    if (v != null) parts.push(`${d.label} ${Number(v).toFixed(1)}${d.unit}`);
  }
  return parts.join(' · ');
});

/** 跳转诊断工作台（携带回路上下文） */
function goDiagnose(): void {
  const loopId = ctx.loopId.value;
  if (!loopId) return;
  router.push({
    path: '/diagnosis/workbench',
    query: { loopId, from: 'tuning' },
  });
}

// ===== 实时更新（P/I/D 由全局 WS 推送，初值来自回路详情） =====
const { applyMessage, onMessage, start, stop } = useLoopRealtime();

onMessage((msg) => {
  applyMessage(msg, overviewRows.value as any[]);
});

// ===== 装载 =====
async function reloadForNode(): Promise<void> {
  await Promise.all([loadLoops(), loadOpenItems()]);
  await loadOverview();
}

onMounted(async () => {
  start();
  await Promise.all([loadPlantTree(), reloadForNode()]);
  const loopId = route.query.loopId as string | undefined;
  if (loopId) {
    fromDiagnosis.value = route.query.from === 'diagnosis';
    ctx.selectLoop(loopId);
  }
});

onBeforeUnmount(() => {
  stop();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      subtitle="回路 PID 参数优化：辨识 → 整定矩阵 → 仿真对比 → 方案确认"
      title="整定工作台"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          :loading="overviewLoading || openLoading"
          @click="reloadForNode"
        />
      </template>
    </ClpmPageToolbar>

    <div class="tuning-layout">
      <!-- ===== 左脊柱：装置树 + 回路清单 + 整定建议 ===== -->
      <aside class="tuning-sidebar">
        <div class="tuning-sidebar__section-title">
          <span>装置</span>
          <button
            v-if="plantTreeSelectedKeys.length > 0"
            class="tuning-sidebar__clear"
            @click="handlePlantTreeSelect([])"
          >
            清除
          </button>
        </div>
        <Spin :spinning="plantTreeLoading" size="small">
          <Tree
            v-if="plantTreeData.length > 0"
            v-model:expanded-keys="plantTreeExpandedKeys"
            v-model:selected-keys="plantTreeSelectedKeys"
            :block-node="true"
            :show-line="false"
            :tree-data="plantTreeData as any"
            class="tuning-plant-tree"
            @select="handlePlantTreeSelect"
          />
          <div v-else class="tuning-sidebar__empty">暂无装置数据</div>
        </Spin>

        <div class="tuning-sidebar__section-title">
          <span>回路</span>
          <span class="text-xs text-neutral-400">
            {{ filteredLoops.length }}
          </span>
        </div>
        <Input
          v-model:value="loopKeyword"
          allow-clear
          placeholder="搜索位号/描述..."
          size="small"
        />
        <div class="tuning-sidebar__list-wrap">
          <Spin :spinning="loopLoading" size="small">
            <div
              v-for="item in filteredLoops"
              :key="item.loopId"
              class="tuning-loop-item"
              :class="{
                'tuning-loop-item--active': ctx.loopId.value === item.loopId,
              }"
              role="button"
              tabindex="0"
              :title="item.description || item.tagName"
              @click="ctx.selectLoop(item.loopId)"
              @keydown.enter="ctx.selectLoop(item.loopId)"
            >
              <span class="tuning-loop-item__tag">{{ item.tagName }}</span>
              <span class="tuning-loop-item__unit">{{ item.unitName }}</span>
            </div>
            <Empty
              v-if="!loopLoading && filteredLoops.length === 0"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="tuning-sidebar__empty"
              description="暂无回路"
            />
          </Spin>
        </div>

        <div class="tuning-sidebar__section-title">
          <span>整定建议</span>
          <span class="text-xs text-neutral-400">
            {{ tuningSuggestions.length }} 项
          </span>
        </div>
        <div class="tuning-sidebar__sugg-wrap">
          <Spin :spinning="openLoading" size="small">
            <div
              v-for="item in tuningSuggestions"
              :key="item.id"
              class="tuning-sugg-item"
              :class="{
                'tuning-sugg-item--active': ctx.loopId.value === item.loopId,
              }"
              role="button"
              tabindex="0"
              :title="item.title"
              @click="ctx.selectLoop(item.loopId)"
              @keydown.enter="ctx.selectLoop(item.loopId)"
            >
              <span class="tuning-sugg-item__tag">{{ item.loopTagName }}</span>
              <span class="tuning-sugg-item__meta">
                <span
                  class="tuning-sugg-item__status"
                  :class="`is-${item.status.toLowerCase()}`"
                >
                  {{ item.statusLabel }}
                </span>
              </span>
            </div>
            <Empty
              v-if="!openLoading && tuningSuggestions.length === 0"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="tuning-sidebar__empty"
              description="暂无整定建议"
            />
          </Spin>
        </div>
      </aside>

      <!-- ===== 右主区 ===== -->
      <div class="tuning-main">
        <Alert
          v-if="fromDiagnosis"
          class="mb-2"
          type="info"
          message="来自诊断中心的整定请求：已预填回路，可直接发起过程辨识"
          show-icon
          closable
        />

        <!-- 已选回路：4 锚点整定流程 -->
        <template v-if="ctx.loopId.value">
          <div class="tuning-flow-header">
            <button class="tuning-back" @click="ctx.clearLoop()">
              ← 返回回路列表
            </button>
            <span class="tuning-flow-tag">{{ selectedLoopTag }}</span>
          </div>

          <div class="tuning-anchor-nav">
            <a
              v-for="a in anchors"
              :key="a.href"
              class="tuning-anchor-link"
              @click.prevent="scrollTo(a.href)"
            >
              {{ a.label }}
            </a>
          </div>

          <!-- 诊断基线条（2026-08-19）：最新诊断结论与负向指标，整定前后对照 -->
          <div v-if="selectedDiagnosis?.runId" class="diag-baseline">
            <span class="diag-baseline__label">诊断基线</span>
            <Tag
              class="diag-baseline__cat"
              :color="
                SEVERITY_COLOR[selectedDiagnosis.severity ?? ''] ?? 'default'
              "
            >
              {{ selectedDiagnosis.primaryCategoryLabel ?? '—' }}
            </Tag>
            <span class="diag-baseline__meta">{{ diagBaselineMeta }}</span>
            <span v-if="diagBaselineMetrics" class="diag-baseline__metrics">
              {{ diagBaselineMetrics }}
            </span>
          </div>
          <div v-else class="diag-baseline diag-baseline--empty">
            <span class="diag-baseline__label">诊断基线</span>
            <span class="diag-baseline__meta">该回路尚未诊断，建议先诊断获取基线</span>
            <a class="diag-baseline__link" @click.prevent="goDiagnose">
              去诊断 →
            </a>
          </div>

          <IdentifySection :ctx="ctx" />
          <MatrixSection :ctx="ctx" />
          <SimulateSection :ctx="ctx" />
          <ConfirmSection :ctx="ctx" />
        </template>

        <!-- 未选回路：该节点下所有回路总览 -->
        <Card v-else size="small">
          <template #title>
            <span class="section-title">回路总览</span>
            <span class="ml-2 text-xs font-normal text-neutral-400">
              {{ overviewRows.length }} 个回路 · 点击行进入整定流程
            </span>
          </template>
          <Table
            :columns="overviewColumns"
            :data-source="overviewRows"
            :loading="overviewLoading"
            :pagination="false"
            size="small"
            row-key="loopId"
            :custom-row="
              (record: any) => ({
                onClick: () => ctx.selectLoop(record.loopId),
              })
            "
            :custom-cell="() => ({ style: { cursor: 'pointer' } })"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'tagName'">
                <span class="font-medium">{{ record.tagName }}</span>
              </template>
              <template v-else-if="column.key === 'importanceLevel'">
                <span
                  v-if="record.importanceLevel"
                  :style="{
                    color: IMPORTANCE_LEVEL_COLOR[record.importanceLevel],
                  }"
                >
                  {{ IMPORTANCE_LEVEL_TEXT[record.importanceLevel] ?? '—' }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'latestScore'">
                <span
                  v-if="record.latestScore != null"
                  class="clpm-num font-medium"
                  :style="{
                    color: scoreGrade(record.latestScore)?.color,
                  }"
                >
                  {{ record.latestScore.toFixed(1) }}
                </span>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'scoreGrade'">
                <Tag
                  v-if="scoreGrade(record.latestScore)"
                  :color="scoreGrade(record.latestScore)?.color"
                  class="mr-0"
                >
                  {{ scoreGrade(record.latestScore)?.label }}
                </Tag>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'diagnosis'">
                <span
                  v-if="record.primaryCategoryLabel"
                  :title="record.primaryCategoryLabel"
                >
                  {{ record.primaryCategoryLabel }}
                </span>
                <span v-else class="text-neutral-400">未诊断</span>
              </template>
              <template v-else-if="column.key === 'suggestion'">
                <Tooltip
                  v-if="record.suggFirst"
                  :title="record.suggFirst"
                  placement="topLeft"
                >
                  <span class="text-xs">
                    {{ record.suggFirst }}
                    <Tag
                      v-if="record.suggCount > 1"
                      color="orange"
                      class="mr-0"
                    >
                      {{ record.suggCount }}
                    </Tag>
                  </span>
                </Tooltip>
                <span v-else class="text-neutral-400">—</span>
              </template>
              <template v-else-if="column.key === 'pid'">
                <span class="clpm-num text-xs">
                  {{ fmtPid(record.currentValues.pidP) }} /
                  {{ fmtPid(record.currentValues.pidI) }} /
                  {{ fmtPid(record.currentValues.pidD) }}
                </span>
              </template>
              <template v-else-if="column.key === 'action'">
                <Tooltip
                  title="进入整定流程前会校验适用性（L0/L1 阻止，L2 提示）"
                  placement="top"
                >
                  <Button
                    type="link"
                    size="small"
                    class="p-0"
                    @click.stop="handleGoTuning(record as OverviewRow)"
                  >
                    调参优化
                  </Button>
                </Tooltip>
              </template>
            </template>
          </Table>
        </Card>
      </div>
    </div>
  </Page>
</template>

<style scoped>
.tuning-layout {
  display: flex;
  gap: 12px;
  align-items: stretch;
}

/* ===== 左脊柱（对齐回路/诊断工作台） ===== */
.tuning-sidebar {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  gap: 6px;
  width: 248px;
  max-height: calc(100vh - 180px);
  padding: 10px 10px 8px;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.tuning-sidebar__section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 2px 0;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.tuning-sidebar__clear {
  padding: 0 4px;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.tuning-sidebar__empty {
  padding: 12px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

/* 装置树：紧凑（28px 行高） */
.tuning-plant-tree {
  flex-shrink: 0;
  max-height: 140px;
  overflow: auto;
  font-size: 12px;
}

.tuning-plant-tree :deep(.ant-tree-node-content-wrapper) {
  min-height: 28px;
  line-height: 28px;
}

.tuning-plant-tree :deep(.ant-tree-treenode) {
  padding-top: 0;
  padding-bottom: 0;
}

/* 回路清单（主区，flex-1） */
.tuning-sidebar__list-wrap {
  flex: 1;
  min-height: 140px;
  padding-top: 6px;
  overflow: auto;
  border-top: 1px solid hsl(var(--border));
}

.tuning-loop-item {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 28px;
  padding: 0 4px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}

.tuning-loop-item:hover {
  background: hsl(var(--accent));
}

.tuning-loop-item--active {
  background: hsl(var(--accent));
  box-shadow: inset 1px 0 0 hsl(var(--primary));
}

.tuning-loop-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  white-space: nowrap;
}

.tuning-loop-item__unit {
  flex-shrink: 0;
  max-width: 72px;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

/* 整定建议（底部固定高度区） */
.tuning-sidebar__sugg-wrap {
  flex-shrink: 0;
  max-height: 150px;
  padding-top: 6px;
  overflow: auto;
  border-top: 1px solid hsl(var(--border));
}

.tuning-sugg-item {
  display: flex;
  gap: 6px;
  align-items: center;
  min-height: 28px;
  padding: 0 4px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}

.tuning-sugg-item:hover {
  background: hsl(var(--accent));
}

.tuning-sugg-item--active {
  background: hsl(var(--accent));
  box-shadow: inset 1px 0 0 hsl(var(--primary));
}

.tuning-sugg-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 500;
  white-space: nowrap;
}

.tuning-sugg-item__meta {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
  margin-left: auto;
}

.tuning-sugg-item__prio {
  font-size: 10px;
  font-weight: 600;
  color: hsl(var(--destructive));
}

.tuning-sugg-item__status {
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.tuning-sugg-item__status.is-pending {
  color: hsl(var(--warning, #b45309));
}

.tuning-sugg-item__status.is-handling {
  color: hsl(var(--primary));
}

.tuning-sugg-item__status.is-reopened {
  color: hsl(var(--destructive));
}

/* ===== 右主区 ===== */
.tuning-main {
  flex: 1;
  min-width: 0;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
}

.tuning-flow-header {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.tuning-back {
  padding: 2px 8px;
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: none;
}

.tuning-back:hover {
  text-decoration: underline;
}

.tuning-flow-tag {
  font-size: 13px;
  font-weight: 600;
}

.tuning-anchor-nav {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  gap: 16px;
  padding: 6px 12px;
  margin-bottom: 8px;
  background: hsl(var(--background));
  border-bottom: 1px solid hsl(var(--border));
}

.tuning-anchor-link {
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
}

.tuning-anchor-link:hover {
  text-decoration: underline;
}

/* ===== 诊断基线条（2026-08-19）：最新诊断结论 + 负向指标紧凑横排 ===== */
.diag-baseline {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  align-items: center;
  padding: 5px 12px;
  margin-bottom: 8px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.diag-baseline__label {
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--foreground) / 60%);
}

.diag-baseline__cat {
  margin-right: 0;
  font-size: 11px;
  line-height: 18px;
}

.diag-baseline__meta {
  font-size: 11px;
  color: hsl(var(--foreground) / 45%);
}

.diag-baseline__metrics {
  margin-left: auto;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground) / 75%);
}

.diag-baseline--empty .diag-baseline__meta {
  color: hsl(var(--foreground) / 40%);
}

.diag-baseline__link {
  margin-left: auto;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  white-space: nowrap;
}

.diag-baseline__link:hover {
  text-decoration: underline;
}

:deep(.tuning-section) {
  margin-bottom: 12px;
  scroll-margin-top: 48px;
}
</style>
