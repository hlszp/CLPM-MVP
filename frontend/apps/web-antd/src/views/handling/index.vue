<script setup lang="ts">
/**
 * 处置清单页（/handling，§8.2）
 *
 * 页型：执行闭环管理器——回答"哪些建议要干、谁在干、效果如何"。
 * 框架：顶部统计卡（点击即筛选）→ 状态 tabs + 筛选行 → 表格 → 行点击开详情抽屉。
 * 深链接：/handling?focus={id} 自动打开对应详情抽屉（诊断侧「去处置」跳转，§8.4）。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import { Card, Input, Select, Table, Tag, TreeSelect } from 'ant-design-vue';

import { getHandlingItemsApi, getHandlingStatsApi } from '#/api/handling';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { IMPORTANCE_LEVEL_LABEL } from '#/constants/clpm-ui';
import { formatLocalTime } from '#/utils/format';

import HandlingDetailDrawer from './components/handling-detail-drawer.vue';
import {
  ACTION_TYPE_OPTIONS,
  SOURCE_TEXT,
  STATUS_COLOR,
  STATUS_TAB_OPTIONS,
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

// ===== 统计卡 =====
const stats = ref<HandlingApi.Stats | null>(null);

async function loadStats() {
  try {
    stats.value = await getHandlingStatsApi();
  } catch {
    stats.value = null;
  }
}

const statCards = computed(() => [
  {
    key: 'PENDING' as const,
    label: '待处置',
    value: stats.value?.counts.PENDING ?? 0,
    color: '#fa8c16',
  },
  {
    key: 'HANDLING' as const,
    label: '处置中',
    value: stats.value?.counts.HANDLING ?? 0,
    color: '#1677ff',
  },
  {
    key: 'VERIFYING' as const,
    label: '验证中',
    value: stats.value?.counts.VERIFYING ?? 0,
    color: '#13c2c2',
  },
  {
    key: 'MONTH' as const,
    label: '本月闭环',
    value: stats.value?.monthClosed ?? 0,
    color: '#52c41a',
  },
]);

function clickStatCard(key: string) {
  query.statusTab = key === 'MONTH' ? 'CLOSED' : (key as HandlingApi.Status);
  query.page = 1;
  load();
}

// ===== 清单查询 =====
const loading = ref(false);
const items = ref<HandlingApi.ListItem[]>([]);
const total = ref(0);

const query = reactive({
  page: 1,
  pageSize: 20,
  statusTab: '' as '' | HandlingApi.Status,
  actionType: undefined as HandlingApi.ActionType | undefined,
  source: undefined as HandlingApi.Source | undefined,
  plantNodeId: undefined as string | undefined,
  importanceLevel: undefined as number | undefined,
  keyword: '',
});

async function load() {
  loading.value = true;
  try {
    const res = await getHandlingItemsApi({
      page: query.page,
      pageSize: query.pageSize,
      status: query.statusTab || undefined,
      actionType: query.actionType,
      source: query.source,
      plantNodeId: query.plantNodeId,
      importanceLevel: query.importanceLevel,
      keyword: query.keyword.trim() || undefined,
    });
    items.value = res.items;
    total.value = res.total;
  } finally {
    loading.value = false;
  }
}

function refreshAll() {
  load();
  loadStats();
}

function handleTableChange(pag: { current?: number; pageSize?: number }) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  load();
}

// ===== 装置树（plantNodeId 递归下钻筛选） =====
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

// ===== 详情抽屉 =====
const drawerOpen = ref(false);
const focusItemId = ref<null | string>(null);

function openDetail(record: HandlingApi.ListItem) {
  focusItemId.value = record.id;
  drawerOpen.value = true;
}

function handleDrawerUpdated() {
  refreshAll();
}

/** 深链接：/handling?focus={id} 自动开抽屉（诊断侧「去处置」跳转） */
function applyUrlContext() {
  const focus = route.query.focus as string | undefined;
  if (focus) {
    focusItemId.value = focus;
    drawerOpen.value = true;
  }
}

// ===== 工具栏 =====
function handleHelp() {
  showPageHelp({
    title: '处置 帮助',
    content: `
      <p><b>定位</b>：诊断建议的执行闭环管理器——跟踪"谁在什么时候做了什么、效果如何、何时关闭"。</p>
      <p><b>状态机</b>：待处置 → 处置中 → 验证中 → 已闭环（终态）；验证无效 → 重开（可再次处置）；待处置 → 已忽略（终态）。已闭环不可重开，复发走重新诊断。</p>
      <p><b>KPI 验证窗口</b>：前窗=开始处置前 24h（基线），后窗=提交验证后 24h（效果），验证时服务端固化快照。</p>
      <p><b>参数整定</b>：记录式闭环——人工填写调整前后 P/I/D，平台不下写 DCS。</p>
      <p><b>排序规则</b>：待处置 → 重开 → 处置中 → 验证中 → 其他，同组按最近更新倒序。</p>
    `,
  });
}

// ===== 表格列 =====
const columns = [
  { dataIndex: 'loopTagName', title: '回路', width: 140 },
  { dataIndex: 'unitPath', title: '装置.单元', width: 150 },
  { dataIndex: 'actionTypeLabel', title: '类型', width: 90 },
  { dataIndex: 'content', title: '建议摘要', ellipsis: true },
  { dataIndex: 'source', title: '来源', width: 88 },
  { dataIndex: 'status', title: '状态', width: 88 },
  { dataIndex: 'suggestedByAt', title: '建议人/时间', width: 150 },
  { dataIndex: 'handledBy', title: '处置人', width: 100 },
  { dataIndex: 'updatedAt', title: '最近更新', width: 110 },
];

const fmt = (ts: null | string | undefined) =>
  formatLocalTime(ts, 'MM-DD HH:mm');

onMounted(() => {
  refreshAll();
  loadPlantTree();
  applyUrlContext();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="诊断建议执行闭环 · 开始处置 → 提交验证 → 效果验证 → 闭环"
      title="处置"
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

    <!-- 顶部统计卡（点击即筛选，§8.2） -->
    <div class="mb-3 mt-2 grid grid-cols-4 gap-3">
      <Card
        v-for="c in statCards"
        :key="c.key"
        :body-style="{ padding: '10px 16px', cursor: 'pointer' }"
        size="small"
        @click="clickStatCard(c.key)"
      >
        <div class="flex items-baseline justify-between">
          <span class="text-xs text-neutral-500">{{ c.label }}</span>
          <span :style="{ color: c.color }" class="text-xl font-semibold">{{
            c.value
          }}</span>
        </div>
      </Card>
    </div>

    <!-- 状态 tabs + 筛选行 -->
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <Select
        v-model:value="query.statusTab"
        :options="STATUS_TAB_OPTIONS"
        style="width: 110px"
        @change="((query.page = 1), load())"
      />
      <Select
        v-model:value="query.actionType"
        :allow-clear="true"
        :options="ACTION_TYPE_OPTIONS"
        placeholder="处置类型"
        style="width: 130px"
        @change="((query.page = 1), load())"
      />
      <Select
        v-model:value="query.source"
        :allow-clear="true"
        :options="[
          { label: '系统建议', value: 'SYSTEM' },
          { label: '人工新增', value: 'MANUAL' },
        ]"
        placeholder="来源"
        style="width: 110px"
        @change="((query.page = 1), load())"
      />
      <TreeSelect
        v-model:value="query.plantNodeId"
        :allow-clear="true"
        :tree-data="plantTreeData"
        :tree-default-expanded-keys="plantTreeData.map((n) => n.value)"
        placeholder="装置"
        style="width: 180px"
        tree-node-filter-prop="label"
        @change="((query.page = 1), load())"
      />
      <Select
        v-model:value="query.importanceLevel"
        :allow-clear="true"
        :options="[
          { label: '1 级（关键）', value: 1 },
          { label: '2 级（重要）', value: 2 },
          { label: '3 级（一般）', value: 3 },
        ]"
        placeholder="回路等级"
        style="width: 130px"
        @change="((query.page = 1), load())"
      />
      <Input
        v-model:value="query.keyword"
        allow-clear
        placeholder="回路位号/建议内容"
        style="width: 180px"
        @press-enter="((query.page = 1), load())"
      />
    </div>

    <Card :body-style="{ padding: '0' }" size="small">
      <ClpmDataCanvas
        :empty="!loading && items.length === 0"
        empty-text="暂无待处置建议"
      >
        <Table
          :columns="columns"
          :custom-row="
            (record: HandlingApi.ListItem) => ({
              onClick: () => openDetail(record),
              style: { cursor: 'pointer' },
            })
          "
          :data-source="items"
          :loading="loading"
          :pagination="{
            current: query.page,
            pageSize: query.pageSize,
            showSizeChanger: true,
            pageSizeOptions: ['10', '20', '50'],
            showTotal: (t: number) => `共 ${t} 条`,
            total,
          }"
          :size="tableSize"
          row-key="id"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'loopTagName'">
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
            <template v-else-if="column.dataIndex === 'unitPath'">
              {{ record.unitPath ?? '—' }}
            </template>
            <template v-else-if="column.dataIndex === 'actionTypeLabel'">
              {{ record.actionTypeLabel ?? '—' }}
            </template>
            <template v-else-if="column.dataIndex === 'source'">
              {{ SOURCE_TEXT[record.source as HandlingApi.Source] }}
            </template>
            <template v-else-if="column.dataIndex === 'status'">
              <Tag :color="STATUS_COLOR[record.status as HandlingApi.Status]">
                {{ record.statusLabel }}
              </Tag>
            </template>
            <template v-else-if="column.dataIndex === 'suggestedByAt'">
              {{ record.suggestedBy }} · {{ fmt(record.suggestedAt) }}
            </template>
            <template v-else-if="column.dataIndex === 'handledBy'">
              {{ record.handledBy ?? '—' }}
            </template>
            <template v-else-if="column.dataIndex === 'updatedAt'">
              {{ fmt(record.updatedAt) }}
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </Card>

    <HandlingDetailDrawer
      v-model:open="drawerOpen"
      :can-operate="canOperate"
      :item-id="focusItemId"
      @updated="handleDrawerUpdated"
    />
  </Page>
</template>
