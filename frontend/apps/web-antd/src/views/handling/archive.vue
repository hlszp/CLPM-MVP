<script setup lang="ts">
/**
 * 处置档案页（/handling/archive，Phase 1F 骨架）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.3（v1.1）
 * 回路维度追溯：一行一回路（聚合状态分布/KPI 改善/重开数），
 * 行点击开档案抽屉看跨 run 处置全史。
 * 数据源：GET /handling/loops（Phase 1F 后端待交付，接口就绪前显示空态）。
 */
import type { HandlingApi } from '#/api/handling';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Card, Input, Select, Table, Tag, TreeSelect } from 'ant-design-vue';

import { getHandlingLoopsApi } from '#/api/handling';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import ClpmDataCanvas from '#/components/clpm/data-canvas.vue';
import ClpmPageToolbar from '#/components/clpm/page-toolbar.vue';
import ClpmToolbarButton from '#/components/clpm/toolbar-button.vue';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { IMPORTANCE_LEVEL_LABEL } from '#/constants/clpm-ui';
import { formatLocalTime } from '#/utils/format';

import HandlingArchiveDrawer from './components/handling-archive-drawer.vue';
import { STATUS_COLOR, STATUS_TEXT } from './constants';

const { tableSize, densityLabel, cycleDensity } = useTableDensity('handling-archive');

// ===== 查询 =====
const loading = ref(false);
const items = ref<HandlingApi.LoopAggregateItem[]>([]);
const total = ref(0);

const query = reactive({
  page: 1,
  pageSize: 20,
  plantNodeId: undefined as string | undefined,
  importanceLevel: undefined as number | undefined,
  /** antd Select 不支持 boolean 值，用字符串承载（§8.3 仅看有在途） */
  activeOnly: 'all' as 'active' | 'all',
  sort: 'recent' as 'recent' | 'reopened',
  keyword: '',
});

async function load() {
  loading.value = true;
  try {
    const res = await getHandlingLoopsApi({
      page: query.page,
      pageSize: query.pageSize,
      plantNodeId: query.plantNodeId,
      importanceLevel: query.importanceLevel,
      activeOnly: query.activeOnly === 'active' || undefined,
      sort: query.sort,
      keyword: query.keyword.trim() || undefined,
    });
    items.value = res.items;
    total.value = res.total;
  } catch {
    // /handling/loops 尚未交付（Phase 1F 后端）：骨架期空态
    items.value = [];
    total.value = 0;
  } finally {
    loading.value = false;
  }
}

function handleTableChange(pag: { current?: number; pageSize?: number }) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  load();
}

// ===== 装置树 =====
const plantTreeData = ref<Array<{ children?: any[]; label: string; value: string }>>([]);

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

// ===== 档案抽屉 =====
const drawerOpen = ref(false);
const focusLoop = ref<HandlingApi.LoopAggregateItem | null>(null);

function openArchive(record: HandlingApi.LoopAggregateItem) {
  focusLoop.value = record;
  drawerOpen.value = true;
}

// ===== 工具栏 =====
function handleHelp() {
  showPageHelp({
    title: '处置档案 帮助',
    content: `
      <p><b>定位</b>：回路维度的处置追溯——回答"这回路被处置过几次、每次做了什么、效果如何"。诊断/整定/复核前的回看入口。</p>
      <p><b>KPI 改善</b>：最近一次闭环的处置后评分 − 处置前评分（正=改善，绿；负=恶化，红；无闭环记录显示 —）。</p>
      <p><b>排序</b>：最近处置（默认）/ 重开最多（问题回路 Top，反复出问题的回路优先关注）。</p>
      <p><b>在途</b>：待处置 + 处置中 + 验证中 > 0 的回路。</p>
    `,
  });
}

// ===== 渲染辅助 =====
const columns = [
  { dataIndex: 'loopTagName', title: '回路', width: 150 },
  { dataIndex: 'unitPath', title: '装置.单元', width: 160 },
  { dataIndex: 'totalCount', title: '累计处置', width: 80 },
  { dataIndex: 'counts', title: '状态分布', width: 260 },
  { dataIndex: 'lastClosedKpiDelta', title: 'KPI 改善', width: 100 },
  { dataIndex: 'lastHandledAt', title: '最近处置', width: 110 },
  { dataIndex: 'lastHandledBy', title: '最近处置人', width: 110 },
];

type StatusKey = keyof HandlingApi.LoopAggregateItem['counts'];
const STATUS_KEYS: Array<{ key: StatusKey; status: HandlingApi.Status }> = [
  { key: 'pending', status: 'PENDING' },
  { key: 'handling', status: 'HANDLING' },
  { key: 'verifying', status: 'VERIFYING' },
  { key: 'closed', status: 'CLOSED' },
  { key: 'reopened', status: 'REOPENED' },
  { key: 'ignored', status: 'IGNORED' },
];

function fmtKpiDelta(v: null | number | undefined): string {
  return typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(1)}` : '—';
}

const fmt = (ts: null | string | undefined) => formatLocalTime(ts, 'MM-DD HH:mm');

onMounted(() => {
  // TODO(Phase 1F): 消费 route.query.loopId（统计页 Top 回路跳转定位，
  // /loops 聚合接口交付后按 loopId 精确定位并自动开档案抽屉）
  load();
  loadPlantTree();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="回路维度处置追溯 · 累计处置 / 状态分布 / KPI 改善"
      title="处置档案"
    >
      <template #actions>
        <ClpmToolbarButton
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          icon="ant-design:column-height-outlined"
          @click="cycleDensity"
        />
        <ClpmToolbarButton icon="ant-design:question-circle-outlined" label="帮助" @click="handleHelp" />
        <ClpmToolbarButton icon="ant-design:sync-outlined" label="刷新" @click="load()" />
      </template>
    </ClpmPageToolbar>

    <!-- 筛选行（§8.3） -->
    <div class="mb-3 mt-2 flex flex-wrap items-center gap-3">
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
      <Select
        v-model:value="query.activeOnly"
        :options="[
          { label: '全部回路', value: 'all' },
          { label: '仅看有在途', value: 'active' },
        ]"
        style="width: 120px"
        @change="((query.page = 1), load())"
      />
      <Select
        v-model:value="query.sort"
        :options="[
          { label: '最近处置', value: 'recent' },
          { label: '重开最多', value: 'reopened' },
        ]"
        style="width: 120px"
        @change="((query.page = 1), load())"
      />
      <Input
        v-model:value="query.keyword"
        allow-clear
        placeholder="回路位号"
        style="width: 160px"
        @press-enter="((query.page = 1), load())"
      />
    </div>

    <Card :body-style="{ padding: '0' }" size="small">
      <ClpmDataCanvas :empty="!loading && items.length === 0" empty-text="暂无回路处置档案">
        <Table
          :columns="columns"
          :custom-row="
            (record: HandlingApi.LoopAggregateItem) => ({
              onClick: () => openArchive(record),
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
          row-key="loopId"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'loopTagName'">
              <div class="flex flex-col">
                <span class="font-medium">{{ record.loopTagName }}</span>
                <span v-if="record.importanceLevel" class="text-xs text-neutral-500">
                  {{ IMPORTANCE_LEVEL_LABEL[record.importanceLevel] }}
                </span>
              </div>
            </template>
            <template v-else-if="column.dataIndex === 'unitPath'">
              {{ record.unitPath ?? '—' }}
            </template>
            <template v-else-if="column.dataIndex === 'counts'">
              <!-- 状态分布：仅显示计数 > 0 的状态 tag -->
              <span
                v-for="s in STATUS_KEYS.filter((k) => record.counts?.[k.key] > 0)"
                :key="s.key"
                class="mr-1"
              >
                <Tag :color="STATUS_COLOR[s.status]">
                  {{ STATUS_TEXT[s.status] }} {{ record.counts[s.key] }}
                </Tag>
              </span>
              <span v-if="!record.counts || STATUS_KEYS.every((k) => !record.counts[k.key])">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'lastClosedKpiDelta'">
              <span
                :class="
                  record.lastClosedKpiDelta > 0
                    ? 'text-emerald-600'
                    : record.lastClosedKpiDelta < 0
                      ? 'text-rose-600'
                      : ''
                "
              >
                {{ fmtKpiDelta(record.lastClosedKpiDelta) }}
              </span>
            </template>
            <template v-else-if="column.dataIndex === 'lastHandledAt'">
              {{ fmt(record.lastHandledAt ?? record.lastSuggestedAt) }}
            </template>
            <template v-else-if="column.dataIndex === 'lastHandledBy'">
              {{ record.lastHandledBy ?? '—' }}
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </Card>

    <HandlingArchiveDrawer v-model:open="drawerOpen" :loop="focusLoop" />
  </Page>
</template>
