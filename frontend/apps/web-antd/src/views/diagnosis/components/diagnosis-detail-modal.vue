<script setup lang="ts">
/**
 * 诊断详情弹窗 —— 遮罩模式，点击概览行弹出（2026-08-18 v2）。
 *
 * 交互：标题栏可拖动移动；右下角手柄可调整宽高（初始 720px，较 v1 收窄）。
 * 结构：顶部三信息区（回路基本信息 / 最新性能评估指标 / 诊断基本信息）+
 * 下方三个 Tab（诊断结论 / 证据链 / 处置建议，DiagnosisResultPanel section 模式）。
 */
import { computed, nextTick, ref, watch } from 'vue';

import dayjs from 'dayjs';
import {
  Empty,
  Modal,
  Skeleton,
  Spin,
  TabPane,
  Tabs,
} from 'ant-design-vue';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { KpiSnapshotItem } from '#/api/metric';
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import { getDiagnosisRunDetailApi } from '#/api/diagnosis';
import { getLoopSnapshotsApi } from '#/api/metric';
import { getLoopListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
  scoreGrade,
} from '../constants';
import DiagnosisResultPanel from './diagnosis-result-panel.vue';

const props = defineProps<{
  item: DiagnosisApi.LatestRunItem | null;
}>();

const open = defineModel<boolean>('open', { default: false });

// ===== 数据加载 =====
const detailLoading = ref(false);
const runDetail = ref<DiagnosisApi.RunDetail | null>(null);
const kpiLoading = ref(false);
const kpi = ref<KpiSnapshotItem | null>(null);
/** 回路台账（量程/单元等概览行没有的字段） */
const loopInfo = ref<LoopApi.LoopListItem | null>(null);

/** plant node 平铺索引（"装置.单元"路径回溯；加载失败回退 unitName） */
const nodeIndex = ref(
  new Map<string, { name: string; parentId: null | string }>(),
);
let nodeIndexLoaded = false;

async function ensureNodeIndex(): Promise<void> {
  if (nodeIndexLoaded) return;
  try {
    const tree = await getPlantNodeTreeApi();
    const idx = new Map<string, { name: string; parentId: null | string }>();
    const walk = (nodes: PlantNodeApi.PlantNode[]) => {
      for (const n of nodes) {
        idx.set(n.id, { name: n.name, parentId: n.parentId });
        if (n.children?.length) walk(n.children);
      }
    };
    walk(tree);
    nodeIndex.value = idx;
  } catch {
    /* 树加载失败时 unitPath 回退 unitName */
  }
  nodeIndexLoaded = true;
}

/** naive UTC → 本地时间 */
function fmtLocal(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso) ? naiveIso : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

function fmtRate(v?: null | number): string {
  return v == null ? '—' : `${v.toFixed(1)}%`;
}

async function load(item: DiagnosisApi.LatestRunItem) {
  // KPI 快照 / 回路台账 / 诊断详情并行加载
  kpiLoading.value = true;
  kpi.value = null;
  getLoopSnapshotsApi({ loopId: item.loopId, latestOnly: true, pageSize: 1 })
    .then((res) => {
      kpi.value = res.items?.[0] ?? null;
    })
    .catch(() => {
      kpi.value = null;
    })
    .finally(() => {
      kpiLoading.value = false;
    });

  loopInfo.value = null;
  getLoopListApi({ keyword: item.loopTagName, page: 1, pageSize: 20 })
    .then((res) => {
      loopInfo.value =
        res.items.find((l) => l.loopId === item.loopId) ?? null;
    })
    .catch(() => {
      loopInfo.value = null;
    });
  ensureNodeIndex();

  if (!item.runId) {
    runDetail.value = null;
    detailLoading.value = false;
    return;
  }
  detailLoading.value = true;
  runDetail.value = null;
  try {
    runDetail.value = await getDiagnosisRunDetailApi(item.runId);
  } catch {
    runDetail.value = null;
  } finally {
    detailLoading.value = false;
  }
}

// ===== 顶部信息区 =====
const grade = computed(() => scoreGrade(kpi.value?.score));

/** 所属装置.单元（plant node 树回溯路径） */
const unitPath = computed(() => {
  const info = loopInfo.value;
  if (!info) return '—';
  const parts: string[] = [];
  let cur = nodeIndex.value.get(info.unitId);
  while (cur) {
    parts.unshift(cur.name);
    cur = cur.parentId ? nodeIndex.value.get(cur.parentId) : undefined;
  }
  return parts.length > 0
    ? parts.join('.')
    : (info.unitName || '—');
});

/** PV 量程文本（min~max 单位） */
const rangeText = computed(() => {
  const r = loopInfo.value?.pvRange;
  if (!r || (r.min == null && r.max == null)) return '—';
  const unit = loopInfo.value?.pvUnit ?? '';
  return `${r.min ?? '?'}~${r.max ?? '?'}${unit ? ` ${unit}` : ''}`;
});

/** 性能评估指标 6 率（用户指定口径） */
const kpiRates = computed(() => {
  const k = kpi.value;
  return [
    { label: '有效自控率', value: fmtRate(k?.effectiveAutoRate) },
    { label: '平稳率', value: fmtRate(k?.steadyRate) },
    { label: '准确率', value: fmtRate(k?.accuracyRate) },
    { label: '快速率', value: fmtRate(k?.fastRate) },
    { label: '振荡率', value: fmtRate(k?.oscillationRate) },
    { label: '饱和率', value: fmtRate(k?.saturationRate) },
  ];
});

/** 诊断时间窗口（概览行优先，详情兜底） */
const twStart = computed(
  () => props.item?.timeWindowStart ?? runDetail.value?.timeWindowStart,
);
const twEnd = computed(
  () => props.item?.timeWindowEnd ?? runDetail.value?.timeWindowEnd,
);

// ===== Tabs =====
const activeTab = ref('conclusion');

// ===== 拖动 + 调整宽高 =====
const modalW = ref(720);
const bodyH = ref(520);
const MIN_W = 640;
const MIN_H = 360;

function getModalEl(): HTMLElement | null {
  return document.querySelector<HTMLElement>('.diag-detail-modal .ant-modal');
}

/** 标题栏按下 → 拖动移动（切绝对定位，clamp 在视口内） */
function onHeaderMouseDown(e: MouseEvent) {
  if (e.button !== 0) return;
  const modal = getModalEl();
  const wrap = document.querySelector<HTMLElement>('.diag-detail-modal');
  if (!modal || !wrap) return;
  const rect = modal.getBoundingClientRect();
  const wrapRect = wrap.getBoundingClientRect();
  modal.style.position = 'absolute';
  modal.style.margin = '0';
  modal.style.left = `${rect.left - wrapRect.left}px`;
  modal.style.top = `${rect.top - wrapRect.top}px`;
  const startX = e.clientX;
  const startY = e.clientY;
  const origLeft = rect.left - wrapRect.left;
  const origTop = rect.top - wrapRect.top;
  const move = (ev: MouseEvent) => {
    modal.style.left = `${Math.min(Math.max(origLeft + ev.clientX - startX, 0), window.innerWidth - 80)}px`;
    modal.style.top = `${Math.min(Math.max(origTop + ev.clientY - startY, 0), window.innerHeight - 48)}px`;
  };
  const up = () => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
  e.preventDefault();
}

/** 右下角手柄按下 → 调整宽高 */
function onResizeStart(e: MouseEvent) {
  if (e.button !== 0) return;
  const startX = e.clientX;
  const startY = e.clientY;
  const startW = modalW.value;
  const startH = bodyH.value;
  const move = (ev: MouseEvent) => {
    modalW.value = Math.min(
      Math.max(startW + ev.clientX - startX, MIN_W),
      window.innerWidth - 32,
    );
    bodyH.value = Math.min(
      Math.max(startH + ev.clientY - startY, MIN_H),
      window.innerHeight - 96,
    );
  };
  const up = () => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    // 通知 echarts 自适应新宽度（vben useEcharts 监听 window resize）
    window.dispatchEvent(new Event('resize'));
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
  e.preventDefault();
  e.stopPropagation();
}

/** 关闭时恢复居中定位（尺寸保留用户偏好） */
function resetModalPosition() {
  const modal = getModalEl();
  if (!modal) return;
  modal.style.position = '';
  modal.style.margin = '';
  modal.style.left = '';
  modal.style.top = '';
}

let dragBound = false;

function bindDragOnce() {
  if (dragBound) return;
  const header = document.querySelector<HTMLElement>(
    '.diag-detail-modal .ant-modal-header',
  );
  if (!header) return;
  header.addEventListener('mousedown', onHeaderMouseDown);
  dragBound = true;
}

watch(open, (v) => {
  if (v && props.item) {
    activeTab.value = 'conclusion';
    load(props.item);
    nextTick(bindDragOnce);
  } else if (!v) {
    resetModalPosition();
  }
});
</script>

<template>
  <Modal
    v-model:open="open"
    :title="`诊断详情 · ${item?.loopTagName ?? ''}`"
    :footer="null"
    :width="modalW"
    :body-style="{ height: `${bodyH}px`, overflow: 'hidden' }"
    wrap-class-name="diag-detail-modal"
  >
    <div v-if="item" class="diag-detail-body">
      <!-- ===== 顶部三信息区（Tabs 上方） ===== -->
      <div class="diag-detail-top">
        <!-- ① 回路基本信息 -->
        <div class="diag-detail-row">
          <span class="diag-detail-row__title">回路基本信息</span>
          <span class="diag-info">
            <span class="diag-info__k">位号</span>
            <span class="diag-info__v font-semibold">{{ item.loopTagName }}</span>
          </span>
          <span class="diag-info">
            <span class="diag-info__k">名称</span>
            <span class="diag-info__v">{{ item.loopDescription || '—' }}</span>
          </span>
          <span class="diag-info">
            <span class="diag-info__k">等级</span>
            <span
              v-if="item.importanceLevel"
              class="diag-info__v"
              :style="{ color: IMPORTANCE_LEVEL_COLOR[item.importanceLevel] }"
            >
              {{ IMPORTANCE_LEVEL_TEXT[item.importanceLevel] }}
            </span>
            <span v-else class="diag-info__v">—</span>
          </span>
          <span class="diag-info">
            <span class="diag-info__k">量程</span>
            <span class="diag-info__v tabular-nums">{{ rangeText }}</span>
          </span>
          <span class="diag-info">
            <span class="diag-info__k">装置.单元</span>
            <span class="diag-info__v">{{ unitPath }}</span>
          </span>
        </div>

        <!-- ② 最新性能评估指标 -->
        <div class="diag-detail-row">
          <span class="diag-detail-row__title">性能评估指标</span>
          <Skeleton
            v-if="kpiLoading"
            :paragraph="{ rows: 1 }"
            active
            class="flex-1"
          />
          <template v-else-if="kpi">
            <span class="diag-info">
              <span class="diag-info__k">综合评分</span>
              <span
                class="diag-info__v text-base font-semibold tabular-nums"
                :style="{ color: grade?.color }"
              >
                {{ kpi.score != null ? kpi.score.toFixed(1) : '—' }}
              </span>
            </span>
            <span class="diag-info">
              <span class="diag-info__k">等级</span>
              <span
                class="diag-info__v"
                :style="{ color: grade?.color }"
              >
                {{ grade?.label ?? '—' }}
              </span>
            </span>
            <span
              v-for="r in kpiRates"
              :key="r.label"
              class="diag-info"
            >
              <span class="diag-info__k">{{ r.label }}</span>
              <span class="diag-info__v tabular-nums">{{ r.value }}</span>
            </span>
            <span class="diag-info">
              <span class="diag-info__k">评估窗口</span>
              <span class="diag-info__v tabular-nums">
                {{ fmtLocal(kpi.tsStart) }} ~ {{ fmtLocal(kpi.tsEnd) }}
              </span>
            </span>
          </template>
          <span v-else class="text-xs text-neutral-400">
            暂无性能评估数据（尚未生成 KPI 快照）
          </span>
        </div>

        <!-- ③ 诊断基本信息 -->
        <div class="diag-detail-row">
          <span class="diag-detail-row__title">诊断基本信息</span>
          <span class="diag-info">
            <span class="diag-info__k">诊断次序</span>
            <span class="diag-info__v">
              {{ item.runCount ? `第 ${item.runCount} 次` : '未诊断' }}
            </span>
          </span>
          <span class="diag-info">
            <span class="diag-info__k">诊断时间</span>
            <span class="diag-info__v tabular-nums">
              {{ fmtLocal(item.lastDiagnosedAt) }}
            </span>
          </span>
          <span class="diag-info">
            <span class="diag-info__k">触发方式</span>
            <span
              v-if="item.triggerType"
              class="diag-info__v"
              :style="{ color: TRIGGER_TYPE_COLOR[item.triggerType] }"
            >
              {{
                item.triggerTypeLabel ??
                TRIGGER_TYPE_TEXT[item.triggerType] ??
                item.triggerType
              }}
            </span>
            <span v-else class="diag-info__v">—</span>
          </span>
          <span class="diag-info">
            <span class="diag-info__k">时间窗口</span>
            <span class="diag-info__v tabular-nums">
              {{ fmtLocal(twStart) }} ~ {{ fmtLocal(twEnd) }}
            </span>
          </span>
        </div>
      </div>

      <!-- ===== 三 Tab：诊断结论 / 证据链 / 处置建议 ===== -->
      <Tabs v-model:active-key="activeTab" class="diag-detail-tabs" size="small">
        <TabPane key="conclusion" tab="诊断结论">
          <Empty v-if="!item.runId" class="py-6" description="该回路尚未诊断" />
          <Spin v-else-if="detailLoading" class="block py-6" />
          <DiagnosisResultPanel
            v-else-if="runDetail"
            :detail="runDetail"
            section="conclusion"
          />
          <Empty v-else class="py-6" description="诊断详情加载失败" />
        </TabPane>
        <TabPane key="evidence" tab="证据链">
          <Empty v-if="!item.runId" class="py-6" description="该回路尚未诊断" />
          <Spin v-else-if="detailLoading" class="block py-6" />
          <DiagnosisResultPanel
            v-else-if="runDetail"
            :detail="runDetail"
            section="evidence"
          />
          <Empty v-else class="py-6" description="诊断详情加载失败" />
        </TabPane>
        <TabPane key="advice" tab="处置建议">
          <Empty v-if="!item.runId" class="py-6" description="该回路尚未诊断" />
          <Spin v-else-if="detailLoading" class="block py-6" />
          <DiagnosisResultPanel
            v-else-if="runDetail"
            :detail="runDetail"
            section="advice"
          />
          <Empty v-else class="py-6" description="诊断详情加载失败" />
        </TabPane>
      </Tabs>

      <!-- 右下角宽高手柄 -->
      <div class="diag-detail-resize" @mousedown="onResizeStart" />
    </div>
  </Modal>
</template>

<style>
/* 弹窗 DOM 挂载于 body（wrapClassName 定位），需全局样式 */
.diag-detail-modal .ant-modal-header {
  cursor: move;
  user-select: none;
}

.diag-detail-modal .diag-detail-body {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.diag-detail-modal .diag-detail-top {
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  gap: 6px;
  max-height: 45%;
  margin-bottom: 8px;
  padding: 8px 10px;
  overflow: auto;
  background: hsl(var(--accent) / 25%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-detail-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  align-items: baseline;
  font-size: 12px;
  line-height: 20px;
}

.diag-detail-modal .diag-detail-row__title {
  flex-shrink: 0;
  width: 76px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-info {
  display: inline-flex;
  gap: 4px;
  align-items: baseline;
  min-width: 0;
}

.diag-detail-modal .diag-info__k {
  color: hsl(var(--accent-foreground) / 55%);
  white-space: nowrap;
}

.diag-detail-modal .diag-info__v {
  font-weight: 500;
}

/* Tabs 占满剩余高度，tab 内容区滚动 */
.diag-detail-modal .diag-detail-tabs {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
}

.diag-detail-modal .diag-detail-tabs > .ant-tabs-content-holder {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-right: 2px;
}

/* 右下角宽高手柄 */
.diag-detail-modal .diag-detail-resize {
  position: absolute;
  right: 0;
  bottom: 0;
  z-index: 10;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  background: linear-gradient(
    135deg,
    transparent 50%,
    hsl(var(--border)) 50%
  );
  border-end-end-radius: 8px;
}

.diag-detail-modal .diag-detail-resize:hover {
  background: linear-gradient(
    135deg,
    transparent 50%,
    hsl(var(--primary) / 45%) 50%
  );
}
</style>
