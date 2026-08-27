<script setup lang="ts">
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { KpiSnapshotItem } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

/**
 * 诊断详情弹窗 —— 遮罩模式，点击概览行弹出（2026-08-18 v5）。
 *
 * 交互：标题栏可拖动移动；右下角手柄可调整宽高（默认 860×600，
 * 宽度按"性能评估指标单行不换行"测算：KPI 行 ≈795px + body padding）。
 * 结构：
 * - 顶部三信息行（回路基本信息 / 性能评估指标 / 诊断基本信息，均单行 nowrap）
 * - Tab1 诊断结论：AI 结论卡 + 人工复核表单（复核时间/复核人自动填入）
 * - Tab2 诊断证据：数据质量 / 波形快照 / 特征值（默认全展开）
 * - Tab3 处置建议：系统按诊断/复核结论自动带出 + 人工新增处置措施
 */
import { computed, nextTick, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { useUserStore } from '@vben/stores';

import {
  Button,
  Empty,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Select,
  Skeleton,
  Spin,
  TabPane,
  Tabs,
  Tag,
  Textarea,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  createRunActionApi,
  deleteRunActionApi,
  getDiagnosisRunDetailApi,
  getRunActionsApi,
  reviewDiagnosisRunApi,
  updateRunActionApi,
} from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import { getLoopSnapshotsApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { useModules } from '#/composables/use-modules';
// v2.0 处置双实体：建议状态映射改用建议侧常量（原 HANDLING_STATUS_* 为 v1.x 工单旧口径）
import {
  SUGGESTION_STATUS_COLOR,
  SUGGESTION_STATUS_TEXT,
} from '#/views/handling/constants';

import {
  CATEGORY_OPTIONS,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  scoreGrade,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
} from '../constants';
import DiagnosisResultPanel from './diagnosis-result-panel.vue';

const props = defineProps<{
  item: DiagnosisApi.LatestRunItem | null;
}>();

const emit = defineEmits<{ reviewed: [] }>();

const open = defineModel<boolean>('open', { default: false });

const router = useRouter();
const { moduleEnabled } = useModules();

/** 「去处置」：携带建议 actionId 跳诊断建议入口，自动打开建议详情抽屉
 * （深链接契约：/handling/suggestions?focus={suggestionId}） */
function gotoHandling(actionId: string): void {
  open.value = false;
  router.push({ path: '/handling/suggestions', query: { focus: actionId } });
}

/** 「去整定」：TUNING 类建议跳整定工作台并预填回路（09 设计方案 §6.5 联动） */
function gotoTuning(loopId: string): void {
  open.value = false;
  router.push({
    path: '/tuning/workbench',
    query: { from: 'diagnosis', loopId },
  });
}

const userStore = useUserStore();
/** 当前用户（复核人自动填入；后端以登录态为准，前端仅展示） */
const currentUserName = computed(
  () => userStore.userInfo?.realName || userStore.userInfo?.username || '—',
);

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
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso)
    ? naiveIso
    : `${naiveIso}Z`;
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
      loopInfo.value = res.items.find((l) => l.loopId === item.loopId) ?? null;
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
  return parts.length > 0 ? parts.join('.') : info.unitName || '—';
});

/** PV 量程文本（min~max 单位） */
const rangeText = computed(() => {
  const r = loopInfo.value?.pvRange;
  if (!r || (r.min == null && r.max == null)) return '—';
  const unit = loopInfo.value?.pvUnit ?? '';
  return `${r.min ?? '?'}~${r.max ?? '?'}${unit ? ` ${unit}` : ''}`;
});

/** 性能评估指标 6 率（单行紧凑展示） */
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

// ===== Tab1：人工复核表单 =====
const reviewForm = ref<{ reviewComment: string; reviewResults: string[] }>({
  reviewResults: [],
  reviewComment: '',
});
const reviewSubmitting = ref(false);
/** 复核时间展示：未复核=当前系统时间（自动填入）；已复核=上次复核时间 */
const reviewTimeText = computed(() => {
  if (props.item?.reviewStatus === 'REVIEWED' && props.item.reviewedAt) {
    return fmtLocal(props.item.reviewedAt);
  }
  return dayjs().format('YYYY-MM-DD HH:mm:ss');
});
/** 复核人展示：未复核=当前登录用户；已复核=上次复核人 */
const reviewerText = computed(() => {
  if (props.item?.reviewStatus === 'REVIEWED' && props.item.reviewedBy) {
    return props.item.reviewedBy;
  }
  return currentUserName.value;
});

async function submitReview() {
  if (!props.item?.runId) return;
  if (reviewForm.value.reviewResults.length === 0) {
    message.warning('请至少选择一项复核结论');
    return;
  }
  reviewSubmitting.value = true;
  try {
    await reviewDiagnosisRunApi(props.item.runId, {
      reviewComment: reviewForm.value.reviewComment || null,
      reviewResults: reviewForm.value.reviewResults,
    });
    message.success('复核已记录');
    emit('reviewed');
    // 刷新诊断详情 + 处置建议（后端已按复核结论重置系统建议）
    if (props.item.runId) {
      load(props.item);
      loadActions();
    }
  } finally {
    reviewSubmitting.value = false;
  }
}

// ===== Tab3：处置建议 =====
const actionsLoading = ref(false);
const actionItems = ref<DiagnosisApi.ActionItem[]>([]);
const newActionContent = ref('');
const newActionSubmitting = ref(false);

/** 行内编辑状态（仅 MANUAL）：editingId + 编辑内容 */
const editingActionId = ref('');
const editingContent = ref('');

async function loadActions(): Promise<void> {
  const runId = props.item?.runId;
  if (!runId) return;
  actionsLoading.value = true;
  try {
    const res = await getRunActionsApi(runId);
    actionItems.value = res.items;
  } catch {
    actionItems.value = [];
  } finally {
    actionsLoading.value = false;
  }
}

async function submitNewAction(): Promise<void> {
  const runId = props.item?.runId;
  const content = newActionContent.value.trim();
  if (!runId || !content) {
    if (!content) message.warning('请输入处置措施内容');
    return;
  }
  newActionSubmitting.value = true;
  try {
    await createRunActionApi(runId, { content });
    message.success('处置措施已添加');
    newActionContent.value = '';
    loadActions();
  } finally {
    newActionSubmitting.value = false;
  }
}

function startEditAction(a: DiagnosisApi.ActionItem): void {
  editingActionId.value = a.id;
  editingContent.value = a.content;
}

function cancelEditAction(): void {
  editingActionId.value = '';
  editingContent.value = '';
}

async function saveEditAction(): Promise<void> {
  const content = editingContent.value.trim();
  if (!editingActionId.value || !content) {
    if (!content) message.warning('处置措施内容不能为空');
    return;
  }
  try {
    await updateRunActionApi(editingActionId.value, { content });
    message.success('处置措施已更新');
    cancelEditAction();
    loadActions();
  } catch {
    /* 错误提示由请求拦截器统一弹出 */
  }
}

async function removeAction(a: DiagnosisApi.ActionItem): Promise<void> {
  Modal.confirm({
    title: '删除处置建议',
    content: `确定删除该${a.source === 'SYSTEM' ? '系统建议' : '人工新增措施'}吗？${
      a.source === 'SYSTEM' ? '（删除后不会自动重建，除非重新提交复核）' : ''
    }`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteRunActionApi(a.id);
        message.success('已删除');
        loadActions();
      } catch {
        /* 错误提示由请求拦截器统一弹出 */
      }
    },
  });
}

// ===== Tabs =====
const activeTab = ref('conclusion');

// ===== 拖动 + 调整宽高 =====
/** 默认 860：KPI 单行（评分+等级+6率 ≈795px）+ body padding 32px */
const modalW = ref(860);
/** 高度按视口自适应（尽量完整显示 Tab 内容）：视口 - 标题栏/页边余量，clamp [520, 880] */
const bodyH = ref(Math.min(Math.max(window.innerHeight - 200, 520), 880));
const MIN_W = 720;
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
    // 复核表单回显：已复核预填上次结论（可改判）；未复核默认勾选 AI 主分类
    reviewForm.value.reviewResults = props.item.reviewResults?.length
      ? [...props.item.reviewResults]!
      : (props.item.primaryCategory
        ? [props.item.primaryCategory]
        : []);
    reviewForm.value.reviewComment = '';
    actionItems.value = [];
    newActionContent.value = '';
    load(props.item);
    loadActions();
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
      <!-- ===== 顶部三信息行（均单行 nowrap） ===== -->
      <div class="diag-detail-top">
        <!-- ① 回路基本信息 -->
        <div class="diag-detail-card">
          <div class="diag-detail-card__title">回路基本信息</div>
          <div class="diag-info-row">
            <span class="diag-info__item">
              <span class="diag-info__k">位号</span>
              <span class="diag-info__v font-semibold">{{
                item.loopTagName
              }}</span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">名称</span>
              <span
                class="diag-info__v diag-ellipsis"
                :title="item.loopDescription ?? ''"
              >
                {{ item.loopDescription || '—' }}
              </span>
            </span>
            <span class="diag-info__item">
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
            <span class="diag-info__item">
              <span class="diag-info__k">量程</span>
              <span class="diag-info__v tabular-nums">{{ rangeText }}</span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">装置.单元</span>
              <span class="diag-info__v diag-ellipsis" :title="unitPath">
                {{ unitPath }}
              </span>
            </span>
          </div>
        </div>

        <!-- ② 性能评估指标（单行；评估窗口在标题右侧） -->
        <div class="diag-detail-card">
          <div class="diag-detail-card__title">
            <span>性能评估指标</span>
            <span
              v-if="!kpiLoading && kpi"
              class="diag-detail-card__extra tabular-nums"
            >
              评估窗口 {{ fmtLocal(kpi.tsStart) }}~{{ fmtLocal(kpi.tsEnd) }}
            </span>
          </div>
          <Skeleton
            v-if="kpiLoading"
            :paragraph="{ rows: 1 }"
            active
            class="diag-kpi-skeleton"
          />
          <div v-else-if="kpi" class="diag-info-row">
            <span class="diag-info__item">
              <span class="diag-info__k">综合评分</span>
              <span
                class="diag-info__v font-semibold tabular-nums"
                :style="{ color: grade?.color }"
              >
                {{ kpi.score != null ? kpi.score.toFixed(1) : '—' }}
              </span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">等级</span>
              <span class="diag-info__v" :style="{ color: grade?.color }">
                {{ grade?.label ?? '—' }}
              </span>
            </span>
            <span v-for="r in kpiRates" :key="r.label" class="diag-info__item">
              <span class="diag-info__k">{{ r.label }}</span>
              <span class="diag-info__v tabular-nums">{{ r.value }}</span>
            </span>
          </div>
          <div v-else class="diag-detail-card__empty">
            暂无性能评估数据（尚未生成 KPI 快照）
          </div>
        </div>

        <!-- ③ 诊断基本信息 -->
        <div class="diag-detail-card">
          <div class="diag-detail-card__title">诊断基本信息</div>
          <div class="diag-info-row">
            <span class="diag-info__item">
              <span class="diag-info__k">诊断次序</span>
              <span class="diag-info__v">
                {{ item.runCount ? `第 ${item.runCount} 次` : '未诊断' }}
              </span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">诊断时间</span>
              <span class="diag-info__v tabular-nums">
                {{ fmtLocal(item.lastDiagnosedAt) }}
              </span>
            </span>
            <span class="diag-info__item">
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
            <span class="diag-info__item diag-info__item--end">
              <span class="diag-info__k">时间窗口</span>
              <span class="diag-info__v tabular-nums">
                {{ fmtLocal(twStart) }}~{{ fmtLocal(twEnd) }}
              </span>
            </span>
          </div>
        </div>
      </div>

      <!-- ===== 三 Tab：诊断结论 / 诊断证据 / 处置建议 ===== -->
      <Tabs
        v-model:active-key="activeTab"
        class="diag-detail-tabs"
        size="small"
      >
        <!-- Tab1 诊断结论：上=AI 结论；下=人工复核 -->
        <TabPane key="conclusion" tab="诊断结论">
          <Empty v-if="!item.runId" class="py-4" description="该回路尚未诊断" />
          <template v-else>
            <Spin v-if="detailLoading" class="block py-4" />
            <template v-else>
              <DiagnosisResultPanel
                v-if="runDetail"
                :detail="runDetail"
                section="conclusion"
              />
              <Empty v-else class="py-4" description="诊断详情加载失败" />
            </template>

            <!-- 人工复核（复核时间/复核人自动填入） -->
            <div class="diag-review">
              <div class="diag-review__title">
                人工复核
                <Tag
                  v-if="item.reviewStatus === 'REVIEWED'"
                  color="green"
                  style="margin-left: 6px"
                >
                  已复核
                </Tag>
                <Tag v-else color="orange" style="margin-left: 6px">待复核</Tag>
              </div>
              <Form layout="vertical" class="diag-review__form">
                <FormItem label="复核结论（多选）" required>
                  <Select
                    v-model:value="reviewForm.reviewResults"
                    :options="CATEGORY_OPTIONS"
                    mode="multiple"
                    placeholder="选择人工确认的问题分类（可多选）"
                    :max-tag-count="4"
                  />
                </FormItem>
                <FormItem label="复核意见">
                  <Textarea
                    v-model:value="reviewForm.reviewComment"
                    :maxlength="500"
                    placeholder="记录现场核实情况、处理安排等（可选，≤500 字）"
                    :rows="2"
                    show-count
                  />
                </FormItem>
                <div class="diag-review__meta">
                  <div class="diag-review__field">
                    <span class="diag-review__k">复核时间</span>
                    <Input :value="reviewTimeText" readonly size="small" />
                  </div>
                  <div class="diag-review__field">
                    <span class="diag-review__k">复核人</span>
                    <Input :value="reviewerText" readonly size="small" />
                  </div>
                  <Button
                    :loading="reviewSubmitting"
                    type="primary"
                    @click="submitReview"
                  >
                    {{
                      item.reviewStatus === 'REVIEWED' ? '更新复核' : '提交复核'
                    }}
                  </Button>
                </div>
              </Form>
            </div>
          </template>
        </TabPane>

        <!-- Tab2 诊断证据：数据质量/波形快照/特征值（默认全展开） -->
        <TabPane key="evidence" tab="诊断证据">
          <Empty v-if="!item.runId" class="py-4" description="该回路尚未诊断" />
          <Spin v-else-if="detailLoading" class="block py-4" />
          <DiagnosisResultPanel
            v-else-if="runDetail"
            :detail="runDetail"
            section="evidence"
          />
          <Empty v-else class="py-4" description="诊断详情加载失败" />
        </TabPane>

        <!-- Tab3 处置建议：系统带出 + 人工新增 -->
        <TabPane key="advice" tab="处置建议">
          <Empty v-if="!item.runId" class="py-4" description="该回路尚未诊断" />
          <template v-else>
            <Spin v-if="actionsLoading" class="block py-4" />
            <template v-else>
              <div v-if="actionItems.length > 0" class="diag-action-list">
                <div
                  v-for="a in actionItems"
                  :key="a.id"
                  class="diag-action-item"
                >
                  <!-- 行内编辑态（仅 MANUAL） -->
                  <template v-if="editingActionId === a.id">
                    <Textarea
                      v-model:value="editingContent"
                      :maxlength="500"
                      :rows="2"
                      auto-focus
                    />
                    <div class="mt-1 flex justify-end gap-2">
                      <Button size="small" @click="cancelEditAction"
                        >取消</Button
                      >
                      <Button
                        size="small"
                        type="primary"
                        @click="saveEditAction"
                      >
                        保存
                      </Button>
                    </div>
                  </template>
                  <!-- 展示态 -->
                  <template v-else>
                    <div class="flex items-start gap-2">
                      <Tag
                        :color="a.source === 'SYSTEM' ? 'blue' : 'green'"
                        class="mt-0.5 shrink-0"
                      >
                        {{
                          a.source === 'SYSTEM'
                            ? `系统建议 R${a.priority}`
                            : '人工新增'
                        }}
                      </Tag>
                      <!-- 处置建议状态 tag（PENDING 之外的状态在诊断侧可见，§8.4；
                           v2.0 建议状态机：ACCEPTED/CONVERTED/REJECTED/IGNORED） -->
                      <Tag
                        v-if="a.status && a.status !== 'PENDING'"
                        :color="
                          SUGGESTION_STATUS_COLOR[
                            a.status as keyof typeof SUGGESTION_STATUS_COLOR
                          ] ?? 'default'
                        "
                        class="mt-0.5 shrink-0"
                      >
                        {{
                          SUGGESTION_STATUS_TEXT[
                            a.status as keyof typeof SUGGESTION_STATUS_TEXT
                          ] ?? a.status
                        }}
                      </Tag>
                      <!-- TODO(v2.0-H2): CONVERTED 行额外显示 convertedOrderNo
                           （诊断侧 actions 接口需回链该字段后启用） -->
                      <div class="min-w-0 flex-1">
                        <div>{{ a.content }}</div>
                        <div class="mt-0.5 text-xs text-neutral-500">
                          依据：{{ a.basis || '—' }} · 建议人
                          {{ a.suggestedBy }} ·
                          {{ fmtLocal(a.suggestedAt) }}
                        </div>
                      </div>
                      <div class="flex shrink-0 gap-1" @click.stop>
                        <Button
                          v-if="a.category === 'TUNING' && moduleEnabled('tuning')"
                          size="small"
                          type="link"
                          @click="gotoTuning(a.loopId)"
                        >
                          去整定
                        </Button>
                        <Button
                          v-if="moduleEnabled('handling')"
                          size="small"
                          type="link"
                          @click="gotoHandling(a.id)"
                        >
                          去处置
                        </Button>
                        <Button
                          v-if="a.source === 'MANUAL'"
                          size="small"
                          type="link"
                          @click="startEditAction(a)"
                        >
                          编辑
                        </Button>
                        <Button
                          danger
                          size="small"
                          type="link"
                          @click="removeAction(a)"
                        >
                          删除
                        </Button>
                      </div>
                    </div>
                  </template>
                </div>
              </div>
              <Empty v-else class="py-4" description="暂无处置建议" />

              <!-- 新增处置措施（建议人/建议时间由系统自动带入） -->
              <div class="diag-action-new">
                <div class="diag-review__title">新增处置措施</div>
                <Textarea
                  v-model:value="newActionContent"
                  :maxlength="500"
                  :rows="2"
                  placeholder="输入处置措施（建议人与建议时间将自动记录为当前登录用户与系统时间）"
                />
                <div class="diag-action-new__footer">
                  <span class="text-xs text-neutral-400">
                    建议人 {{ currentUserName }} ·
                    {{ dayjs().format('YYYY-MM-DD HH:mm') }}
                  </span>
                  <Button
                    :loading="newActionSubmitting"
                    size="small"
                    type="primary"
                    @click="submitNewAction"
                  >
                    添加
                  </Button>
                </div>
              </div>
            </template>
          </template>
        </TabPane>
      </Tabs>

      <!-- 右下角宽高手柄 -->
      <div class="diag-detail-resize" @mousedown="onResizeStart"></div>
    </div>
  </Modal>
</template>

<style>
/* 弹窗 DOM 挂载于 body（wrapClassName 定位），需全局样式 */
.diag-detail-modal .ant-modal-header {
  cursor: move;
  user-select: none;
}

/* 压缩 body 内边距，为单行信息行留宽 */
.diag-detail-modal .ant-modal-body {
  padding: 12px 16px;
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
  flex-shrink: 0;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.diag-detail-modal .diag-detail-card {
  background: hsl(var(--accent) / 20%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-detail-card__title {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
  padding: 3px 10px 0;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-detail-card__extra {
  font-size: 11px;
  font-weight: 400;
  white-space: nowrap;
}

.diag-detail-modal .diag-detail-card__empty {
  padding: 4px 10px 6px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-kpi-skeleton {
  padding: 4px 10px 6px;
}

/* 信息行：标签+值紧凑单行（不换行；超宽横向滚动兜底） */
.diag-detail-modal .diag-info-row {
  display: flex;
  gap: 2px 12px;
  align-items: baseline;
  padding: 4px 10px 6px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}

.diag-detail-modal .diag-info__item {
  display: inline-flex;
  flex-shrink: 0;
  gap: 3px;
  align-items: baseline;
}

.diag-detail-modal .diag-info__item--end {
  margin-left: auto;
}

.diag-detail-modal .diag-info__k {
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 55%);
}

.diag-detail-modal .diag-info__v {
  font-weight: 500;
}

/* 超长文本（名称/装置路径）截断省略，不换行 */
.diag-detail-modal .diag-ellipsis {
  display: inline-block;
  max-width: 176px;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
  white-space: nowrap;
}

/* Tabs 占满剩余高度，tab 内容区滚动 */
.diag-detail-modal .diag-detail-tabs {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  font-size: 12px;
}

.diag-detail-modal .diag-detail-tabs > .ant-tabs-content-holder {
  flex: 1;
  min-height: 0;
  padding-right: 2px;
  overflow: auto;
}

/* Tab 内容紧凑（表格/标签/文本统一 12px） */
.diag-detail-modal .diag-detail-tabs .ant-table {
  font-size: 12px;
}

.diag-detail-modal .diag-detail-tabs .ant-table-cell {
  padding: 4px 8px;
  font-size: 12px;
}

.diag-detail-modal .diag-detail-tabs .ant-tag {
  font-size: 11px;
}

/* 人工复核区（诊断结论 Tab 下半） */
.diag-detail-modal .diag-review {
  padding: 10px 12px;
  margin-top: 10px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-review__title {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.diag-detail-modal .diag-review__form .ant-form-item {
  margin-bottom: 8px;
}

.diag-detail-modal .diag-review__form .ant-form-item-label > label {
  font-size: 12px;
}

.diag-detail-modal .diag-review__meta {
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

.diag-detail-modal .diag-review__field {
  display: flex;
  flex: 1;
  gap: 6px;
  align-items: center;
}

.diag-detail-modal .diag-review__k {
  flex-shrink: 0;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

/* 处置建议列表 */
.diag-detail-modal .diag-action-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.diag-detail-modal .diag-action-item {
  padding: 6px 10px;
  font-size: 12px;
  background: hsl(var(--accent) / 20%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-action-new {
  padding: 8px 10px;
  margin-top: 8px;
  background: hsl(var(--card));
  border: 1px dashed hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-action-new__footer {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
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
  background: linear-gradient(135deg, transparent 50%, hsl(var(--border)) 50%);
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
