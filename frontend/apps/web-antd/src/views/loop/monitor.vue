<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S2-LOOP-011 回路监控列表页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.15
 * - 沿用回路台账列表风格（筛选区 + Table + 分页）
 * - Table 列：回路位号 / 描述 / 所属单元 / PV / SP / OP / MODE / PID_P / PID_I / PID_D / PV质量 / 评分 / 状态 / 读取时间 / 操作
 * - 实时值显示：PV/SP/OP 数值、MODE Tag 颜色（Auto 绿 / Manual 橙 / Cascade 蓝）
 * - PV 质量码渲染：Good 绿 / Bad 红虚线 / Uncertain 黄
 * - PID 参数：当前监控列表接口未返回，统一显示 "—" 占位
 * - 点击行跳转回路详情页 /loop/detail/:id
 * - 支持按装置/关键字筛选
 * - 30 秒自动刷新（Switch 开关 + 倒计时）
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
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopMonitor' });

const router = useRouter();

const loading = ref(false);
const monitorList = ref<LoopApi.MonitorListItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 100,
});

const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

// Auto refresh
const autoRefresh = ref(true);
const refreshInterval = 30; // seconds
const countdown = ref(refreshInterval);
let refreshTimer: null | ReturnType<typeof setInterval> = null;
let countdownTimer: null | ReturnType<typeof setInterval> = null;

const columns: TableColumnsType = [
  { title: '回路位号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 140 },
  { title: 'PV', key: 'pv', width: 100 },
  { title: 'SP', key: 'sp', width: 100 },
  { title: 'OP', key: 'op', width: 100 },
  { title: 'MODE', key: 'mode', width: 100 },
  { title: 'PID_P', key: 'pidP', width: 90 },
  { title: 'PID_I', key: 'pidI', width: 90 },
  { title: 'PID_D', key: 'pidD', width: 90 },
  { title: 'PV 质量', key: 'pvQuality', width: 110 },
  { title: '评分', dataIndex: 'score', key: 'score', width: 80 },
  { title: '状态', key: 'status', width: 110 },
  { title: '读取时间', key: 'readAt', width: 170 },
  { title: '操作', key: 'action', width: 100, fixed: 'right' },
];

/** MODE 颜色映射：Auto=绿 / Manual=橙 / Cascade=蓝 */
function modeColor(modeLabel: string): string {
  if (modeLabel === 'Auto') return 'green';
  if (modeLabel === 'Manual') return 'orange';
  if (modeLabel === 'Cascade') return 'blue';
  return 'default';
}

/** MODE 中文标签映射：0=手动, 1=自动, 2=串级 */
function modeText(record: LoopApi.MonitorListItem): string {
  const label = record.currentValues?.modeLabel;
  if (label) return label;
  const mode = record.currentValues?.mode;
  if (mode === 0) return 'Manual';
  if (mode === 1) return 'Auto';
  if (mode === 2) return 'Cascade';
  return '—';
}

/** 数值格式化，空值返回 '—' */
function formatNumber(val: null | number | undefined, digits = 2): string {
  if (val == null || Number.isNaN(val)) return '—';
  return val.toFixed(digits);
}

/** OP 值格式化，带 % 后缀 */
function formatOp(val: null | number | undefined): string {
  if (val == null || Number.isNaN(val)) return '—';
  return `${val.toFixed(2)}%`;
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
  query.pageSize = pagination.pageSize || 100;
  loadList();
}

/** 点击行跳转详情 */
function handleRowClick(record: LoopApi.MonitorListItem) {
  router.push(`/loop/detail/${record.loopId}`);
}

/** 点击查看详情按钮 */
function handleViewDetail(record: LoopApi.MonitorListItem) {
  router.push(`/loop/detail/${record.loopId}`);
}

function formatTime(t: null | string | undefined): string {
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
    countdown.value = refreshInterval;
    refreshTimer = setInterval(() => {
      loadList();
      countdown.value = refreshInterval;
    }, refreshInterval * 1000);
    countdownTimer = setInterval(() => {
      if (countdown.value > 0) countdown.value -= 1;
    }, 1000);
  }
}

/** 停止自动刷新 */
function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
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
          <span
            v-if="autoRefresh"
            class="text-xs text-gray-400"
            style="min-width: 56px"
          >
            {{ countdown }}s 后刷新
          </span>
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
          pageSizeOptions: ['20', '50', '100'],
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: LoopApi.MonitorListItem) => record.loopId"
        :scroll="{ x: 1700 }"
        size="middle"
        :custom-row="
          (record: LoopApi.MonitorListItem) => ({
            onClick: () => handleRowClick(record),
            style: { cursor: 'pointer' },
          })
        "
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'pv'">
            <span class="font-medium text-blue-600">
              {{ formatNumber(record.currentValues?.pv) }}
            </span>
          </template>
          <template v-else-if="column.key === 'sp'">
            {{ formatNumber(record.currentValues?.sp) }}
          </template>
          <template v-else-if="column.key === 'op'">
            {{ formatOp(record.currentValues?.op) }}
          </template>
          <template v-else-if="column.key === 'mode'">
            <Tag
              v-if="record.currentValues?.modeLabel || record.currentValues?.mode != null"
              :color="modeColor(record.currentValues?.modeLabel)"
            >
              {{ modeText(record as LoopApi.MonitorListItem) }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'pidP'">
            <span class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'pidI'">
            <span class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'pidD'">
            <span class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'pvQuality'">
            <QualityTag :quality="record.currentValues?.pvQuality" />
          </template>
          <template v-else-if="column.key === 'status'">
            <StatusBadge :status="record.status" :is-active="record.isActive" />
          </template>
          <template v-else-if="column.key === 'score'">
            <span v-if="record.score != null" class="font-medium">
              {{ record.score?.toFixed(1) ?? '--' }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'readAt'">
            {{ formatTime(record.currentValues?.readAt ?? record.readAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              type="link"
              size="small"
              @click.stop="handleViewDetail(record as LoopApi.MonitorListItem)"
            >
              查看详情
            </Button>
          </template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
