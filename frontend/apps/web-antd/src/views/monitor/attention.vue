<script lang="ts" setup>
/**
 * 关注队列标杆页面（v1.2：按回路合并分组）
 *
 * 页型 D：队列分诊——回答"今天先干什么"
 * 主表一行=一个"问题回路"，行内展开子项明细
 *
 * 设计规范：docs/设计文档/页面标杆设计/03-关注队列/
 */
import type { TableColumnsType } from 'ant-design-vue';
import type { MenuProps } from 'ant-design-vue';

import type { MonitorApi } from '#/api/monitor';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';

import {
  Badge,
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Dropdown,
  FormItem,
  Input,
  message,
  Modal,
  Pagination,
  Space,
  Spin,
  Table,
  Tag,
  Textarea,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';

import {
  acknowledgeEventApi,
  markFalsePositiveApi,
  resolveEventApi,
} from '#/api/alert';
import { getAttentionListApi } from '#/api/monitor';
import {
  ClpmEmptyState,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import {
  PRIORITY_LABEL,
  PRIORITY_TO_STATUS,
  statusTokenToAntdColor,
} from '#/constants/clpm-ui';
import { formatTime, normalizeUtcTimestamp } from '#/utils/format';

import 'dayjs/locale/zh-cn';

defineOptions({ name: 'MonitorAttention' });
dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const route = useRoute();
const router = useRouter();

/** 返回系统概览（面包屑导航） */
function goBackToOverview() {
  router.push({ path: '/dashboard/workbench' });
}

// ===== 相对时间格式化 =====
function formatRelative(ts: null | string | undefined): string {
  if (!ts) return '-';
  try {
    return dayjs(normalizeUtcTimestamp(ts)).fromNow();
  } catch {
    return formatTime(ts);
  }
}

// ===== 语义映射 =====
const priorityColor = (priority: string) =>
  statusTokenToAntdColor(PRIORITY_TO_STATUS[priority] ?? 'neutral');

const SOURCE_LABEL: Record<MonitorApi.AttentionSource, string> = {
  ALERT: '活跃预警',
  DEGRADATION: '评分恶化',
  DATA_QUALITY: '数据质量',
  FITNESS_ABNORMAL: '适用性异常',
  HANDLING: '处置工单',
};

const SOURCE_COLOR: Record<MonitorApi.AttentionSource, string> = {
  ALERT: 'error',
  DEGRADATION: 'warning',
  DATA_QUALITY: 'default',
  FITNESS_ABNORMAL: 'purple',
  HANDLING: 'processing',
};

const STATUS_LABEL: Record<MonitorApi.AttentionStatus, string> = {
  OPEN: '待处理',
  ACKNOWLEDGED: '已确认',
  SUPPRESSED: '已抑制',
  IN_PROGRESS: '处理中',
};

const STATUS_COLOR: Record<MonitorApi.AttentionStatus, string> = {
  OPEN: 'error',
  ACKNOWLEDGED: 'warning',
  SUPPRESSED: 'default',
  IN_PROGRESS: 'processing',
};

const PRIORITY_ORDER: MonitorApi.AttentionPriority[] = [
  'URGENT',
  'HIGH',
  'MEDIUM',
  'LOW',
];
const SOURCE_ORDER: MonitorApi.AttentionSource[] = [
  'ALERT',
  'DEGRADATION',
  'DATA_QUALITY',
  'FITNESS_ABNORMAL',
  'HANDLING',
];
const SOURCE_SET = new Set<string>(SOURCE_ORDER);

/** P2 IA优化：fitness tag 中文映射（与其他模块共用） */
const ATT_FITNESS_TAG_CN: Record<string, string> = {
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
const attFitnessTagToCn = (t: string) => ATT_FITNESS_TAG_CN[t] ?? t;
/** FITNESS_ABNORMAL Tooltip 文案：等级 + 原因标签 */
function attFitnessTip(
  level: null | string | undefined,
  tags: null | string[] | undefined,
): string {
  const lv = level ?? '';
  const tagText =
    tags && tags.length > 0
      ? tags.map((t) => attFitnessTagToCn(t)).join('、')
      : '适用性异常';
  return `适用性异常（${lv || 'NA'}）：${tagText}`;
}

// ===== 表格密度三档 =====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('monitor-attention');

// ===== 列表状态 =====
const loading = ref(false);
const attentionGroups = ref<MonitorApi.AttentionGroup[]>([]);
const totalGroups = ref(0);
const totalItems = ref(0);
const aggregates = ref<MonitorApi.AttentionAggregates>({
  byPriority: {},
  bySource: {},
  byStatus: {},
  byGroupPriority: {},
  groupCount: 0,
  openCount: 0,
  urgentCount: 0,
  dataQualityCount: 0,
});
const truncated = ref<Record<string, boolean>>({});
const loadedAt = ref('');
const lastRefresh = ref('');

const query = reactive({
  plantNodeId: undefined as string | undefined,
  loopId: undefined as string | undefined,
  source: [] as MonitorApi.AttentionSource[],
  priority: [] as MonitorApi.AttentionPriority[],
  status: [] as MonitorApi.AttentionStatus[],
  keyword: '',
  page: 1,
  pageSize: 20,
});

// 展开行状态
const expandedRowKeys = ref<string[]>([]);

// ===== 详情抽屉 =====
const detailVisible = ref(false);
const currentItem = ref<MonitorApi.AttentionItem | null>(null);

// ===== 处置弹窗 =====
const resolveVisible = ref(false);
const resolveNote = ref('');
const resolvingAttentionId = ref('');
const resolvingEventId = ref('');
const resolveLoading = ref(false);

// ===== 行内动作防重复提交 =====
const actingAttentionId = ref('');

// ===== 主表八列定版（v1.2） =====
const columns: TableColumnsType = [
  {
    title: '序号',
    key: 'index',
    width: 60,
    fixed: 'left',
    align: 'center',
  },
  {
    title: '回路号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 180,
    fixed: 'left',
  },
  {
    title: '装置·单元',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 160,
    ellipsis: true,
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 90,
    align: 'center',
  },
  {
    title: '来源',
    dataIndex: 'sources',
    key: 'sources',
    width: 280,
  },
  {
    title: '摘要',
    dataIndex: 'summary',
    key: 'summary',
    ellipsis: true,
    minWidth: 280,
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 130,
  },
  {
    title: '操作',
    key: 'actions',
    width: 80,
    fixed: 'right',
    align: 'center',
  },
];

// ===== 数据加载 =====
async function loadData() {
  loading.value = true;
  try {
    const res = await getAttentionListApi({
      plantNodeId: query.plantNodeId || undefined,
      loopId: query.loopId || undefined,
      source: query.source.length > 0 ? query.source : undefined,
      priority: query.priority.length > 0 ? query.priority : undefined,
      status: query.status.length > 0 ? query.status : undefined,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });

    attentionGroups.value = res.items;
    totalGroups.value = res.totalGroups;
    totalItems.value = res.totalItems;
    aggregates.value = res.aggregates;

    truncated.value = res.truncated || {};
    loadedAt.value = res.loadedAt || new Date().toISOString();
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN', {
      hour12: false,
    });
  } catch (error: any) {
    message.error(error?.message ?? '加载关注队列失败');
    attentionGroups.value = [];
    totalGroups.value = 0;
    totalItems.value = 0;
  } finally {
    loading.value = false;
  }
}

// ===== 筛选切换 =====
function handleFilterChange() {
  query.page = 1;
  expandedRowKeys.value = [];
  loadData();
}

function toggleArrayFilter<T>(arr: T[], val: T): T[] {
  return arr.includes(val) ? arr.filter((v) => v !== val) : [...arr, val];
}

function toggleSource(s: MonitorApi.AttentionSource) {
  query.source = toggleArrayFilter(query.source, s);
  handleFilterChange();
}

function togglePriority(p: MonitorApi.AttentionPriority) {
  query.priority = toggleArrayFilter(query.priority, p);
  handleFilterChange();
}

function handleKeywordSearch() {
  query.page = 1;
  expandedRowKeys.value = [];
  loadData();
}

function handleResetFilters() {
  query.source = [];
  query.priority = [];
  query.status = [];
  query.keyword = '';
  query.loopId = undefined;
  query.page = 1;
  expandedRowKeys.value = [];
  loadData();
}

// ===== 分页 =====
function handlePageChange(page: number, pageSize: number) {
  query.page = page;
  query.pageSize = pageSize;
  expandedRowKeys.value = [];
  loadData();
}

// ===== 展开/折叠行 =====
function toggleExpand(group: MonitorApi.AttentionGroup) {
  const idx = expandedRowKeys.value.indexOf(group.groupId);
  if (idx === -1) {
    expandedRowKeys.value.push(group.groupId);
  } else {
    expandedRowKeys.value.splice(idx, 1);
  }
}

// ===== 执行跳转动作 =====
function executeNavAction(action: MonitorApi.AttentionAction) {
  if (!action.enabled) {
    if (action.disabledReason) {
      message.warning(action.disabledReason);
    }
    return;
  }
  if (action.target) {
    router.push({
      path: action.target.route,
      query: { ...action.target.query, from: '/monitor/attention' },
    });
  }
}

// ===== 详情抽屉（子项） =====
function openChildDetail(item: MonitorApi.AttentionItem) {
  currentItem.value = item;
  detailVisible.value = true;
}

// ===== 子项动作执行 =====
async function executeChildAction(
  item: MonitorApi.AttentionItem,
  action: MonitorApi.AttentionAction,
) {
  if (!action.enabled) {
    if (action.disabledReason) {
      message.warning(action.disabledReason);
    }
    return;
  }

  if (action.target) {
    router.push({
      path: action.target.route,
      query: { ...action.target.query, from: '/monitor/attention' },
    });
    return;
  }

  if (item.source === 'ALERT' && item.eventId) {
    if (actingAttentionId.value) return;
    actingAttentionId.value = item.attentionId;
    try {
      switch (action.type) {
        case 'ACKNOWLEDGE': {
          await acknowledgeEventApi(item.eventId);
          message.success('事件已确认');
          break;
        }
        case 'MARK_FALSE_POSITIVE': {
          await markFalsePositiveApi(item.eventId, true);
          message.success('已标记误报');
          break;
        }
        default: {
          break;
        }
      }
      await loadData();
      if (currentItem.value?.attentionId === item.attentionId) {
        detailVisible.value = false;
      }
    } catch (error: any) {
      message.error(error?.message ?? '操作失败');
    } finally {
      actingAttentionId.value = '';
    }
  }
}

function openResolveModal(item: MonitorApi.AttentionItem) {
  if (!item.eventId) return;
  resolvingAttentionId.value = item.attentionId;
  resolvingEventId.value = item.eventId;
  resolveNote.value = '';
  resolveVisible.value = true;
}

async function handleResolveSubmit() {
  if (!resolveNote.value.trim()) {
    message.warning('请填写处置说明');
    return;
  }
  if (resolveLoading.value) return;
  resolveLoading.value = true;
  try {
    await resolveEventApi(resolvingEventId.value, resolveNote.value);
    message.success('事件已处置');
    resolveVisible.value = false;
    await loadData();
    if (currentItem.value?.attentionId === resolvingAttentionId.value) {
      detailVisible.value = false;
    }
  } catch (error: any) {
    message.error(error?.message ?? '处置失败');
  } finally {
    resolveLoading.value = false;
  }
}

// ===== 组行类名（紧急红底/高优先级左条） =====
function getRowClassName(record: MonitorApi.AttentionGroup) {
  if (record.priority === 'URGENT') return 'row-urgent';
  if (record.priority === 'HIGH') return 'row-high';
  return '';
}

// ===== 子项更多菜单 =====
function buildChildMenu(item: MonitorApi.AttentionItem): MenuProps {
  const items: MenuProps['items'] = item.actions
    .filter(
      (a) =>
        a.type !== 'OPEN_WORKBENCH' &&
        a.type !== 'VIEW_DETAIL' &&
        a.type !== 'BACK_TO_OVERVIEW',
    )
    .map((a) => ({
      key: a.type,
      label: a.label + (a.disabledReason ? `（${a.disabledReason}）` : ''),
      disabled: !a.enabled,
      onClick: () => {
        if (a.type === 'RESOLVE') {
          openResolveModal(item);
        } else {
          executeChildAction(item, a);
        }
      },
    }));
  return { items };
}

// ===== 工具栏 =====
function handleHelp() {
  showPageHelp({
    title: '关注队列 帮助',
    content: `
      <p><b>五来源定义</b>：活跃预警（ACTIVE/ACKNOWLEDGED/SUPPRESSED 预警事件）、评分恶化（日降≥2分）、数据质量（完整性告警或可信度 D/E）、适用性异常（整定适用性等级 L0/L1/L2 及原因标签）、处置工单（重开/超期等在途工单待办）。</p>
      <p><b>优先级规则</b>：紧急=CRITICAL 活跃预警；高=ERROR 预警/完整性 CRITICAL/日降≥10分；中=WARN/完整性 WARNING/日降5-10分；低=INFO/日降2-5分/可信度 D/E。</p>
      <p><b>排序规则</b>：优先级从高到低 → 同级：未确认 → 超期 → 处理中 → 已确认 → 已抑制 → 时间倒序。</p>
      <p><b>合并规则</b>：同一回路的多个关注项合并为一行；组优先级=组内最高；展开可看子项明细。</p>
      <p>点击「进入工作台」可跳转至回路工作台查看详情并处置，携带上下文直接定位相关证据。</p>
    `,
  });
}

// ===== 深链接 =====
function applyUrlContext() {
  const sourceParam = route.query.source as string | undefined;
  const eventIdParam = route.query.eventId as string | undefined;
  const loopIdParam = route.query.loopId as string | undefined;
  const plantNodeIdParam = route.query.plantNodeId as string | undefined;
  if (sourceParam && SOURCE_SET.has(sourceParam)) {
    query.source = [sourceParam as MonitorApi.AttentionSource];
  }
  if (loopIdParam) {
    query.loopId = loopIdParam;
  }
  if (plantNodeIdParam) {
    query.plantNodeId = plantNodeIdParam;
  }
  return eventIdParam;
}

function tryOpenDetailByEventId(eventId: string) {
  for (const g of attentionGroups.value) {
    const child = g.children.find((c) => c.eventId === eventId);
    if (child) {
      expandedRowKeys.value = [g.groupId];
      setTimeout(() => openChildDetail(child), 100);
      return;
    }
  }
}

// ===== 截断提示 =====
const hasTruncation = computed(() =>
  Object.values(truncated.value).some(Boolean),
);
const truncationMessage = computed(() => {
  const sources = Object.entries(truncated.value)
    .filter(([, v]) => v)
    .map(([k]) => SOURCE_LABEL[k as MonitorApi.AttentionSource] || k);
  if (sources.length === 0) return '';
  return `已达单来源 500 条聚合上限（${sources.join('、')}），请细化筛选`;
});

// ===== 是否有筛选（用于空态区分） =====
const hasFilters = computed(
  () =>
    query.source.length > 0 ||
    query.priority.length > 0 ||
    query.status.length > 0 ||
    !!query.keyword ||
    !!query.loopId ||
    !!query.plantNodeId,
);

// ===== 当前页项数 =====
const currentPageItemCount = computed(() =>
  attentionGroups.value.reduce((s, g) => s + g.children.length, 0),
);

// ===== 生命周期 =====
onMounted(async () => {
  const eventId = applyUrlContext();
  await loadData();
  if (eventId) {
    tryOpenDetailByEventId(eventId);
  }
});

watch(
  () => route.query,
  (q) => {
    const newSource = q.source as string | undefined;
    const newEventId = q.eventId as string | undefined;
    const newLoopId = q.loopId as string | undefined;
    const newPlantNodeId = q.plantNodeId as string | undefined;
    let needReload = false;
    if (
      newSource &&
      SOURCE_SET.has(newSource) &&
      !query.source.includes(newSource as MonitorApi.AttentionSource)
    ) {
      query.source = [newSource as MonitorApi.AttentionSource];
      query.page = 1;
      needReload = true;
    }
    if ((newLoopId ?? undefined) !== (query.loopId ?? undefined)) {
      query.loopId = newLoopId;
      query.page = 1;
      needReload = true;
    }
    if ((newPlantNodeId ?? undefined) !== (query.plantNodeId ?? undefined)) {
      query.plantNodeId = newPlantNodeId;
      query.page = 1;
      needReload = true;
    }
    if (needReload) {
      loadData().then(() => {
        if (newEventId) tryOpenDetailByEventId(newEventId);
      });
    } else if (newEventId) {
      tryOpenDetailByEventId(newEventId);
    }
  },
);
</script>

<template>
  <Page>
    <ClpmPageToolbar title="关注队列" :loading="loading">
      <!-- 从系统概览跳转过来时显示返回面包屑 -->
      <template #context v-if="route.query.from === 'overview'">
        <button
          class="flex items-center gap-1 rounded border border-transparent px-2 py-0.5 text-xs text-blue-600 hover:border-blue-200 hover:bg-blue-50"
          @click="goBackToOverview"
        >
          <span>←</span>
          <span>系统概览</span>
        </button>
      </template>
      <template #actions>
        <ClpmToolbarButton
          icon="lucide:refresh-cw"
          label="刷新"
          :loading="loading"
          tooltip="刷新关注队列数据"
          @click="loadData"
        />
        <ClpmToolbarButton
          icon="lucide:help-circle"
          label="帮助"
          tooltip="查看关注队列使用说明"
          @click="handleHelp"
        />
        <ClpmToolbarButton
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
      </template>
    </ClpmPageToolbar>

    <div class="h-full flex flex-col overflow-hidden bg-[var(--clr-surface)]">
      <Spin :spinning="loading" class="flex-1 flex flex-col min-h-0">
        <div class="flex flex-col h-full p-3 gap-3 overflow-auto">
          <!-- 截断提示条 -->
          <div
            v-if="hasTruncation"
            class="flex items-center gap-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700"
          >
            <IconifyIcon icon="lucide:alert-triangle" :size="16" />
            <span>{{ truncationMessage }}</span>
          </div>

          <!-- R2 摘要条 -->
          <div
            class="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-gray-100 bg-white px-4 py-3"
          >
            <div class="flex items-baseline gap-2">
              <span class="text-2xl font-semibold tabular-nums text-red-600">
                {{ aggregates.openCount || 0 }}
              </span>
              <span class="text-sm text-gray-500">待处理</span>
            </div>
            <div class="h-5 w-px bg-gray-200"></div>
            <div class="flex items-baseline gap-2">
              <span class="text-2xl font-semibold tabular-nums">
                {{ aggregates.groupCount || 0 }}
              </span>
              <span class="text-sm text-gray-500">问题回路</span>
            </div>
            <div
              v-if="aggregates.urgentCount"
              class="h-5 w-px bg-gray-200"
            ></div>
            <div
              v-if="aggregates.urgentCount"
              class="flex items-baseline gap-2"
            >
              <span class="text-2xl font-semibold tabular-nums text-red-600">
                {{ aggregates.urgentCount }}
              </span>
              <span class="text-sm text-gray-500">紧急</span>
            </div>
            <div
              v-if="aggregates.dataQualityCount"
              class="h-5 w-px bg-gray-200"
            ></div>
            <div
              v-if="aggregates.dataQualityCount"
              class="flex items-baseline gap-2"
            >
              <span class="text-2xl font-semibold tabular-nums text-gray-600">
                {{ aggregates.dataQualityCount }}
              </span>
              <span class="text-sm text-gray-500">数据质量</span>
            </div>
            <div class="ml-auto flex items-center gap-2 text-xs text-gray-400">
              <IconifyIcon icon="lucide:clock" :size="12" />
              <span>聚合于 {{ formatTime(loadedAt) }}</span>
            </div>
          </div>

          <!-- R2.5 优先级速览卡 + 来源 chips -->
          <div class="flex flex-wrap items-center gap-3">
            <!-- 优先级速览卡（组口径） -->
            <div class="flex items-center gap-1.5">
              <button
                class="rounded-md border px-3 py-1.5 text-sm transition-colors"
                :class="
                  query.priority.length === 0
                    ? 'border-blue-400 bg-blue-50 text-blue-700'
                    : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                "
                @click="
                  () => {
                    query.priority = [];
                    handleFilterChange();
                  }
                "
              >
                全部
                <span class="ml-1 font-semibold tabular-nums">
                  {{ aggregates.groupCount || 0 }}
                </span>
                <span class="text-xs text-gray-400 ml-0.5">
                  ({{ totalItems }}项)
                </span>
              </button>
              <button
                v-for="p in PRIORITY_ORDER"
                :key="p"
                class="rounded-md border px-3 py-1.5 text-sm transition-colors"
                :class="
                  query.priority.includes(p)
                    ? 'border-current'
                    : 'border-gray-200 bg-white hover:border-gray-300'
                "
                :style="
                  query.priority.includes(p)
                    ? `border-color: var(--ant-${priorityColor(p)}-color); background: var(--ant-${priorityColor(p)}-color-1); color: var(--ant-${priorityColor(p)}-color);`
                    : ''
                "
                @click="togglePriority(p)"
              >
                {{ PRIORITY_LABEL[p] }}
                <span class="ml-1 font-semibold tabular-nums">
                  {{ aggregates.byGroupPriority?.[p] || 0 }}
                </span>
              </button>
            </div>

            <!-- 来源 chips（项口径） -->
            <div class="ml-auto flex items-center gap-1.5">
              <Tag
                v-for="s in SOURCE_ORDER"
                :key="s"
                :color="query.source.includes(s) ? SOURCE_COLOR[s] : 'default'"
                class="cursor-pointer !mb-0"
                style="margin: 0; font-size: 12px"
                @click="toggleSource(s)"
              >
                {{ SOURCE_LABEL[s] }}
                <span class="ml-1 opacity-70 tabular-nums">
                  {{ aggregates.bySource[s] || 0 }}
                </span>
              </Tag>
            </div>
          </div>

          <!-- R3 筛选工具条 -->
          <div class="clpm-filter-bar">
            <FormItem label="搜索" class="!mb-0">
              <Input
                v-model:value="query.keyword"
                allow-clear
                placeholder="位号 / 标题"
                style="width: 200px"
                @press-enter="handleKeywordSearch"
              />
            </FormItem>

            <Space class="!ml-auto">
              <Button type="primary" @click="handleKeywordSearch">查询</Button>
              <Button @click="handleResetFilters">重置</Button>
            </Space>
          </div>

          <!-- R4 分诊主表（flex-1 占满剩余空间） -->
          <Card
            :bordered="false"
            size="small"
            class="flex-1 flex flex-col min-h-0 shadow-sm"
          >
            <Table
              :columns="columns"
              :data-source="attentionGroups"
              :loading="loading"
              :pagination="false"
              :size="tableSize"
              :scroll="{ x: 1360, y: 'calc(100vh - 420px)' }"
              :row-key="(record: MonitorApi.AttentionGroup) => record.groupId"
              :expanded-row-keys="expandedRowKeys"
              :expand-icon-column-index="-1"
              :row-class-name="getRowClassName"
              class="flex-1"
              @expanded-rows-change="
                (keys: (string | number)[]) =>
                  (expandedRowKeys = keys as string[])
              "
            >
              <template #bodyCell="{ column, record, index }">
                <template v-if="column.key === 'index'">
                  <span class="tabular-nums text-gray-500">
                    {{ (query.page - 1) * query.pageSize + index + 1 }}
                  </span>
                </template>

                <template v-else-if="column.key === 'tagName'">
                  <div class="flex items-center gap-2">
                    <Tag
                      :color="priorityColor(record.priority)"
                      class="!mr-0 !px-1.5"
                      style="font-size: 11px; line-height: 16px"
                    >
                      {{ record.priorityLabel }}
                    </Tag>
                    <span class="font-mono font-semibold text-gray-800">
                      {{ record.tagName }}
                    </span>
                  </div>
                </template>

                <template v-else-if="column.key === 'unitName'">
                  <span class="text-gray-600">{{
                    record.unitName || '-'
                  }}</span>
                </template>

                <template v-else-if="column.key === 'status'">
                  <Tag
                    :color="
                      STATUS_COLOR[record.status as keyof typeof STATUS_COLOR]
                    "
                  >
                    {{
                      STATUS_LABEL[record.status as keyof typeof STATUS_LABEL]
                    }}
                  </Tag>
                </template>

                <template v-else-if="column.key === 'sources'">
                  <Space :size="4">
                    <template
                      v-for="s in record.sources.slice(0, 3)"
                      :key="s"
                    >
                      <Tooltip
                        v-if="s === 'FITNESS_ABNORMAL'"
                        :title="
                          attFitnessTip(
                            (record as MonitorApi.AttentionGroup)
                              .fitnessLevel,
                            (record as MonitorApi.AttentionGroup)
                              .fitnessTags,
                          )
                        "
                        placement="top"
                      >
                        <Tag
                          :color="
                            SOURCE_COLOR[
                              s as keyof typeof SOURCE_COLOR
                            ]
                          "
                          style="margin: 0; font-size: 12px"
                        >
                          {{
                            SOURCE_LABEL[
                              s as keyof typeof SOURCE_LABEL
                            ]
                          }}
                        </Tag>
                      </Tooltip>
                      <Tag
                        v-else
                        :color="
                          SOURCE_COLOR[
                            s as keyof typeof SOURCE_COLOR
                          ]
                        "
                        style="margin: 0; font-size: 12px"
                      >
                        {{
                          SOURCE_LABEL[
                            s as keyof typeof SOURCE_LABEL
                          ]
                        }}
                      </Tag>
                    </template>
                    <Tag
                      v-if="record.sources.length > 3"
                      style="margin: 0; font-size: 12px"
                    >
                      +{{ record.sources.length - 3 }}
                    </Tag>
                    <Badge
                      v-if="record.itemCount > 1"
                      :count="record.itemCount"
                      size="small"
                      :style="{ background: '#666' }"
                    />
                  </Space>
                </template>

                <template v-else-if="column.key === 'summary'">
                  <Tooltip :title="record.summary">
                    <span>{{ record.summary }}</span>
                  </Tooltip>
                </template>

                <template v-else-if="column.key === 'updatedAt'">
                  <div class="flex items-center gap-1">
                    <Tooltip :title="formatTime(record.updatedAt)">
                      <span :class="{ 'text-red-600': record.isOverdue }">
                        {{ formatRelative(record.updatedAt) }}
                      </span>
                    </Tooltip>
                    <IconifyIcon
                      v-if="record.isOverdue"
                      icon="lucide:alert-circle"
                      :size="14"
                      class="text-red-500"
                    />
                  </div>
                </template>

                <template v-else-if="column.key === 'actions'">
                  <Space :size="0">
                    <Tooltip
                      :title="
                        expandedRowKeys.includes(record.groupId)
                          ? '收起子项'
                          : '展开子项'
                      "
                    >
                      <Button
                        type="text"
                        size="small"
                        class="!px-1"
                        @click="
                          toggleExpand(record as MonitorApi.AttentionGroup)
                        "
                      >
                        <IconifyIcon
                          :icon="
                            expandedRowKeys.includes(record.groupId)
                              ? 'lucide:chevron-up'
                              : 'lucide:chevron-down'
                          "
                          :size="16"
                        />
                      </Button>
                    </Tooltip>
                    <Tooltip title="进入回路工作台">
                      <Button
                        type="text"
                        size="small"
                        class="!px-1"
                        :disabled="!record.primaryAction?.enabled"
                        @click="executeNavAction(record.primaryAction)"
                      >
                        <IconifyIcon icon="lucide:external-link" :size="16" />
                      </Button>
                    </Tooltip>
                  </Space>
                </template>
              </template>

              <!-- 展开子项明细 -->
              <template #expandedRowRender="{ record }">
                <div class="py-2 pl-8 pr-4 bg-gray-50/50">
                  <div class="mb-2 text-xs font-medium text-gray-500">
                    {{ record.tagName }} 的 {{ record.itemCount }} 个关注项：
                  </div>
                  <div class="space-y-1">
                    <div
                      v-for="child in record.children"
                      :key="child.attentionId"
                      class="flex items-center gap-3 rounded border border-gray-100 bg-white px-3 py-2 text-sm"
                    >
                      <Tooltip
                        v-if="child.source === 'FITNESS_ABNORMAL'"
                        :title="
                          attFitnessTip(child.fitnessLevel, child.fitnessTags)
                        "
                        placement="top"
                      >
                        <Tag
                          :color="
                            SOURCE_COLOR[
                              child.source as keyof typeof SOURCE_COLOR
                            ]
                          "
                          class="!mr-0"
                          style="font-size: 11px"
                        >
                          {{
                            SOURCE_LABEL[
                              child.source as keyof typeof SOURCE_LABEL
                            ]
                          }}
                        </Tag>
                      </Tooltip>
                      <Tag
                        v-else
                        :color="
                          SOURCE_COLOR[
                            child.source as keyof typeof SOURCE_COLOR
                          ]
                        "
                        class="!mr-0"
                        style="font-size: 11px"
                      >
                        {{
                          SOURCE_LABEL[
                            child.source as keyof typeof SOURCE_LABEL
                          ]
                        }}
                      </Tag>
                      <Tag
                        :color="priorityColor(child.priority)"
                        class="!mr-0"
                        style="font-size: 11px"
                      >
                        {{
                          PRIORITY_LABEL[
                            child.priority as keyof typeof PRIORITY_LABEL
                          ]
                        }}
                      </Tag>
                      <Tag
                        :color="
                          STATUS_COLOR[
                            child.status as keyof typeof STATUS_COLOR
                          ]
                        "
                        class="!mr-0"
                        style="font-size: 11px"
                      >
                        {{
                          STATUS_LABEL[
                            child.status as keyof typeof STATUS_LABEL
                          ]
                        }}
                      </Tag>
                      <Tooltip :title="child.summary">
                        <span class="flex-1 truncate text-gray-700">
                          {{ child.summary }}
                        </span>
                      </Tooltip>
                      <div v-if="child.rankReasons?.length" class="flex gap-1">
                        <Tag
                          v-for="(r, i) in child.rankReasons.slice(0, 2)"
                          :key="i"
                          color="default"
                          class="!mr-0"
                          style="font-size: 11px"
                        >
                          {{ r }}
                        </Tag>
                        <Tag
                          v-if="child.rankReasons.length > 2"
                          color="default"
                          class="!mr-0"
                          style="font-size: 11px"
                        >
                          +{{ child.rankReasons.length - 2 }}
                        </Tag>
                      </div>
                      <Tooltip
                        :title="formatTime(child.updatedAt || child.occurredAt)"
                      >
                        <span
                          class="text-gray-400 whitespace-nowrap tabular-nums"
                        >
                          {{
                            formatRelative(child.updatedAt || child.occurredAt)
                          }}
                        </span>
                      </Tooltip>
                      <Space :size="2">
                        <Button
                          type="link"
                          size="small"
                          @click="openChildDetail(child)"
                        >
                          详情
                        </Button>
                        <Button
                          type="link"
                          size="small"
                          :disabled="!child.primaryAction?.enabled"
                          @click="executeNavAction(child.primaryAction)"
                        >
                          工作台
                        </Button>
                        <Dropdown
                          v-if="child.actions?.length > 2"
                          :menu="buildChildMenu(child)"
                          trigger="click"
                        >
                          <Button type="link" size="small" class="!px-1">
                            <IconifyIcon
                              icon="lucide:more-horizontal"
                              :size="16"
                            />
                          </Button>
                        </Dropdown>
                      </Space>
                    </div>
                  </div>
                </div>
              </template>

              <!-- 空态 -->
              <template #emptyText>
                <ClpmEmptyState
                  v-if="!hasFilters && totalItems === 0"
                  scene="tracker"
                  title="今日无例外，回路运行正常"
                  description="当前筛选范围内没有需要处理的关注项。"
                />
                <ClpmEmptyState
                  v-else
                  scene="tracker"
                  title="筛选无结果"
                  description="当前筛选条件下没有匹配的关注项，请尝试调整筛选条件。"
                  :actions="[
                    {
                      label: '清除筛选',
                      icon: 'lucide:x',
                      onClick: handleResetFilters,
                    },
                  ]"
                />
              </template>
            </Table>
          </Card>

          <!-- R5 分页条（双口径） -->
          <div class="flex items-center justify-between text-sm text-gray-500">
            <div v-if="totalGroups > 0">
              第 {{ query.page }} 页 · {{ query.pageSize }}/页
              <span class="mx-2">｜</span>
              已加载
              {{ Math.min(query.page * query.pageSize, totalGroups) }} 回路组 ·
              {{ currentPageItemCount }} 项
              <span class="mx-2">/</span>
              共 {{ totalGroups }} 回路组 · {{ totalItems }} 项
            </div>
            <div v-else class="invisible">占位</div>
            <Pagination
              v-model:current="query.page"
              v-model:page-size="query.pageSize"
              :total="totalGroups"
              :show-size-changer="true"
              :show-total="(t: number) => `共 ${t} 回路组`"
              :page-size-options="['10', '20', '50']"
              @change="handlePageChange"
            />
          </div>
        </div>
      </Spin>
    </div>

    <!-- 详情抽屉（子项） -->
    <Drawer
      v-model:open="detailVisible"
      title="关注项详情"
      width="540"
      :destroy-on-close="true"
    >
      <template v-if="currentItem">
        <Descriptions :column="1" bordered size="small">
          <DescriptionsItem label="优先级">
            <Tag :color="priorityColor(currentItem.priority)">
              {{
                PRIORITY_LABEL[
                  currentItem.priority as keyof typeof PRIORITY_LABEL
                ]
              }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="来源">
            <Tag
              :color="
                SOURCE_COLOR[currentItem.source as keyof typeof SOURCE_COLOR]
              "
            >
              {{
                SOURCE_LABEL[currentItem.source as keyof typeof SOURCE_LABEL]
              }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="状态">
            <Tag
              :color="
                STATUS_COLOR[currentItem.status as keyof typeof STATUS_COLOR]
              "
            >
              {{
                STATUS_LABEL[currentItem.status as keyof typeof STATUS_LABEL]
              }}
            </Tag>
            <span class="ml-2 text-xs text-gray-400">
              来源状态：{{ currentItem.sourceStatus }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="回路">
            {{ currentItem.tagName }}
          </DescriptionsItem>
          <DescriptionsItem label="装置·单元">
            {{ currentItem.unitName || '-' }}
          </DescriptionsItem>
          <DescriptionsItem label="标题">
            {{ currentItem.title }}
          </DescriptionsItem>
          <DescriptionsItem label="摘要">
            {{ currentItem.summary }}
          </DescriptionsItem>
          <DescriptionsItem label="排序原因">
            <div v-for="reason in currentItem.rankReasons" :key="reason">
              · {{ reason }}
            </div>
          </DescriptionsItem>
          <DescriptionsItem label="发生时间">
            {{ formatTime(currentItem.occurredAt) }}
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.updatedAt" label="更新时间">
            {{ formatTime(currentItem.updatedAt) }}
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.score !== undefined" label="评分">
            {{ currentItem.score }}
            <span v-if="currentItem.scoreDelta !== undefined" class="ml-2">
              <Tag :color="currentItem.scoreDelta <= -5 ? 'red' : 'orange'">
                {{ currentItem.scoreDelta > 0 ? '+' : ''
                }}{{ currentItem.scoreDelta }}
              </Tag>
            </span>
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.confidenceLevel" label="可信度">
            {{ currentItem.confidenceLevel }}
          </DescriptionsItem>
          <DescriptionsItem v-if="currentItem.sourceSeverity" label="严重度">
            {{ currentItem.sourceSeverity }}
          </DescriptionsItem>
        </Descriptions>

        <div class="mt-4">
          <div class="mb-2 text-sm font-medium text-gray-700">可用操作</div>
          <Space wrap>
            <Button
              v-for="action in currentItem.actions"
              :key="action.type"
              :type="
                action.type === currentItem.primaryAction.type
                  ? 'primary'
                  : 'default'
              "
              :disabled="!action.enabled || !!actingAttentionId"
              :loading="actingAttentionId === currentItem.attentionId"
              size="small"
              @click="
                action.type === 'RESOLVE'
                  ? openResolveModal(currentItem)
                  : executeChildAction(currentItem, action)
              "
            >
              {{ action.label }}
            </Button>
          </Space>
          <div
            v-for="action in currentItem.actions.filter(
              (a) => !a.enabled && a.disabledReason,
            )"
            :key="`reason-${action.type}`"
            class="mt-2 text-xs text-gray-400"
          >
            {{ action.label }}：{{ action.disabledReason }}
          </div>
        </div>

        <div
          class="mt-4 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-700"
        >
          <Tooltip
            title="平台只输出建议、证据、风险和回退方案；参数由授权人员人工实施并留痕。"
          >
            <span class="inline-flex items-center gap-1">
              <IconifyIcon icon="lucide:shield-alert" :size="13" />
              平台安全边界：只读建议、人工实施、需留痕
            </span>
          </Tooltip>
        </div>
      </template>
    </Drawer>

    <!-- 处置弹窗 -->
    <Modal
      v-model:open="resolveVisible"
      title="处置预警事件"
      :confirm-loading="resolveLoading"
      ok-text="提交处置"
      cancel-text="取消"
      @ok="handleResolveSubmit"
    >
      <div class="py-2">
        <p class="mb-2 text-sm text-gray-600">
          请填写处置说明（必填），提交后事件状态将变为「已处置」。
        </p>
        <Textarea
          v-model:value="resolveNote"
          :rows="4"
          placeholder="例如：检查发现阀门存在粘滞，已联系仪表人员检修并调整 PID 参数..."
          :maxlength="500"
          show-count
        />
      </div>
    </Modal>
  </Page>
</template>

<style scoped>
.clpm-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 12px 16px;
  background: hsl(var(--card) / 60%);
  border-radius: 6px;
}

:deep(.row-urgent) {
  background-color: hsl(var(--destructive) / 6%);
}

:deep(.row-urgent:hover) > td {
  background-color: hsl(var(--destructive) / 10%) !important;
}

:deep(.row-high) {
  box-shadow: inset 3px 0 0 hsl(var(--warning));
}

:deep(.ant-table-expanded-row) > td {
  padding: 0 !important;
}
</style>
