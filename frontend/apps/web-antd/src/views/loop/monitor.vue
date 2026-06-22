<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S2-LOOP-011 回路监控列表页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.15
 * - Table 展示回路实时状态（位号/PV/SP/OP/MODE/质量码/评分/状态）
 * - PV 质量码渲染：Good 绿 / Bad 红虚线 / Uncertain 黄
 * - 点击行跳转回路详情页 /loop/detail/:id
 * - 支持按装置/状态筛选
 * - 30 秒自动刷新（可配置开关）
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Input,
  Select,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import { getLoopMonitorListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import QualityTag from '#/components/loop/quality-tag.vue';
import StatusBadge from '#/components/loop/status-badge.vue';

defineOptions({ name: 'LoopMonitor' });

const router = useRouter();

const loading = ref(false);
const monitorList = ref<LoopApi.MonitorListItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 20,
});

const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

// Auto refresh
const autoRefresh = ref(true);
const refreshInterval = 30; // seconds
let refreshTimer: null | ReturnType<typeof setInterval> = null;

const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 140 },
  { title: 'PV', key: 'pv', width: 120 },
  { title: 'SP', key: 'sp', width: 100 },
  { title: 'OP', key: 'op', width: 100 },
  { title: 'MODE', key: 'mode', width: 100 },
  { title: 'PV 质量', key: 'pvQuality', width: 110 },
  { title: '评分', dataIndex: 'score', key: 'score', width: 80 },
  { title: '状态', key: 'status', width: 110 },
  { title: '读取时间', dataIndex: 'readAt', key: 'readAt', width: 170 },
];

/** 扁平化工厂节点树 */
function flattenNodes(
  nodes: PlantNodeApi.PlantNode[],
  result: PlantNodeApi.PlantNode[] = [],
): PlantNodeApi.PlantNode[] {
  for (const node of nodes) {
    result.push(node);
    if (node.children) {
      flattenNodes(node.children, result);
    }
  }
  return result;
}

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载监控列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getLoopMonitorListApi({
      plantNodeId: query.plantNodeId,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    monitorList.value = data.items;
    total.value = data.total;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

/** 点击行跳转详情 */
function handleRowClick(record: LoopApi.MonitorListItem) {
  router.push(`/loop/detail/${record.loopId}`);
}

function formatTime(t: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

/** 启动自动刷新 */
function startAutoRefresh() {
  stopAutoRefresh();
  if (autoRefresh.value) {
    refreshTimer = setInterval(() => {
      loadList();
    }, refreshInterval * 1000);
  }
}

/** 停止自动刷新 */
function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

/** 切换自动刷新 */
function handleToggleAutoRefresh(val: any) {
  autoRefresh.value = !!val;
  if (autoRefresh.value) {
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
}

onMounted(() => {
  loadPlantNodes();
  loadList();
  startAutoRefresh();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <Page title="回路监控">
    <Card>
      <!-- 筛选区 -->
      <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap items-center gap-3">
          <Select
            v-model:value="query.plantNodeId"
            placeholder="按装置/单元筛选"
            style="width: 220px"
            allow-clear
            :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
            @change="handleSearch"
          />
          <Input
            v-model:value="query.keyword"
            placeholder="搜索位号/描述"
            allow-clear
            style="width: 240px"
            @press-enter="handleSearch"
          />
          <Button type="primary" @click="handleSearch">查询</Button>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">
            自动刷新（{{ refreshInterval }}s）
          </span>
          <Switch :checked="autoRefresh" @change="handleToggleAutoRefresh" />
          <Button size="small" :loading="loading" @click="loadList">
            手动刷新
          </Button>
        </div>
      </div>

      <Table
        :columns="columns"
        :data-source="monitorList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: any) => record.loopId"
        :scroll="{ x: 1300 }"
        size="middle"
        :custom-row="
          (record: any) => ({
            onClick: () => handleRowClick(record),
            style: { cursor: 'pointer' },
          })
        "
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'pv'">
            <span class="font-medium text-blue-600">
              {{ record.currentValues.pv ?? '—' }}
            </span>
          </template>
          <template v-else-if="column.key === 'sp'">
            {{ record.currentValues.sp ?? '—' }}
          </template>
          <template v-else-if="column.key === 'op'">
            {{ record.currentValues.op ?? '—' }}
          </template>
          <template v-else-if="column.key === 'mode'">
            <Tag
              :color="
                record.currentValues.modeLabel === 'Auto'
                  ? 'green'
                  : record.currentValues.modeLabel === 'Manual'
                    ? 'orange'
                    : 'blue'
              "
            >
              {{ record.currentValues.modeLabel || '—' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'pvQuality'">
            <QualityTag :quality="record.currentValues.pvQuality" />
          </template>
          <template v-else-if="column.key === 'status'">
            <StatusBadge :status="record.status" :is-active="record.isActive" />
          </template>
          <template v-else-if="column.key === 'score'">
            <span v-if="record.score != null" class="font-medium">
              {{ record.score.toFixed(1) }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'readAt'">
            {{ formatTime(record.readAt) }}
          </template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
