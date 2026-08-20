<script setup lang="ts">
/**
 * 处置工作台（/handling/workbench，v2.0 双 Tab，§8.1）
 *
 * Tab1 建议审核（默认）：统计卡 + 筛选 + 建议表格（接受/驳回/忽略）
 *   + 多选已接受建议批量转工单 + 手动新增建议。
 * Tab2 工单执行：统计卡 + 筛选 + 工单表格，行点击开工单详情抽屉。
 * 深链接：?tab=orders 切 Tab2；?focus={orderId} 切 Tab2 并尝试开抽屉
 *   （旧 v1.x focus=建议 id 的链接在 v2.0 下抽屉加载失败显示降级空态）。
 * H1 后端并行开发中：接口失败按空态/错误提示降级，不白屏。
 */
import type { TableColumnsType } from 'ant-design-vue';
import type { Dayjs } from 'dayjs';

import type { HandlingApi } from '#/api/handling';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Card,
  DatePicker,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Table,
  TabPane,
  Tabs,
  Tag,
  Textarea,
  TreeSelect,
} from 'ant-design-vue';

import {
  acceptSuggestionApi,
  convertSuggestionsApi,
  createSuggestionApi,
  getHandlingOrdersApi,
  getHandlingSuggestionsApi,
  ignoreSuggestionApi,
  rejectSuggestionApi,
} from '#/api/handling';
import { getLoopListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { IMPORTANCE_LEVEL_LABEL } from '#/constants/clpm-ui';
import { formatLocalTime } from '#/utils/format';

import OrderDetailDrawer from './components/order-detail-drawer.vue';
import {
  ACTION_TYPE_OPTIONS,
  ORDER_SOURCE_TEXT,
  ORDER_STATUS_COLOR,
  ORDER_TAB_OPTIONS,
  SOURCE_TEXT,
  SUGGESTION_STATUS_COLOR,
  SUGGESTION_TAB_OPTIONS,
} from './constants';

const route = useRoute();
const userStore = useUserStore();

/** 流转操作角色（§7：IC_ENGINEER/PE_ENGINEER/ADMIN；SPONSOR/EXPERT 只读） */
const canOperate = computed(() => {
  const roles = userStore.userInfo?.roles ?? [];
  return roles.some((r) => ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'].includes(r));
});

const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('handling-list');

const fmt = (ts: null | string | undefined) =>
  formatLocalTime(ts, 'MM-DD HH:mm');

// ===========================================================================
// Tab 切换（默认建议审核；懒加载：首次切入工单 Tab 时拉取）
// ===========================================================================

const activeTab = ref<'orders' | 'suggestions'>('suggestions');
const ordersLoaded = ref(false);

watch(activeTab, (tab) => {
  if (tab === 'orders' && !ordersLoaded.value) {
    ordersLoaded.value = true;
    refreshOrderAll();
  }
});

// ===========================================================================
// Tab1 建议审核
// ===========================================================================

const sugLoading = ref(false);
const sugItems = ref<HandlingApi.SuggestionItem[]>([]);
const sugTotal = ref(0);

const sugQuery = reactive({
  page: 1,
  pageSize: 20,
  statusTab: '' as '' | HandlingApi.SuggestionStatus,
  source: undefined as HandlingApi.Source | undefined,
  plantNodeId: undefined as string | undefined,
  importanceLevel: undefined as number | undefined,
  keyword: '',
});

async function loadSuggestions() {
  sugLoading.value = true;
  try {
    const res = await getHandlingSuggestionsApi({
      page: sugQuery.page,
      pageSize: sugQuery.pageSize,
      status: sugQuery.statusTab || undefined,
      source: sugQuery.source,
      plantNodeId: sugQuery.plantNodeId,
      importanceLevel: sugQuery.importanceLevel,
      keyword: sugQuery.keyword.trim() || undefined,
    });
    sugItems.value = res.items;
    sugTotal.value = res.total;
  } catch (error: any) {
    message.error(error?.message ?? '建议清单加载失败');
    sugItems.value = [];
    sugTotal.value = 0;
  } finally {
    sugLoading.value = false;
  }
}

// ----- 统计卡（按 status 分别取 total；本周转化暂以 CONVERTED 总数近似） -----

const sugStats = reactive({
  pending: 0,
  accepted: 0,
  rejected: 0,
  converted: 0,
});

async function countByStatus(status: HandlingApi.SuggestionStatus) {
  const res = await getHandlingSuggestionsApi({
    status,
    page: 1,
    pageSize: 1,
  });
  return res.total;
}

async function loadSugStats() {
  // TODO(H2): 后端建议统计端点就绪后切换，避免 4 次计数请求
  const [pending, accepted, rejected, converted] = await Promise.all([
    countByStatus('PENDING').catch(() => null),
    countByStatus('ACCEPTED').catch(() => null),
    countByStatus('REJECTED').catch(() => null),
    countByStatus('CONVERTED').catch(() => null),
  ]);
  sugStats.pending = pending ?? 0;
  sugStats.accepted = accepted ?? 0;
  sugStats.rejected = rejected ?? 0;
  sugStats.converted = converted ?? 0;
}

const sugStatCards = computed(() => [
  {
    key: 'PENDING',
    label: '待审核',
    value: sugStats.pending,
    color: '#fa8c16',
  },
  {
    key: 'ACCEPTED',
    label: '已接受',
    value: sugStats.accepted,
    color: '#1677ff',
  },
  {
    key: 'REJECTED',
    label: '已驳回',
    value: sugStats.rejected,
    color: '#dc3545',
  },
  // TODO(H2): "本周转化"统计口径待后端统计端点（当前以 CONVERTED 总数近似）
  {
    key: 'CONVERTED',
    label: '本周转化',
    value: sugStats.converted,
    color: '#52c41a',
  },
]);

function clickSugStatCard(key: string) {
  sugQuery.statusTab = key as HandlingApi.SuggestionStatus;
  sugQuery.page = 1;
  loadSuggestions();
}

function refreshSugAll() {
  loadSuggestions();
  loadSugStats();
}

function handleSugTableChange(pag: { current?: number; pageSize?: number }) {
  sugQuery.page = pag.current ?? 1;
  sugQuery.pageSize = pag.pageSize ?? 20;
  loadSuggestions();
}

// ----- 审核操作（接受 Popconfirm / 驳回 Modal / 忽略 Modal） -----

const acting = ref(false);

async function handleAccept(record: HandlingApi.SuggestionItem) {
  acting.value = true;
  try {
    await acceptSuggestionApi(record.id);
    message.success('已接受，可在勾选后转工单');
    refreshSugAll();
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

const rejectOpen = ref(false);
const rejectTarget = ref<HandlingApi.SuggestionItem | null>(null);
const rejectReason = ref('');

function openReject(record: HandlingApi.SuggestionItem) {
  rejectTarget.value = record;
  rejectReason.value = '';
  rejectOpen.value = true;
}

async function handleReject() {
  if (!rejectTarget.value) return;
  if (!rejectReason.value.trim()) {
    message.warning('请填写驳回原因');
    return;
  }
  acting.value = true;
  try {
    await rejectSuggestionApi(rejectTarget.value.id, {
      rejectedReason: rejectReason.value.trim(),
    });
    message.success('已驳回（终态，不可重新审核）');
    rejectOpen.value = false;
    refreshSugAll();
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

const ignoreOpen = ref(false);
const ignoreTarget = ref<HandlingApi.SuggestionItem | null>(null);
const ignoreReason = ref('');

function openIgnore(record: HandlingApi.SuggestionItem) {
  ignoreTarget.value = record;
  ignoreReason.value = '';
  ignoreOpen.value = true;
}

async function handleIgnore() {
  if (!ignoreTarget.value) return;
  if (!ignoreReason.value.trim()) {
    message.warning('请填写忽略原因');
    return;
  }
  acting.value = true;
  try {
    await ignoreSuggestionApi(ignoreTarget.value.id, {
      ignoreReason: ignoreReason.value.trim(),
    });
    message.success('已忽略');
    ignoreOpen.value = false;
    refreshSugAll();
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

// ----- 多选（仅 ACCEPTED 行可选）+ 批量转工单 -----

const selectedRowKeys = ref<string[]>([]);

const sugRowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys as string[];
  },
  getCheckboxProps: (record: HandlingApi.SuggestionItem) => ({
    disabled: record.status !== 'ACCEPTED',
  }),
}));

const convertOpen = ref(false);
const converting = ref(false);
const convertForm = reactive({
  actionType: undefined as HandlingApi.ActionType | undefined,
  plannedAt: undefined as Dayjs | undefined,
  handler: '',
  title: '',
});

function openConvert() {
  const picked = sugItems.value.filter((s) =>
    selectedRowKeys.value.includes(s.id),
  );
  if (picked.length === 0) return;
  // §2.2 多建议合一单：同一停工窗口做多件事，须同回路
  const loopIds = new Set(picked.map((s) => s.loopId));
  if (loopIds.size > 1) {
    message.warning('转工单的多条建议须属于同一回路');
    return;
  }
  convertForm.actionType = undefined;
  convertForm.plannedAt = undefined;
  convertForm.handler = '';
  convertForm.title = '';
  convertOpen.value = true;
}

async function handleConvert() {
  if (selectedRowKeys.value.length === 0) return;
  if (!convertForm.actionType) {
    message.warning('请选择处置类型');
    return;
  }
  converting.value = true;
  try {
    const order = await convertSuggestionsApi({
      suggestionIds: selectedRowKeys.value,
      actionType: convertForm.actionType,
      plannedAt: convertForm.plannedAt?.toISOString(),
      handler: convertForm.handler.trim() || undefined,
      title: convertForm.title.trim() || undefined,
    });
    message.success(`已生成工单 ${order.orderNo}`);
    convertOpen.value = false;
    selectedRowKeys.value = [];
    activeTab.value = 'orders';
    ordersLoaded.value = true;
    refreshOrderAll();
  } catch (error: any) {
    message.error(error?.message ?? '转工单失败');
  } finally {
    converting.value = false;
  }
}

// ----- 手动新增建议 -----

const suggestOpen = ref(false);
const creatingSuggestion = ref(false);
const suggestForm = reactive({
  loopId: undefined as string | undefined,
  content: '',
  basis: '',
});

const loopOptions = ref<Array<{ label: string; value: string }>>([]);
let loopSearchTimer: ReturnType<typeof setTimeout> | undefined;

function openSuggestModal() {
  suggestForm.loopId = undefined;
  suggestForm.content = '';
  suggestForm.basis = '';
  if (loopOptions.value.length === 0) searchLoops('');
  suggestOpen.value = true;
}

function searchLoops(keyword: string) {
  if (loopSearchTimer) clearTimeout(loopSearchTimer);
  loopSearchTimer = setTimeout(async () => {
    try {
      const res = await getLoopListApi({
        keyword: keyword || undefined,
        page: 1,
        pageSize: 20,
      });
      loopOptions.value = res.items.map((l) => ({
        label: `${l.tagName}${l.description ? ` · ${l.description}` : ''}`,
        value: l.loopId,
      }));
    } catch {
      loopOptions.value = [];
    }
  }, 300);
}

async function handleCreateSuggestion() {
  if (!suggestForm.loopId) {
    message.warning('请选择回路');
    return;
  }
  if (!suggestForm.content.trim()) {
    message.warning('请填写建议内容');
    return;
  }
  creatingSuggestion.value = true;
  try {
    await createSuggestionApi({
      loopId: suggestForm.loopId,
      content: suggestForm.content.trim(),
      basis: suggestForm.basis.trim() || undefined,
    });
    message.success('建议已添加（待审核）');
    suggestOpen.value = false;
    refreshSugAll();
  } catch (error: any) {
    message.error(error?.message ?? '新增建议失败');
  } finally {
    creatingSuggestion.value = false;
  }
}

// ----- Tab1 表格列 -----

const sugColumns: TableColumnsType = [
  { dataIndex: 'content', title: '建议摘要', ellipsis: true },
  { dataIndex: 'loopTagName', title: '回路', width: 150 },
  { dataIndex: 'categoryLabel', title: '诊断分类', width: 110 },
  { dataIndex: 'source', title: '来源', width: 88 },
  { dataIndex: 'suggestedByAt', title: '建议人/时间', width: 150 },
  { dataIndex: 'status', title: '审核状态', width: 92 },
  { key: 'actions', title: '操作', width: 160, fixed: 'right' },
];

// ===========================================================================
// Tab2 工单执行
// ===========================================================================

const orderLoading = ref(false);
const orderItems = ref<HandlingApi.OrderItem[]>([]);
const orderTotal = ref(0);

const orderQuery = reactive({
  page: 1,
  pageSize: 20,
  statusTab: '' as '' | HandlingApi.OrderStatus,
  actionType: undefined as HandlingApi.ActionType | undefined,
  source: undefined as HandlingApi.OrderSource | undefined,
  handler: '',
  keyword: '',
});

async function loadOrders() {
  orderLoading.value = true;
  try {
    const res = await getHandlingOrdersApi({
      page: orderQuery.page,
      pageSize: orderQuery.pageSize,
      status: orderQuery.statusTab || undefined,
      actionType: orderQuery.actionType,
      source: orderQuery.source,
      handler: orderQuery.handler.trim() || undefined,
      keyword: orderQuery.keyword.trim() || undefined,
    });
    orderItems.value = res.items;
    orderTotal.value = res.total;
  } catch (error: any) {
    message.error(error?.message ?? '工单清单加载失败');
    orderItems.value = [];
    orderTotal.value = 0;
  } finally {
    orderLoading.value = false;
  }
}

// ----- 统计卡（按 status 分别取 total；本月闭环暂以 CLOSED 总数近似） -----

const orderStats = reactive({
  pending: 0,
  executing: 0,
  verifying: 0,
  closed: 0,
});

async function loadOrderStats() {
  // TODO(H2): 后端工单统计端点就绪后切换（含月界闭环数），避免 4 次计数请求
  const counts = await Promise.all([
    getHandlingOrdersApi({ status: 'PENDING', page: 1, pageSize: 1 })
      .then((r) => r.total)
      .catch(() => null),
    getHandlingOrdersApi({ status: 'EXECUTING', page: 1, pageSize: 1 })
      .then((r) => r.total)
      .catch(() => null),
    getHandlingOrdersApi({ status: 'VERIFYING', page: 1, pageSize: 1 })
      .then((r) => r.total)
      .catch(() => null),
    getHandlingOrdersApi({ status: 'CLOSED', page: 1, pageSize: 1 })
      .then((r) => r.total)
      .catch(() => null),
  ]);
  orderStats.pending = counts[0] ?? 0;
  orderStats.executing = counts[1] ?? 0;
  orderStats.verifying = counts[2] ?? 0;
  orderStats.closed = counts[3] ?? 0;
}

const orderStatCards = computed(() => [
  {
    key: 'PENDING',
    label: '待执行',
    value: orderStats.pending,
    color: '#fa8c16',
  },
  {
    key: 'EXECUTING',
    label: '执行中',
    value: orderStats.executing,
    color: '#1677ff',
  },
  {
    key: 'VERIFYING',
    label: '验证中',
    value: orderStats.verifying,
    color: '#13c2c2',
  },
  // TODO(H2): "本月闭环"月界口径待后端统计端点（当前以 CLOSED 总数近似）
  {
    key: 'CLOSED',
    label: '本月闭环',
    value: orderStats.closed,
    color: '#52c41a',
  },
]);

function clickOrderStatCard(key: string) {
  orderQuery.statusTab = key as HandlingApi.OrderStatus;
  orderQuery.page = 1;
  loadOrders();
}

function refreshOrderAll() {
  loadOrders();
  loadOrderStats();
}

function handleOrderTableChange(pag: { current?: number; pageSize?: number }) {
  orderQuery.page = pag.current ?? 1;
  orderQuery.pageSize = pag.pageSize ?? 20;
  loadOrders();
}

// ----- 工单详情抽屉 -----

const orderDrawerOpen = ref(false);
const focusOrderId = ref<null | string>(null);

function openOrderDetail(record: HandlingApi.OrderItem) {
  focusOrderId.value = record.id;
  orderDrawerOpen.value = true;
}

// ----- Tab2 表格列 -----

const orderColumns = [
  { dataIndex: 'orderNo', title: '处置编号', width: 140 },
  { dataIndex: 'loopTagName', title: '回路', width: 140 },
  { dataIndex: 'title', title: '标题', ellipsis: true },
  { dataIndex: 'actionTypeLabel', title: '类型', width: 90 },
  { dataIndex: 'handler', title: '处置人', width: 90 },
  { dataIndex: 'plannedAt', title: '计划时间', width: 110 },
  { dataIndex: 'status', title: '状态', width: 88 },
  { dataIndex: 'updatedAt', title: '最近更新', width: 110 },
];

// ===========================================================================
// 装置树 / 深链接 / 工具栏 / 生命周期
// ===========================================================================

const plantTreeData = ref<
  Array<{ children?: any[]; label: string; value: string }>
>([]);

async function loadPlantTree() {
  try {
    const tree = await getPlantNodeTreeApi();
    const walk = (nodes: any[]): any[] =>
      nodes.map((n) => ({
        label: n.name,
        value: n.id,
        children: n.children?.length ? walk(n.children) : undefined,
      }));
    plantTreeData.value = walk(tree);
  } catch {
    plantTreeData.value = [];
  }
}

/** 深链接：?focus={orderId} 切 Tab2 并尝试开抽屉；?tab=orders 切 Tab2 */
function applyUrlContext() {
  const focus = route.query.focus as string | undefined;
  if (focus) {
    activeTab.value = 'orders';
    ordersLoaded.value = true;
    focusOrderId.value = focus;
    orderDrawerOpen.value = true;
    return;
  }
  if ((route.query.tab as string | undefined) === 'orders') {
    activeTab.value = 'orders';
    ordersLoaded.value = true;
  }
}

function refreshAll() {
  if (activeTab.value === 'suggestions') {
    refreshSugAll();
  } else {
    refreshOrderAll();
  }
}

function handleHelp() {
  showPageHelp({
    title: '处置工作台 帮助',
    content: `
      <p><b>双实体</b>：处置建议（审核对象）与处置工单（执行对象）分离——建议先审核（接受/驳回/忽略），接受后批量转工单执行。</p>
      <p><b>建议状态机</b>：待审核 → 已接受 → 已转工单；已驳回/已忽略为终态（驳回不可重新审核，复发走重新诊断或手动新增）。</p>
      <p><b>转工单</b>：勾选同一回路的多条已接受建议合并转一个工单（对应一个停工窗口做多件事）；工单编号 HD-日期-序号自动生成。</p>
      <p><b>工单状态机</b>：待执行 → 执行中 → 验证中 → 已闭环；验证无效 → 重开（可再次开工）；待执行可作废。闭环不可重开。</p>
      <p><b>KPI 验证窗口</b>：前窗=开工前 24h 基线，后窗=提交验证后 24h；验证时服务端固化快照。</p>
    `,
  });
}

onMounted(() => {
  applyUrlContext();
  if (activeTab.value === 'suggestions') {
    refreshSugAll();
  } else {
    refreshOrderAll();
  }
  loadPlantTree();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="sugLoading || orderLoading"
      subtitle="建议审核 → 转工单 → 执行反馈 → 效果验证 → 闭环"
      title="处置工作台"
    >
      <template #actions>
        <ClpmToolbarButton
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          icon="ant-design:column-height-outlined"
          @click="cycleDensity"
        />
        <ClpmToolbarButton
          icon="ant-design:question-circle-outlined"
          label="帮助"
          @click="handleHelp"
        />
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="refreshAll"
        />
      </template>
    </ClpmPageToolbar>

    <Tabs v-model:active-key="activeTab" size="small">
      <!-- ============ Tab1 建议审核 ============ -->
      <TabPane key="suggestions" tab="建议审核">
        <!-- 统计卡（点击即筛选，§8.1） -->
        <div class="mb-3 mt-2 grid grid-cols-4 gap-3">
          <Card
            v-for="c in sugStatCards"
            :key="c.key"
            :body-style="{ padding: '10px 16px', cursor: 'pointer' }"
            size="small"
            @click="clickSugStatCard(c.key)"
          >
            <div class="flex items-baseline justify-between">
              <span class="text-xs text-neutral-500">{{ c.label }}</span>
              <span :style="{ color: c.color }" class="text-xl font-semibold">
                {{ c.value }}
              </span>
            </div>
          </Card>
        </div>

        <!-- 筛选行 + 批量操作 -->
        <div class="mb-3 flex flex-wrap items-center gap-3">
          <Select
            v-model:value="sugQuery.statusTab"
            :options="SUGGESTION_TAB_OPTIONS"
            style="width: 110px"
            @change="((sugQuery.page = 1), loadSuggestions())"
          />
          <Select
            v-model:value="sugQuery.source"
            :allow-clear="true"
            :options="[
              { label: '系统建议', value: 'SYSTEM' },
              { label: '人工新增', value: 'MANUAL' },
            ]"
            placeholder="来源"
            style="width: 110px"
            @change="((sugQuery.page = 1), loadSuggestions())"
          />
          <TreeSelect
            v-model:value="sugQuery.plantNodeId"
            :allow-clear="true"
            :tree-data="plantTreeData"
            :tree-default-expanded-keys="plantTreeData.map((n) => n.value)"
            placeholder="装置"
            style="width: 180px"
            tree-node-filter-prop="label"
            @change="((sugQuery.page = 1), loadSuggestions())"
          />
          <Select
            v-model:value="sugQuery.importanceLevel"
            :allow-clear="true"
            :options="[
              { label: '1 级（关键）', value: 1 },
              { label: '2 级（重要）', value: 2 },
              { label: '3 级（一般）', value: 3 },
            ]"
            placeholder="回路等级"
            style="width: 130px"
            @change="((sugQuery.page = 1), loadSuggestions())"
          />
          <Input
            v-model:value="sugQuery.keyword"
            allow-clear
            placeholder="回路位号/建议内容"
            style="width: 180px"
            @press-enter="((sugQuery.page = 1), loadSuggestions())"
          />
          <div v-if="canOperate" class="ml-auto flex gap-2">
            <Button
              :disabled="selectedRowKeys.length === 0"
              type="primary"
              @click="openConvert"
            >
              转工单{{ selectedRowKeys.length > 0 ? `（${selectedRowKeys.length}）` : '' }}
            </Button>
            <Button @click="openSuggestModal">新增建议</Button>
          </div>
        </div>

        <Card :body-style="{ padding: '0' }" size="small">
          <ClpmDataCanvas
            :empty="!sugLoading && sugItems.length === 0"
            empty-text="暂无处置建议"
          >
            <Table
              :columns="sugColumns"
              :data-source="sugItems"
              :loading="sugLoading"
              :pagination="{
                current: sugQuery.page,
                pageSize: sugQuery.pageSize,
                showSizeChanger: true,
                pageSizeOptions: ['10', '20', '50'],
                showTotal: (t: number) => `共 ${t} 条`,
                total: sugTotal,
              }"
              :row-selection="canOperate ? sugRowSelection : undefined"
              :size="tableSize"
              row-key="id"
              :scroll="{ x: 1100 }"
              @change="handleSugTableChange"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.dataIndex === 'content'">
                  <span :title="record.content">{{ record.content }}</span>
                </template>
                <template v-else-if="column.dataIndex === 'loopTagName'">
                  <div class="flex flex-col">
                    <span class="font-medium">{{ record.loopTagName }}</span>
                    <span class="text-xs text-neutral-500">
                      <template v-if="record.loopDescription">
                        {{ record.loopDescription }} ·
                      </template>
                      <template v-if="record.importanceLevel">
                        {{ IMPORTANCE_LEVEL_LABEL[record.importanceLevel] }}
                      </template>
                    </span>
                  </div>
                </template>
                <template v-else-if="column.dataIndex === 'categoryLabel'">
                  {{ record.categoryLabel ?? '—' }}
                </template>
                <template v-else-if="column.dataIndex === 'source'">
                  {{ SOURCE_TEXT[record.source as HandlingApi.Source] }}
                </template>
                <template v-else-if="column.dataIndex === 'suggestedByAt'">
                  {{ record.suggestedBy }} · {{ fmt(record.suggestedAt) }}
                </template>
                <template v-else-if="column.dataIndex === 'status'">
                  <Tag
                    :color="
                      SUGGESTION_STATUS_COLOR[
                        record.status as HandlingApi.SuggestionStatus
                      ]
                    "
                  >
                    {{ record.statusLabel }}
                  </Tag>
                  <div
                    v-if="record.status === 'CONVERTED' && record.convertedOrderNo"
                    class="text-xs text-neutral-500"
                  >
                    {{ record.convertedOrderNo }}
                  </div>
                </template>
                <template v-else-if="column.key === 'actions'">
                  <div v-if="record.status === 'PENDING'" class="flex gap-1">
                    <Popconfirm
                      title="确认接受该建议？接受后可勾选转工单"
                      @confirm="
                        handleAccept(record as HandlingApi.SuggestionItem)
                      "
                    >
                      <Button size="small" type="link">接受</Button>
                    </Popconfirm>
                    <Button
                      size="small"
                      type="link"
                      @click="openReject(record as HandlingApi.SuggestionItem)"
                    >
                      驳回
                    </Button>
                    <Button
                      size="small"
                      type="link"
                      @click="openIgnore(record as HandlingApi.SuggestionItem)"
                    >
                      忽略
                    </Button>
                  </div>
                  <span v-else class="text-xs text-neutral-400">—</span>
                </template>
              </template>
            </Table>
          </ClpmDataCanvas>
        </Card>
      </TabPane>

      <!-- ============ Tab2 工单执行 ============ -->
      <TabPane key="orders" tab="工单执行">
        <!-- 统计卡（点击即筛选，§8.1） -->
        <div class="mb-3 mt-2 grid grid-cols-4 gap-3">
          <Card
            v-for="c in orderStatCards"
            :key="c.key"
            :body-style="{ padding: '10px 16px', cursor: 'pointer' }"
            size="small"
            @click="clickOrderStatCard(c.key)"
          >
            <div class="flex items-baseline justify-between">
              <span class="text-xs text-neutral-500">{{ c.label }}</span>
              <span :style="{ color: c.color }" class="text-xl font-semibold">
                {{ c.value }}
              </span>
            </div>
          </Card>
        </div>

        <!-- 筛选行 -->
        <div class="mb-3 flex flex-wrap items-center gap-3">
          <Select
            v-model:value="orderQuery.statusTab"
            :options="ORDER_TAB_OPTIONS"
            style="width: 110px"
            @change="((orderQuery.page = 1), loadOrders())"
          />
          <Select
            v-model:value="orderQuery.actionType"
            :allow-clear="true"
            :options="ACTION_TYPE_OPTIONS"
            placeholder="处置类型"
            style="width: 130px"
            @change="((orderQuery.page = 1), loadOrders())"
          />
          <Select
            v-model:value="orderQuery.source"
            :allow-clear="true"
            :options="[
              { label: ORDER_SOURCE_TEXT.DIAGNOSIS, value: 'DIAGNOSIS' },
              { label: ORDER_SOURCE_TEXT.MANUAL, value: 'MANUAL' },
            ]"
            placeholder="来源"
            style="width: 110px"
            @change="((orderQuery.page = 1), loadOrders())"
          />
          <Input
            v-model:value="orderQuery.handler"
            allow-clear
            placeholder="处置人"
            style="width: 120px"
            @press-enter="((orderQuery.page = 1), loadOrders())"
          />
          <Input
            v-model:value="orderQuery.keyword"
            allow-clear
            placeholder="编号/回路/标题"
            style="width: 180px"
            @press-enter="((orderQuery.page = 1), loadOrders())"
          />
        </div>

        <Card :body-style="{ padding: '0' }" size="small">
          <ClpmDataCanvas
            :empty="!orderLoading && orderItems.length === 0"
            empty-text="暂无处置工单"
          >
            <Table
              :columns="orderColumns"
              :custom-row="
                (record: HandlingApi.OrderItem) => ({
                  onClick: () => openOrderDetail(record),
                  style: { cursor: 'pointer' },
                })
              "
              :data-source="orderItems"
              :loading="orderLoading"
              :pagination="{
                current: orderQuery.page,
                pageSize: orderQuery.pageSize,
                showSizeChanger: true,
                pageSizeOptions: ['10', '20', '50'],
                showTotal: (t: number) => `共 ${t} 条`,
                total: orderTotal,
              }"
              :size="tableSize"
              row-key="id"
              @change="handleOrderTableChange"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.dataIndex === 'orderNo'">
                  <span class="font-mono font-medium">{{
                    record.orderNo
                  }}</span>
                </template>
                <template v-else-if="column.dataIndex === 'loopTagName'">
                  <div class="flex flex-col">
                    <span class="font-medium">{{ record.loopTagName }}</span>
                    <span
                      v-if="record.importanceLevel"
                      class="text-xs text-neutral-500"
                    >
                      {{ IMPORTANCE_LEVEL_LABEL[record.importanceLevel] }}
                    </span>
                  </div>
                </template>
                <template v-else-if="column.dataIndex === 'actionTypeLabel'">
                  {{ record.actionTypeLabel ?? '—' }}
                </template>
                <template v-else-if="column.dataIndex === 'handler'">
                  {{ record.handler ?? '—' }}
                </template>
                <template v-else-if="column.dataIndex === 'plannedAt'">
                  {{ fmt(record.plannedAt) }}
                </template>
                <template v-else-if="column.dataIndex === 'status'">
                  <Tag
                    :color="
                      ORDER_STATUS_COLOR[record.status as HandlingApi.OrderStatus]
                    "
                  >
                    {{ record.statusLabel }}
                  </Tag>
                </template>
                <template v-else-if="column.dataIndex === 'updatedAt'">
                  {{ fmt(record.updatedAt) }}
                </template>
              </template>
            </Table>
          </ClpmDataCanvas>
        </Card>
      </TabPane>
    </Tabs>

    <!-- 工单详情抽屉（Tab2 行点击 / 深链接 focus） -->
    <OrderDetailDrawer
      v-model:open="orderDrawerOpen"
      :can-operate="canOperate"
      :order-id="focusOrderId"
      @updated="refreshOrderAll"
    />

    <!-- 驳回 Modal（原因必填） -->
    <Modal
      v-model:open="rejectOpen"
      cancel-text="取消"
      ok-text="确认驳回"
      title="驳回建议"
      @ok="handleReject"
    >
      <div class="py-2">
        <p class="mb-2 text-sm text-neutral-600">
          驳回为终态（不可重新审核），复发请走重新诊断或手动新增；请填写驳回原因留痕。
        </p>
        <Textarea
          v-model:value="rejectReason"
          :maxlength="200"
          :rows="2"
          placeholder="驳回原因（必填，如：与现场工况不符/建议不适用）"
          show-count
        />
      </div>
    </Modal>

    <!-- 忽略 Modal（原因必填） -->
    <Modal
      v-model:open="ignoreOpen"
      cancel-text="取消"
      ok-text="确认忽略"
      title="忽略建议"
      @ok="handleIgnore"
    >
      <div class="py-2">
        <p class="mb-2 text-sm text-neutral-600">
          忽略为轻量关闭（终态），适用于建议不适用/重复场景；请填写忽略原因留痕。
        </p>
        <Textarea
          v-model:value="ignoreReason"
          :maxlength="200"
          :rows="2"
          placeholder="忽略原因（必填，如：与近期检修计划重复）"
          show-count
        />
      </div>
    </Modal>

    <!-- 转工单 Modal -->
    <Modal
      v-model:open="convertOpen"
      :confirm-loading="converting"
      cancel-text="取消"
      ok-text="生成工单"
      :title="`转工单（${selectedRowKeys.length} 条建议）`"
      @ok="handleConvert"
    >
      <div class="flex flex-col gap-3 py-2">
        <div class="flex items-center gap-2">
          <span class="w-16 shrink-0 text-xs text-neutral-500">处置类型</span>
          <Select
            v-model:value="convertForm.actionType"
            :options="ACTION_TYPE_OPTIONS"
            class="flex-1"
            placeholder="选择处置类型（必填，8 类）"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="w-16 shrink-0 text-xs text-neutral-500">计划时间</span>
          <DatePicker
            v-model:value="convertForm.plannedAt"
            class="flex-1"
            placeholder="计划处置时间（可选）"
            show-time
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="w-16 shrink-0 text-xs text-neutral-500">处置人</span>
          <Input
            v-model:value="convertForm.handler"
            :maxlength="64"
            class="flex-1"
            placeholder="执行处置的人员/班组（可选）"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="w-16 shrink-0 text-xs text-neutral-500">标题</span>
          <Input
            v-model:value="convertForm.title"
            :maxlength="200"
            class="flex-1"
            placeholder="工单标题（可选，缺省取首条建议内容前 50 字）"
          />
        </div>
      </div>
    </Modal>

    <!-- 新增建议 Modal -->
    <Modal
      v-model:open="suggestOpen"
      :confirm-loading="creatingSuggestion"
      cancel-text="取消"
      ok-text="添加建议"
      title="新增建议"
      @ok="handleCreateSuggestion"
    >
      <div class="flex flex-col gap-3 py-2">
        <div class="flex items-center gap-2">
          <span class="w-16 shrink-0 text-xs text-neutral-500">回路</span>
          <Select
            v-model:value="suggestForm.loopId"
            :filter-option="false"
            :options="loopOptions"
            class="flex-1"
            placeholder="搜索并选择回路（位号/名称）"
            show-search
            @search="searchLoops"
          />
        </div>
        <div class="flex items-start gap-2">
          <span class="w-16 shrink-0 pt-1 text-xs text-neutral-500">内容</span>
          <Textarea
            v-model:value="suggestForm.content"
            :maxlength="500"
            :rows="3"
            class="flex-1"
            placeholder="建议内容（必填）"
            show-count
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="w-16 shrink-0 text-xs text-neutral-500">依据</span>
          <Input
            v-model:value="suggestForm.basis"
            :maxlength="500"
            class="flex-1"
            placeholder="建议依据（可选，如：现场巡检发现/工艺通知单）"
          />
        </div>
      </div>
    </Modal>
  </Page>
</template>
