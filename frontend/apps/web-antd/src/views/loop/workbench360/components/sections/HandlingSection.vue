<!--
  问题处置剖面（workbench360 P4-3；v3 §6.4，原型 renderSec("handling")）

  双实体（红线④）：
  - 建议 loop_action_item：5 态表（页内 通过/驳回/忽略/转工单）；
  - 工单 handling_order：时间线卡（↗ / 行点击 → 工单详情抽屉）。

  复用纪律（禁分叉）：
  - 工单详情整件复用 views/handling/components/order-detail-drawer（v2.0
    自包含：六态流转 + KPI 前后对比 + 时间线，canOperate 按角色下发）；
  - 审核动作语义与旧处置工作台逐字对齐（accept→可转单 / reject+原因终态 /
    ignore+原因 / convert 多合一单；驳回/忽略原因必填）；
  - 创建处置项弹窗复用 OrderCreateModal（手工来源）。
-->
<script setup lang="ts">
import type { Dayjs } from 'dayjs';

import type { HandlingApi } from '#/api/handling';

import { computed, reactive, ref, watch } from 'vue';

import { useUserStore } from '@vben/stores';

import { DatePicker, Input, message, Modal, Select, Textarea } from 'ant-design-vue';

import {
  acceptSuggestionApi,
  convertSuggestionsApi,
  getHandlingOrdersApi,
  getHandlingSuggestionsApi,
  ignoreSuggestionApi,
  rejectSuggestionApi,
} from '#/api/handling';
import { formatLocalTime } from '#/utils/format';
import OrderDetailDrawer from '#/views/handling/components/order-detail-drawer.vue';
import {
  ACTION_TYPE_OPTIONS,
  ORDER_SOURCE_TEXT,
} from '#/views/handling/constants';

import OrderCreateModal from '../OrderCreateModal.vue';

const props = defineProps<{
  /** 当前回路位号（弹窗/抽屉标题） */
  loopTagName: null | string;
  /** 选中回路 ID */
  selectedLoopId: null | string;
}>();

const emit = defineEmits<{
  (e: 'journeyDirty'): void;
}>();

const userStore = useUserStore();

/** 流转操作权限（与旧处置工作台同口径：管理员/仪控/工艺工程师） */
const canOperate = computed(() => {
  const roles = userStore.userInfo?.roles ?? [];
  return roles.some((r) => ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'].includes(r));
});

/* ── 建议清单（5 态） ── */
const SUG_PAGE_SIZE = 8;
const sug = reactive({
  error: '',
  items: [] as HandlingApi.SuggestionItem[],
  loading: false,
  page: 1,
  total: 0,
});

async function loadSuggestions(page = sug.page) {
  const id = props.selectedLoopId;
  if (!id) {
    sug.items = [];
    sug.total = 0;
    return;
  }
  sug.loading = true;
  sug.error = '';
  try {
    const res = await getHandlingSuggestionsApi({
      loopId: id,
      page,
      pageSize: SUG_PAGE_SIZE,
    });
    sug.items = res.items;
    sug.total = res.total;
    sug.page = res.page;
  } catch (error: any) {
    sug.error = error?.message ?? '建议清单加载失败';
    sug.items = [];
  } finally {
    sug.loading = false;
  }
}

/* ── 工单清单（状态分组 + updatedAt DESC，首页首条=最新在途） ── */
const ORD_PAGE_SIZE = 5;
const ord = reactive({
  error: '',
  items: [] as HandlingApi.OrderItem[],
  loading: false,
  page: 1,
  total: 0,
});

async function loadOrders(page = ord.page) {
  const id = props.selectedLoopId;
  if (!id) {
    ord.items = [];
    ord.total = 0;
    return;
  }
  ord.loading = true;
  ord.error = '';
  try {
    const res = await getHandlingOrdersApi({
      loopId: id,
      page,
      pageSize: ORD_PAGE_SIZE,
    });
    ord.items = res.items;
    ord.total = res.total;
    ord.page = res.page;
  } catch (error: any) {
    ord.error = error?.message ?? '工单清单加载失败';
    ord.items = [];
  } finally {
    ord.loading = false;
  }
}

function loadAll() {
  void loadSuggestions(1);
  void loadOrders(1);
}

watch(
  () => props.selectedLoopId,
  (id) => {
    if (id) loadAll();
  },
  { immediate: true },
);

/** 动作条徽标：在途工单数（待执行/执行中/重开，当前页口径已注明 title） */
const inFlightCount = computed(
  () =>
    ord.items.filter((o) =>
      ['EXECUTING', 'PENDING', 'REOPENED'].includes(o.status),
    ).length,
);

/** 待审核建议数（全量口径，独立 status=PENDING 查询 total） */
const pendingSugCount = ref(0);

async function loadPendingCount() {
  const id = props.selectedLoopId;
  if (!id) {
    pendingSugCount.value = 0;
    return;
  }
  try {
    const res = await getHandlingSuggestionsApi({
      loopId: id,
      page: 1,
      pageSize: 1,
      status: 'PENDING',
    });
    pendingSugCount.value = res.total;
  } catch {
    pendingSugCount.value = 0;
  }
}

watch(
  () => props.selectedLoopId,
  () => {
    void loadPendingCount();
  },
  { immediate: true },
);

/* ── 审核动作（与旧处置工作台同语义） ── */
const acting = ref(false);

async function onAccept(row: HandlingApi.SuggestionItem) {
  if (acting.value) return;
  acting.value = true;
  try {
    await acceptSuggestionApi(row.id);
    message.success('已接受，可转工单');
    await loadSuggestions();
    await loadPendingCount();
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

const rejectOpen = ref(false);
const rejectTarget = ref<HandlingApi.SuggestionItem | null>(null);
const rejectReason = ref('');

function openReject(row: HandlingApi.SuggestionItem) {
  rejectTarget.value = row;
  rejectReason.value = '';
  rejectOpen.value = true;
}

async function onReject() {
  if (!rejectTarget.value || acting.value) return;
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
    await loadSuggestions();
    await loadPendingCount();
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

const ignoreOpen = ref(false);
const ignoreTarget = ref<HandlingApi.SuggestionItem | null>(null);
const ignoreReason = ref('');

function openIgnore(row: HandlingApi.SuggestionItem) {
  ignoreTarget.value = row;
  ignoreReason.value = '';
  ignoreOpen.value = true;
}

async function onIgnore() {
  if (!ignoreTarget.value || acting.value) return;
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
    await loadSuggestions();
  } catch (error: any) {
    message.error(error?.message ?? '操作失败');
  } finally {
    acting.value = false;
  }
}

/* ── 转工单（单建议；多建议合一单走旧处置工作台批量勾选，本页保持单条零跳转） ── */
const convertOpen = ref(false);
const convertTarget = ref<HandlingApi.SuggestionItem | null>(null);
const convertForm = reactive({
  actionType: undefined as HandlingApi.ActionType | undefined,
  handler: '',
  plannedAt: undefined as Dayjs | undefined,
  title: '',
});
const converting = ref(false);

function openConvert(row: HandlingApi.SuggestionItem) {
  convertTarget.value = row;
  convertForm.actionType = undefined;
  convertForm.handler = '';
  convertForm.plannedAt = undefined;
  convertForm.title = '';
  convertOpen.value = true;
}

async function onConvert() {
  if (!convertTarget.value || converting.value) return;
  if (!convertForm.actionType) {
    message.warning('请选择处置类型');
    return;
  }
  converting.value = true;
  try {
    const order = await convertSuggestionsApi({
      suggestionIds: [convertTarget.value.id],
      actionType: convertForm.actionType,
      plannedAt: convertForm.plannedAt?.toISOString(),
      handler: convertForm.handler.trim() || undefined,
      title: convertForm.title.trim() || undefined,
    });
    message.success(`已生成工单 ${order.orderNo}`);
    convertOpen.value = false;
    await loadSuggestions();
    await loadOrders(1);
    await loadPendingCount();
    emit('journeyDirty');
  } catch (error: any) {
    message.error(error?.message ?? '转工单失败');
  } finally {
    converting.value = false;
  }
}

/* ── 工单详情抽屉（复用 order-detail-drawer） ── */
const detailOpen = ref(false);
const detailOrderId = ref<null | string>(null);

function openOrder(orderId: null | string) {
  if (!orderId) return;
  detailOrderId.value = orderId;
  detailOpen.value = true;
}

async function onOrderUpdated() {
  await loadOrders();
  await loadSuggestions();
  emit('journeyDirty');
}

/* ── 创建处置项（手工来源） ── */
const createOpen = ref(false);

function onCreated(_orderNo: string) {
  void loadOrders(1);
  emit('journeyDirty');
}

/* ── 渲染辅助 ── */
const fmt = (ts: null | string | undefined) =>
  formatLocalTime(ts, 'MM-DD HH:mm');

function sugStatusCls(status: HandlingApi.SuggestionStatus): string {
  switch (status) {
    case 'ACCEPTED': {
      return 't-info';
    }
    case 'CONVERTED': {
      return 't-ok';
    }
    case 'PENDING': {
      return 't-warn';
    }
    case 'REJECTED': {
      return 't-danger';
    }
    default: {
      return 't-gray';
    }
  }
}

function orderStatusCls(status: HandlingApi.OrderStatus): string {
  switch (status) {
    case 'CLOSED': {
      return 't-ok';
    }
    case 'EXECUTING':
    case 'VERIFYING': {
      return 't-info';
    }
    case 'PENDING': {
      return 't-warn';
    }
    case 'REOPENED': {
      return 't-danger';
    }
    default: {
      return 't-gray';
    }
  }
}

/** 最新工单时间线节点（诚实口径：仅渲染该行实有字段） */
interface TlNode {
  cls: string;
  detail: string;
  title: string;
  ts: null | string;
}

const latestOrder = computed(() => ord.items[0] ?? null);

const latestNodes = computed<TlNode[]>(() => {
  const o = latestOrder.value;
  if (!o) return [];
  const nodes: TlNode[] = [
    {
      cls: 'done',
      detail: `来源：${ORDER_SOURCE_TEXT[o.source] ?? o.source}${
        o.actionTypeLabel ? ` · ${o.actionTypeLabel}` : ''
      }`,
      title: '工单创建',
      ts: null,
    },
    {
      cls: o.plannedAt ? 'done' : 'pending',
      detail: `${o.handler ? `处置人 ${o.handler}` : '处置人待定'}${
        o.plannedBy ? ` · 创建 ${o.plannedBy}` : ''
      }`,
      title: '计划实施',
      ts: o.plannedAt ?? null,
    },
  ];
  if (o.startedAt)
    nodes.push({
      cls: 'done',
      detail: o.handler ?? '',
      title: '开工',
      ts: o.startedAt,
    });
  if (o.submittedAt)
    nodes.push({
      cls: 'done',
      detail: '参数/措施已实施，待验证',
      title: '提交验证',
      ts: o.submittedAt,
    });
  if (o.verifiedAt)
    nodes.push({
      cls: o.verifyResult === 'EFFECTIVE' ? 'ok' : 'warn',
      detail:
        o.verifyResult === 'EFFECTIVE'
          ? '验证有效，工单闭环'
          : '验证无效，工单重开',
      title: '效果验证',
      ts: o.verifiedAt,
    });
  else if (['EXECUTING', 'REOPENED', 'VERIFYING'].includes(o.status))
    nodes.push({
      cls: 'future',
      detail: '实施后 T+7d 前后窗 KPI 对比（工单详情内可发起）',
      title: '效果验证（待验证）',
      ts: null,
    });
  return nodes;
});

const otherOrders = computed(() => ord.items.slice(1));

const sugTotalPages = computed(() =>
  Math.max(1, Math.ceil(sug.total / SUG_PAGE_SIZE)),
);
const ordTotalPages = computed(() =>
  Math.max(1, Math.ceil(ord.total / ORD_PAGE_SIZE)),
);
</script>

<template>
  <div class="wb360-handling">
    <!-- 动作条 -->
    <div class="act-bar">
      <button class="btn primary sm" type="button" @click="createOpen = true">
        创建处置项
      </button>
      <span
        class="tag t-info"
        title="待执行/执行中/重开状态的处置工单（当前页口径）"
      >
        <span class="dot"></span>{{ inFlightCount }} 工单在途
      </span>
      <span
        class="tag"
        :class="pendingSugCount > 0 ? 't-warn' : 't-gray'"
        title="待审核处置建议（全量）"
      >
        <span class="dot"></span>待审核 {{ pendingSugCount }}
      </span>
      <span class="spacer"></span>
      <span class="dim">建议审核 → 转工单 → 实施 → 效果验证闭环</span>
    </div>

    <div class="cols2">
      <!-- 左：处置建议（5 态） -->
      <div class="col">
        <b class="col-title">处置建议（本回路）</b>
        <div v-if="sug.error" class="error-line">
          {{ sug.error }}
          <button class="link" type="button" @click="loadSuggestions()">
            重试
          </button>
        </div>
        <table class="tbl">
          <thead>
            <tr>
              <th>建议</th>
              <th>来源</th>
              <th>状态</th>
              <th style="width: 150px">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="sug.loading && sug.items.length === 0">
              <td class="dim" colspan="4">建议加载中…</td>
            </tr>
            <tr v-else-if="sug.items.length === 0">
              <td class="dim" colspan="4">
                暂无处置建议（诊断结论产出或手工创建后出现在此）
              </td>
            </tr>
            <template v-else>
              <tr v-for="row in sug.items" :key="row.id">
                <td>
                  <div class="sug-content" :title="row.content">
                    {{ row.content }}
                  </div>
                  <div class="sug-meta dim">
                    {{ row.categoryLabel ?? '' }}
                    {{ row.basis ? ` · 依据 ${row.basis}` : '' }}
                  </div>
                </td>
                <td class="dim">
                  {{ row.source === 'SYSTEM' ? '诊断' : '手工' }}
                </td>
                <td>
                  <span
                    class="tag"
                    :class="sugStatusCls(row.status)"
                    :title="
                      row.status === 'REJECTED'
                        ? `驳回原因：${row.rejectedReason ?? '—'}`
                        : row.status === 'IGNORED'
                          ? `忽略原因：${row.ignoreReason ?? '—'}`
                          : undefined
                    "
                  >
                    <span class="dot"></span>{{ row.statusLabel }}
                  </span>
                </td>
                <td>
                  <template v-if="row.status === 'PENDING'">
                    <button
                      class="link"
                      :disabled="acting || !canOperate"
                      type="button"
                      @click="onAccept(row)"
                      >通过</button
                    >
                    <button
                      class="link"
                      :disabled="acting || !canOperate"
                      type="button"
                      @click="openReject(row)"
                      >驳回</button
                    >
                    <button
                      class="link"
                      :disabled="acting || !canOperate"
                      type="button"
                      @click="openIgnore(row)"
                      >忽略</button
                    >
                  </template>
                  <template v-else-if="row.status === 'ACCEPTED'">
                    <button
                      class="link"
                      :disabled="!canOperate"
                      type="button"
                      @click="openConvert(row)"
                      >转工单</button
                    >
                  </template>
                  <template v-else-if="row.status === 'CONVERTED'">
                    <button
                      class="link"
                      type="button"
                      @click="openOrder(row.convertedOrderId ?? null)"
                      >查看工单</button
                    >
                  </template>
                  <template v-else>
                    <span
                      class="dim"
                      :title="
                        row.reviewedAt
                          ? `审核时间 ${fmt(row.reviewedAt)}`
                          : undefined
                      "
                      >{{ fmt(row.reviewedAt) }}</span
                    >
                  </template>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
        <div class="tbl-foot">
          <span class="dim"
            >共 {{ sug.total }} 条 · 第 {{ sug.page }} / {{ sugTotalPages }}
            页</span
          >
          <span class="pager">
            <button
              :disabled="sug.page <= 1 || sug.loading"
              type="button"
              @click="loadSuggestions(sug.page - 1)"
            >
              上一页
            </button>
            <button
              :disabled="sug.page >= sugTotalPages || sug.loading"
              type="button"
              @click="loadSuggestions(sug.page + 1)"
            >
              下一页
            </button>
          </span>
        </div>
        <div v-if="!canOperate" class="dim role-tip">
          当前角色只读（流转操作限管理员/仪控/工艺工程师）
        </div>
      </div>

      <!-- 右：工单时间线卡 -->
      <div class="col">
        <b class="col-title">工单时间线（本回路）</b>
        <div v-if="ord.error" class="error-line">
          {{ ord.error }}
          <button class="link" type="button" @click="loadOrders()">重试</button>
        </div>
        <div v-if="ord.loading && ord.items.length === 0" class="dim pad">
          工单加载中…
        </div>
        <div v-else-if="!latestOrder" class="dim pad">
          暂无处置工单（建议转单或「创建处置项」后出现在此）
        </div>
        <template v-else>
          <div class="tl-card">
            <button
              class="xbadge"
              title="展开工单详情（状态 · 流转 · KPI 前后对比）"
              type="button"
              @click="openOrder(latestOrder?.id ?? null)"
            >
              ↗
            </button>
            <div class="tl-head">
              <b class="mono">{{ latestOrder.orderNo }}</b>
              <span class="tag" :class="orderStatusCls(latestOrder.status)">
                <span class="dot"></span>{{ latestOrder.statusLabel }}
              </span>
              <span class="dim">{{ latestOrder.title }}</span>
            </div>
            <div class="tl">
              <div
                v-for="(node, i) in latestNodes"
                :key="i"
                class="tl-item"
                :class="node.cls"
              >
                <div class="t-h">
                  <b>{{ node.title }}</b>
                  <span class="mono dim time">{{
                    node.ts ? fmt(node.ts) : '—'
                  }}</span>
                </div>
                <div v-if="node.detail" class="t-m">{{ node.detail }}</div>
              </div>
            </div>
            <button
              class="link block"
              type="button"
              @click="openOrder(latestOrder?.id ?? null)"
            >
              工单详情与流转操作 →
            </button>
          </div>

          <!-- 其余工单（当前页） -->
          <div v-if="otherOrders.length > 0" class="ord-list">
            <button
              v-for="o in otherOrders"
              :key="o.id"
              class="ord-row"
              type="button"
              @click="openOrder(o.id)"
            >
              <span class="mono">{{ o.orderNo }}</span>
              <span class="tag" :class="orderStatusCls(o.status)">
                <span class="dot"></span>{{ o.statusLabel }}
              </span>
              <span class="ord-title">{{ o.title }}</span>
              <span class="dim mono">{{ fmt(o.updatedAt) }}</span>
            </button>
          </div>
          <div class="tbl-foot">
            <span class="dim"
              >共 {{ ord.total }} 条 · 第 {{ ord.page }} / {{ ordTotalPages }}
              页</span
            >
            <span class="pager">
              <button
                :disabled="ord.page <= 1 || ord.loading"
                type="button"
                @click="loadOrders(ord.page - 1)"
              >
                上一页
              </button>
              <button
                :disabled="ord.page >= ordTotalPages || ord.loading"
                type="button"
                @click="loadOrders(ord.page + 1)"
              >
                下一页
              </button>
            </span>
          </div>
        </template>
      </div>
    </div>

    <!-- 创建处置项（手工来源） -->
    <OrderCreateModal
      v-model:open="createOpen"
      :loop-id="selectedLoopId"
      :loop-tag-name="loopTagName"
      @created="onCreated"
    />

    <!-- 驳回弹窗（原因必填，终态） -->
    <Modal
      v-model:open="rejectOpen"
      :title="`驳回建议 · ${rejectTarget?.id.slice(0, 8) ?? ''}`"
      cancel-text="取消"
      ok-text="确认驳回"
      width="440px"
      @ok="onReject"
    >
      <p class="modal-tip">驳回为终态（不可重新审核），请填写原因：</p>
      <Textarea
        v-model:value="rejectReason"
        :rows="2"
        placeholder="驳回原因（必填）"
      />
    </Modal>

    <!-- 忽略弹窗（原因必填） -->
    <Modal
      v-model:open="ignoreOpen"
      :title="`忽略建议 · ${ignoreTarget?.id.slice(0, 8) ?? ''}`"
      cancel-text="取消"
      ok-text="确认忽略"
      width="440px"
      @ok="onIgnore"
    >
      <p class="modal-tip">请填写忽略原因（如计划大修一并处理）：</p>
      <Textarea
        v-model:value="ignoreReason"
        :rows="2"
        placeholder="忽略原因（必填）"
      />
    </Modal>

    <!-- 转工单弹窗（单建议；与旧处置工作台 convert 表单同字段） -->
    <Modal
      :confirm-loading="converting"
      :open="convertOpen"
      title="转工单"
      cancel-text="取消"
      ok-text="生成工单"
      width="480px"
      @cancel="convertOpen = false"
      @ok="onConvert"
    >
      <p class="modal-tip">建议：{{ convertTarget?.content ?? '' }}</p>
      <div class="modal-form">
        <div class="frow">
          <label>处置类型</label>
          <Select
            v-model:value="convertForm.actionType"
            :options="ACTION_TYPE_OPTIONS"
            size="small"
            style="width: 240px"
          />
        </div>
        <div class="frow">
          <label>标题</label>
          <Input
            v-model:value="convertForm.title"
            placeholder="可留空由系统生成"
            size="small"
            style="flex: 1"
          />
        </div>
        <div class="frow">
          <label>执行人</label>
          <Input
            v-model:value="convertForm.handler"
            placeholder="可留空"
            size="small"
            style="width: 240px"
          />
        </div>
        <div class="frow">
          <label>计划实施</label>
          <DatePicker
            v-model:value="convertForm.plannedAt"
            size="small"
            style="width: 240px"
          />
        </div>
      </div>
    </Modal>

    <!-- 工单详情抽屉（整件复用 order-detail-drawer） -->
    <OrderDetailDrawer
      v-model:open="detailOpen"
      :can-operate="canOperate"
      :order-id="detailOrderId"
      @updated="onOrderUpdated"
    />
  </div>
</template>

<style scoped>
.wb360-handling {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

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

.cols2 {
  display: grid;
  gap: 12px;
  grid-template-columns: minmax(360px, 1.2fr) minmax(300px, 1fr);
}

@media (max-width: 1100px) {
  .cols2 {
    grid-template-columns: 1fr;
  }
}

.col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.col-title {
  font-size: 13px;
}

.dim {
  color: hsl(var(--muted-foreground) / 80%);
}

.pad {
  padding: 18px 0;
}

.mono {
  font-family: var(--font-mono, monospace);
}

/* 建议表 */
.tbl {
  border-collapse: collapse;
  font-size: 12px;
  width: 100%;
}

.tbl th {
  border-bottom: 1px solid hsl(var(--border));
  color: hsl(var(--muted-foreground));
  font-weight: 500;
  padding: 6px 8px;
  text-align: left;
}

.tbl td {
  border-bottom: 1px solid hsl(var(--border) / 55%);
  padding: 6px 8px;
  vertical-align: top;
}

.sug-content {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sug-meta {
  font-size: 11px;
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

.role-tip {
  font-size: 11px;
}

/* 工单时间线卡 */
.tl-card {
  background: hsl(var(--accent) / 35%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 14px;
  position: relative;
}

.tl-head {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-right: 34px;
}

.tl-head .dim {
  font-size: 12px;
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

.tl {
  border-left: 2px solid hsl(var(--border));
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-left: 6px;
  padding-left: 12px;
}

.tl-item {
  font-size: 12px;
  position: relative;
}

.tl-item::before {
  background: hsl(var(--muted-foreground) / 50%);
  border-radius: 50%;
  content: '';
  height: 7px;
  left: -17.5px;
  position: absolute;
  top: 4px;
  width: 7px;
}

.tl-item.done::before {
  background: hsl(var(--primary));
}

.tl-item.ok::before {
  background: hsl(var(--success));
}

.tl-item.warn::before {
  background: hsl(var(--warning));
}

.tl-item.future {
  color: hsl(var(--muted-foreground) / 75%);
}

.tl-item.future::before {
  background: transparent;
  border: 1px dashed hsl(var(--muted-foreground) / 60%);
}

.t-h {
  align-items: baseline;
  display: flex;
  gap: 10px;
}

.t-h .time {
  font-size: 11px;
}

.t-m {
  color: hsl(var(--muted-foreground));
  font-size: 11px;
  margin-top: 1px;
}

.link.block {
  align-self: flex-start;
}

/* 其余工单行 */
.ord-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ord-row {
  align-items: center;
  background: none;
  border: none;
  border-bottom: 1px dashed hsl(var(--border) / 70%);
  color: hsl(var(--foreground));
  cursor: pointer;
  display: flex;
  font-size: 12px;
  gap: 8px;
  padding: 5px 4px;
  text-align: left;
}

.ord-row:hover {
  background: hsl(var(--accent) / 40%);
}

.ord-title {
  color: hsl(var(--muted-foreground));
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.link {
  background: none;
  border: none;
  color: hsl(var(--primary));
  cursor: pointer;
  font-size: 12px;
  margin-right: 8px;
  padding: 0;
}

.link:hover {
  text-decoration: underline;
}

.link:disabled {
  color: hsl(var(--muted-foreground) / 60%);
  cursor: not-allowed;
  text-decoration: none;
}

.error-line {
  border: 1px solid hsl(var(--destructive) / 35%);
  border-radius: 4px;
  color: hsl(var(--destructive));
  font-size: 12px;
  padding: 8px 12px;
}

.error-line .link {
  color: hsl(var(--destructive));
  margin-left: 8px;
}

/* 弹窗表单 */
.modal-tip {
  color: hsl(var(--muted-foreground));
  font-size: 12px;
  margin: 4px 0 10px;
}

.modal-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.frow {
  align-items: center;
  display: flex;
  gap: 10px;
}

.frow label {
  color: hsl(var(--muted-foreground));
  flex: none;
  font-size: 12px;
  width: 62px;
}

/* tag 徽标（与 DiagSection 同口径） */
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
</style>
