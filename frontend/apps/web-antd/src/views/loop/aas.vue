<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S2-LOOP-008 AAS Tag 同步状态页（v5.3 重写）
 *
 * 对齐 UI/UX v5.3 §6.2.0 + FDS §5.2.1
 * - 顶部同步状态卡片区（3 张横排）：服务状态 / 最近同步时间 / 同步统计
 * - 同步操作区（仅 ADMIN 可见）：手动触发 / 同步周期配置 / 同步日志
 * - Tag 列表区（增强）：筛选栏 + 表格 + Bad 质量高亮
 * - 质量分布饼图（右下角悬浮卡片）：Good/Bad/Uncertain
 * - 同步服务异常时顶部全宽红色警告横幅
 * - Bad 质量 tag 数量超过阈值（>10%）时同步统计卡片闪烁告警
 */
import type { AasApi } from '#/api/aas';
import type { LoopApi } from '#/api/loop';

import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Badge,
  Button,
  Card,
  Drawer,
  message,
  Select,
  Switch,
  Table,
  Tag,
  Input,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getAasConfigApi,
  getAasTagsApi,
  getSyncLogsApi,
  getSyncStatusApi,
  testAasConfigApi,
  triggerAasSyncApi,
  updateAasConfigApi,
} from '#/api/aas';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import QualityTag from '#/components/loop/quality-tag.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'LoopAas' });

const router = useRouter();
const { themeColors } = useClpmTheme();

// ===== 同步状态 =====
const syncStatusLoading = ref(false);
const syncStatus = ref<AasApi.SyncStatusResult | null>(null);
/** 同步服务是否异常（lastSyncStatus === 'FAILED' 或同步未启用） */
const syncAbnormal = computed(() => {
  if (!syncStatus.value) return false;
  if (!syncStatus.value.enabled) return false; // 已停用不算异常
  return syncStatus.value.lastSyncStatus === 'FAILED';
});

/** 服务状态信息 */
const serviceStatus = computed<{ color: string; label: string; pulse: boolean }>(() => {
  if (!syncStatus.value || !syncStatus.value.enabled) {
    return { color: 'default', label: '已停止', pulse: false };
  }
  if (syncStatus.value.lastSyncStatus === 'FAILED') {
    return { color: 'red', label: '异常', pulse: false };
  }
  return { color: 'green', label: '运行中', pulse: true };
});

/** 最近同步时间距今文案 */
const lastSyncAgo = computed(() => {
  const t = syncStatus.value?.lastSyncAt;
  if (!t) return '尚未同步';
  const diff = dayjs().diff(dayjs(t), 'minute');
  if (diff < 1) return '刚刚';
  if (diff < 60) return `${diff} 分钟前`;
  const hours = Math.floor(diff / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
});

/** Bad 质量 Tag 数量占比是否超阈值（>10%） */
const badQualityOverThreshold = computed(() => {
  const stats = syncStatus.value?.tagStats;
  if (!stats || stats.total === 0) return false;
  const bad = stats.byQuality?.BAD ?? 0;
  return bad / stats.total > 0.1;
});

// ===== 同步操作（手动触发）=====
const syncing = ref(false);
const syncElapsedSec = ref(0);
let syncPollTimer: null | ReturnType<typeof setInterval> = null;
let syncElapsedTimer: null | ReturnType<typeof setInterval> = null;
const SYNC_TIMEOUT_SEC = 90;
const SYNC_POLL_INTERVAL_MS = 2000;

const syncProgressText = computed(() => {
  if (!syncing.value) return '';
  const secs = syncElapsedSec.value;
  if (secs < 5) return '正在触发同步任务…';
  if (secs < 15) return '正在从 AAS 读取 Tag 数据…';
  if (secs < 30) return '正在比对与更新本地 Tag 注册表…';
  return `同步进行中（已耗时 ${secs}s）`;
});

function stopSyncPolling() {
  if (syncPollTimer) {
    clearInterval(syncPollTimer);
    syncPollTimer = null;
  }
  if (syncElapsedTimer) {
    clearInterval(syncElapsedTimer);
    syncElapsedTimer = null;
  }
}

/** 轮询同步状态 */
async function pollSyncStatus(triggeredAt: number) {
  const elapsed = Math.floor((Date.now() - triggeredAt) / 1000);
  syncElapsedSec.value = elapsed;

  if (elapsed > SYNC_TIMEOUT_SEC) {
    stopSyncPolling();
    syncing.value = false;
    message.warning(
      `同步已超过 ${SYNC_TIMEOUT_SEC}s 未完成，可能仍在后台执行；请稍后刷新页面查看结果`,
    );
    return;
  }

  try {
    const status = await getSyncStatusApi();
    syncStatus.value = status;
    if (status.lastSyncStatus === 'SUCCESS') {
      stopSyncPolling();
      syncing.value = false;
      message.success('AAS Tag 同步完成');
      await Promise.all([loadTags()]);
    } else if (status.lastSyncStatus === 'FAILED') {
      stopSyncPolling();
      syncing.value = false;
      message.error('AAS Tag 同步失败，请检查 AAS 连接或日志');
    }
  } catch {
    // 轮询本身失败：不立即终止
  }
}

/** 手动触发同步 */
async function handleSync() {
  if (syncing.value) {
    message.info('同步任务进行中，请稍候');
    return;
  }
  syncing.value = true;
  syncElapsedSec.value = 0;
  try {
    const task = await triggerAasSyncApi();
    message.info(`同步任务已触发（任务 ID：${task.taskId}），正在后台执行…`);
    const triggeredAt = Date.now();
    pollSyncStatus(triggeredAt);
    syncPollTimer = setInterval(() => {
      pollSyncStatus(triggeredAt);
    }, SYNC_POLL_INTERVAL_MS);
    syncElapsedTimer = setInterval(() => {
      syncElapsedSec.value = Math.floor((Date.now() - triggeredAt) / 1000);
    }, 1000);
  } catch {
    syncing.value = false;
  }
}

// ===== 同步日志抽屉 =====
const logDrawerVisible = ref(false);
const logLoading = ref(false);
const logList = ref<AasApi.SyncLog[]>([]);
const logTotal = ref(0);

async function loadSyncLogs() {
  logLoading.value = true;
  try {
    const data = await getSyncLogsApi({ page: 1, pageSize: 10 });
    logList.value = data.items;
    logTotal.value = data.total;
  } catch {
    // 错误已由拦截器处理
  } finally {
    logLoading.value = false;
  }
}

function openLogDrawer() {
  logDrawerVisible.value = true;
  loadSyncLogs();
}

// ===== 同步周期配置跳转 =====
function goEngineConfig() {
  router.push('/metric/engine-config');
}

// ===== 连接配置（保留，折叠到次级抽屉）=====
const configDrawerVisible = ref(false);
const configLoading = ref(false);
const configSaving = ref(false);
const testing = ref(false);
const testResult = ref<AasApi.AasConfigTestResult | null>(null);
const configForm = reactive({
  endpoint: '',
  syncIntervalSeconds: 60,
  enabled: false,
});

async function loadConfig() {
  configLoading.value = true;
  try {
    const data = await getAasConfigApi();
    configForm.endpoint = data.endpoint;
    configForm.syncIntervalSeconds = data.syncIntervalSeconds;
    configForm.enabled = data.enabled;
  } catch {
    // 错误已由拦截器处理
  } finally {
    configLoading.value = false;
  }
}

async function handleSaveConfig() {
  configSaving.value = true;
  try {
    await updateAasConfigApi({
      endpoint: configForm.endpoint,
      syncIntervalSeconds: configForm.syncIntervalSeconds,
      enabled: configForm.enabled,
    });
    message.success('配置保存成功');
    await loadConfig();
    await loadSyncStatus();
  } catch {
    // 错误已由拦截器处理
  } finally {
    configSaving.value = false;
  }
}

async function handleTestConnection() {
  if (!configForm.endpoint) {
    message.warning('请先填写 AAS Endpoint');
    return;
  }
  testing.value = true;
  testResult.value = null;
  try {
    await updateAasConfigApi({
      endpoint: configForm.endpoint,
      syncIntervalSeconds: configForm.syncIntervalSeconds,
      enabled: configForm.enabled,
    });
    const result = await testAasConfigApi();
    testResult.value = result;
    if (result.success) {
      message.success(`连接成功，延迟 ${result.latencyMs}ms`);
    } else {
      message.error(`连接失败：${result.message}`);
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    testing.value = false;
  }
}

function openConfigDrawer() {
  configDrawerVisible.value = true;
  loadConfig();
}

// ===== Tag 列表 =====
const tagLoading = ref(false);
const tagList = ref<AasApi.AasTag[]>([]);
const tagTotal = ref(0);
const tagQuery = reactive({
  keyword: '',
  quality: undefined as 'BAD' | 'GOOD' | 'UNCERTAIN' | undefined,
  associated: undefined as 'associated' | 'unassociated' | undefined,
  page: 1,
  pageSize: 20,
});

const qualityOptions = [
  { label: '全部', value: undefined },
  { label: 'Good', value: 'GOOD' },
  { label: 'Bad', value: 'BAD' },
  { label: 'Uncertain', value: 'UNCERTAIN' },
];

const associatedOptions = [
  { label: '全部', value: undefined },
  { label: '已关联', value: 'associated' },
  { label: '未关联', value: 'unassociated' },
];

const columns: TableColumnsType = [
  { title: 'Tag 位号', dataIndex: 'tagName', key: 'tagName', width: 200 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  {
    title: '当前值',
    dataIndex: 'currentValue',
    key: 'currentValue',
    width: 100,
  },
  { title: '数据质量', dataIndex: 'quality', key: 'quality', width: 120 },
  {
    title: '关联回路',
    dataIndex: 'associatedLoopTagName',
    key: 'associatedLoopTagName',
    width: 160,
  },
  { title: '最后同步', dataIndex: 'lastSyncAt', key: 'lastSyncAt', width: 180 },
  { title: '操作', key: 'action', width: 100, fixed: 'right' },
];

const syncStatusMap: Record<
  string,
  {
    color: string;
    label: string;
    status: 'default' | 'error' | 'processing' | 'success';
  }
> = {
  FAILED: { color: 'red', label: '失败', status: 'error' },
  PROCESSING: { color: 'blue', label: '进行中', status: 'processing' },
  SUCCESS: { color: 'green', label: '成功', status: 'success' },
};

function formatTime(t: null | string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

async function loadSyncStatus() {
  syncStatusLoading.value = true;
  try {
    syncStatus.value = await getSyncStatusApi();
    await nextTick();
    renderQualityDonut();
  } catch {
    // 错误已由拦截器处理
  } finally {
    syncStatusLoading.value = false;
  }
}

async function loadTags() {
  tagLoading.value = true;
  try {
    const data = await getAasTagsApi({
      keyword: tagQuery.keyword || undefined,
      quality: tagQuery.quality,
      associated:
        tagQuery.associated === undefined
          ? undefined
          : tagQuery.associated === 'associated',
      page: tagQuery.page,
      pageSize: tagQuery.pageSize,
    });
    tagList.value = data.items;
    tagTotal.value = data.total;
  } catch {
    // 错误已由拦截器处理
  } finally {
    tagLoading.value = false;
  }
}

function handleSearch() {
  tagQuery.page = 1;
  loadTags();
}

function handleTableChange(pagination: TablePaginationConfig) {
  tagQuery.page = pagination.current || 1;
  tagQuery.pageSize = pagination.pageSize || 20;
  loadTags();
}

/** 行样式：Bad 质量行底色淡红 */
function rowClassName(record: AasApi.AasTag): string {
  return record.quality === 'BAD' ? 'aas-tag-row-bad' : '';
}

/** 跳转关联回路详情 */
function handleViewLoop(loopId: null | string) {
  if (!loopId) return;
  router.push(`/loop/detail/${loopId}`);
}

// ===== 质量分布饼图 =====
const qualityChartRef = ref();
const { renderEcharts: drawQualityChart } = useEcharts(qualityChartRef);

function renderQualityDonut() {
  const stats = syncStatus.value?.tagStats;
  if (!stats) return;
  const good = stats.byQuality?.GOOD ?? 0;
  const bad = stats.byQuality?.BAD ?? 0;
  const uncertain = stats.byQuality?.UNCERTAIN ?? 0;

  drawQualityChart({
    color: [themeColors.value.SUCCESS, themeColors.value.DANGER, '#faad14'],
    series: [
      {
        type: 'pie',
        radius: ['52%', '74%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: false,
        data: [
          { name: 'Good', value: good },
          { name: 'Bad', value: bad },
          { name: 'Uncertain', value: uncertain },
        ],
        label: {
          show: true,
          position: 'center',
          formatter: `{a|${stats.total}}\n{b|Tag 总数}`,
          rich: {
            a: {
              color: themeColors.value.SUCCESS,
              fontSize: 20,
              fontWeight: 700,
              lineHeight: 26,
            },
            b: { color: '#999', fontSize: 11, lineHeight: 16 },
          },
        },
        labelLine: { show: false },
        itemStyle: {
          borderColor: '#fff',
          borderWidth: 2,
        },
      },
    ],
    tooltip: {
      trigger: 'item',
      formatter: '{b}: {c} ({d}%)',
    },
    legend: {
      bottom: 0,
      icon: 'circle',
      itemHeight: 8,
      itemWidth: 8,
      data: ['Good', 'Bad', 'Uncertain'],
      textStyle: { fontSize: 11 },
    },
  });
}

/** 点击饼图扇区过滤 Tag 列表 */
function handleQualityChartClick(params: any) {
  const name = params?.name as string | undefined;
  if (!name) return;
  const qualityMap: Record<string, 'BAD' | 'GOOD' | 'UNCERTAIN'> = {
    Bad: 'BAD',
    Good: 'GOOD',
    Uncertain: 'UNCERTAIN',
  };
  const q = qualityMap[name];
  if (q) {
    tagQuery.quality = q;
    handleSearch();
    message.info(`已按 ${name} 质量过滤 Tag 列表`);
  }
}

// ===== 60 秒轮询同步状态 =====
let statusTimer: null | ReturnType<typeof setInterval> = null;
const STATUS_POLL_INTERVAL = 60 * 1000;

function startStatusPolling() {
  stopStatusPolling();
  statusTimer = setInterval(() => {
    loadSyncStatus();
  }, STATUS_POLL_INTERVAL);
}

function stopStatusPolling() {
  if (statusTimer) {
    clearInterval(statusTimer);
    statusTimer = null;
  }
}

onMounted(() => {
  loadSyncStatus();
  loadTags();
  startStatusPolling();
});

onUnmounted(() => {
  stopSyncPolling();
  stopStatusPolling();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="AAS Tag 同步状态"
      subtitle="监控 AAS Tag 同步服务运行状态、同步结果与 Tag 列表（v5.3 重写）"
    />

    <!-- 同步服务异常时顶部全宽红色警告横幅 -->
    <Alert
      v-if="syncAbnormal"
      class="mt-3"
      type="error"
      show-icon
      message="AAS 同步服务异常，回路监控数据可能滞后"
      description="最近一次同步失败，请检查 AAS 连接配置或查看同步日志排查原因。"
    />

    <!-- 同步进度提示（仅在同步进行中显示） -->
    <Alert
      v-if="syncing"
      class="mt-3"
      type="info"
      show-icon
      :message="`AAS Tag 同步进行中（已耗时 ${syncElapsedSec}s）`"
      :description="syncProgressText"
    >
      <template #action>
        <span class="text-xs text-gray-400">
          最长等待 {{ SYNC_TIMEOUT_SEC }}s，超时后请刷新页面查看结果
        </span>
      </template>
    </Alert>

    <!-- 顶部同步状态卡片区（3 张横排） -->
    <div class="aas-status-grid mt-3">
      <!-- 同步服务状态 -->
      <Card size="small" :loading="syncStatusLoading">
        <div class="text-xs text-gray-500">同步服务状态</div>
        <div class="mt-2 flex items-center gap-2">
          <span
            class="aas-status-dot"
            :class="{
              'aas-status-dot--green': serviceStatus.color === 'green',
              'aas-status-dot--red': serviceStatus.color === 'red',
              'aas-status-dot--gray': serviceStatus.color === 'default',
              'aas-status-dot--pulse': serviceStatus.pulse,
            }"
          ></span>
          <span class="text-lg font-semibold">{{ serviceStatus.label }}</span>
        </div>
        <div class="mt-2 text-xs text-gray-400">
          <Switch
            v-model:checked="configForm.enabled"
            size="small"
            @change="handleSaveConfig"
          />
          <span class="ml-2">{{ configForm.enabled ? '已启用' : '已停用' }}</span>
        </div>
      </Card>

      <!-- 最近同步时间 -->
      <Card size="small" :loading="syncStatusLoading">
        <div class="text-xs text-gray-500">最近同步时间</div>
        <div class="clpm-num mt-2 text-lg font-semibold font-mono">
          {{ lastSyncAgo }}
        </div>
        <div class="mt-2 text-xs text-gray-400">
          {{ formatTime(syncStatus?.lastSyncAt ?? null) }}
        </div>
        <div v-if="syncStatus?.lastSyncStatus" class="mt-1">
          <Badge
            :color="syncStatusMap[syncStatus.lastSyncStatus]?.color"
            :status="syncStatusMap[syncStatus.lastSyncStatus]?.status"
            :text="syncStatusMap[syncStatus.lastSyncStatus]?.label"
          />
        </div>
      </Card>

      <!-- 同步统计 -->
      <Card
        size="small"
        :loading="syncStatusLoading"
        :class="{ 'aas-stats-card-alert': badQualityOverThreshold }"
      >
        <div class="text-xs text-gray-500">同步统计</div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="clpm-num text-2xl font-bold">
            {{ syncStatus?.tagStats?.total ?? 0 }}
          </span>
          <span class="text-xs text-gray-400">Tag 总数</span>
        </div>
        <div class="mt-2 flex flex-wrap gap-2 text-xs">
          <Tag color="green" class="m-0">
            Good {{ syncStatus?.tagStats?.byQuality?.GOOD ?? 0 }}
          </Tag>
          <Tag color="red" class="m-0">
            Bad {{ syncStatus?.tagStats?.byQuality?.BAD ?? 0 }}
          </Tag>
          <Tag color="orange" class="m-0">
            Uncertain {{ syncStatus?.tagStats?.byQuality?.UNCERTAIN ?? 0 }}
          </Tag>
          <Tag color="blue" class="m-0">
            已关联 {{ syncStatus?.tagStats?.linked ?? 0 }}
          </Tag>
        </div>
        <div v-if="badQualityOverThreshold" class="mt-2 text-xs text-red-500">
          ⚠ Bad 质量 Tag 占比超过 10%，请检查数据源
        </div>
      </Card>
    </div>

    <!-- 同步操作区（仅 ADMIN 可见） -->
    <Card size="small" class="mt-3">
      <div class="flex flex-wrap items-center gap-3">
        <span class="text-sm font-medium">同步操作：</span>
        <Button
          v-permission="['ADMIN']"
          type="primary"
          :loading="syncing"
          @click="handleSync"
        >
          手动触发同步
        </Button>
        <Button v-permission="['ADMIN']" @click="goEngineConfig">
          同步周期配置
        </Button>
        <Button @click="openLogDrawer">
          同步日志
        </Button>
        <Button @click="openConfigDrawer">
          连接配置
        </Button>
      </div>
    </Card>

    <!-- Tag 列表区 + 质量分布饼图 -->
    <div class="aas-main-grid mt-3">
      <ClpmDataCanvas title="Tag 列表" :loading="tagLoading">
        <template #extra>
          <div class="flex items-center gap-2 text-xs text-gray-400">
            <span>同步时间：{{ formatTime(syncStatus?.lastSyncAt ?? null) }}</span>
          </div>
        </template>

        <!-- 筛选区 -->
        <div class="mb-4 flex flex-wrap items-center gap-3">
          <Input
            v-model:value="tagQuery.keyword"
            placeholder="搜索 Tag 位号/描述"
            allow-clear
            style="width: 240px"
            @press-enter="handleSearch"
          />
          <Select
            v-model:value="tagQuery.quality"
            :options="qualityOptions"
            placeholder="数据质量"
            style="width: 140px"
            allow-clear
            @change="handleSearch"
          />
          <Select
            v-model:value="tagQuery.associated"
            :options="associatedOptions"
            placeholder="关联状态"
            style="width: 140px"
            allow-clear
            @change="handleSearch"
          />
          <Button type="primary" @click="handleSearch">查询</Button>
        </div>

        <Table
          :columns="columns"
          :data-source="tagList"
          :loading="tagLoading"
          :pagination="{
            current: tagQuery.page,
            pageSize: tagQuery.pageSize,
            total: tagTotal,
            showSizeChanger: true,
            showTotal: (t: number) => `共 ${t} 条`,
          }"
          :row-key="(record: LoopApi.LoopTagDetail) => record.tagId ?? ''"
          :row-class-name="rowClassName"
          size="middle"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'quality'">
              <QualityTag :quality="record.quality" />
            </template>
            <template v-else-if="column.key === 'lastSyncAt'">
              {{ formatTime(record.lastSyncAt) }}
            </template>
            <template v-else-if="column.key === 'associatedLoopTagName'">
              <Tag v-if="record.associatedLoopTagName" color="blue">
                {{ record.associatedLoopTagName }}
              </Tag>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'currentValue'">
              <span class="clpm-num">{{ record.currentValue ?? '—' }}</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <Button
                v-if="record.associatedLoopId"
                type="link"
                size="small"
                @click="handleViewLoop(record.associatedLoopId)"
              >
                查看回路
              </Button>
              <span v-else class="text-gray-400">—</span>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>

      <!-- 质量分布饼图（右下角悬浮卡片） -->
      <Card size="small" title="质量分布" class="aas-quality-card">
        <EchartsUI
          ref="qualityChartRef"
          height="240px"
          @click="handleQualityChartClick"
        />
      </Card>
    </div>

    <!-- 同步日志抽屉 -->
    <Drawer
      v-model:open="logDrawerVisible"
      title="同步日志（最近 10 次）"
      width="640"
      placement="right"
    >
      <Table
        :columns="[
          { title: '时间', dataIndex: 'operatedAt', key: 'operatedAt', width: 160 },
          { title: '操作', dataIndex: 'operationType', key: 'operationType', width: 100 },
          { title: '操作人', dataIndex: 'operator', key: 'operator', width: 100 },
          { title: '变更前', dataIndex: 'beforeValue', key: 'beforeValue', ellipsis: true },
          { title: '变更后', dataIndex: 'afterValue', key: 'afterValue', ellipsis: true },
        ]"
        :data-source="logList"
        :loading="logLoading"
        :pagination="false"
        :row-key="(record: AasApi.SyncLog) => record.id"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'operatedAt'">
            {{ formatTime(record.operatedAt) }}
          </template>
        </template>
      </Table>
      <div class="mt-3 text-xs text-gray-400">共 {{ logTotal }} 条记录</div>
    </Drawer>

    <!-- 连接配置抽屉 -->
    <Drawer
      v-model:open="configDrawerVisible"
      title="AAS 连接配置"
      width="480"
      placement="right"
    >
      <div class="space-y-4">
        <div>
          <label class="mb-1 block text-sm">AAS Endpoint</label>
          <Input
            v-model:value="configForm.endpoint"
            placeholder="例如：opc.tcp://192.168.1.100:4840"
          />
        </div>
        <div>
          <label class="mb-1 block text-sm">同步周期（秒）</label>
          <Input
            v-model:value="configForm.syncIntervalSeconds"
            :min="10"
            :max="3600"
            type="number"
          />
        </div>
        <div class="flex items-center gap-2">
          <label class="text-sm">启用同步</label>
          <Switch v-model:checked="configForm.enabled" />
        </div>
        <div class="flex gap-2">
          <Button
            v-permission="['ADMIN', 'IC_ENGINEER']"
            type="primary"
            :loading="configSaving"
            @click="handleSaveConfig"
          >
            保存配置
          </Button>
          <Button :loading="testing" @click="handleTestConnection">
            测试连接
          </Button>
        </div>
        <!-- 测试结果 -->
        <div v-if="testResult" class="rounded border p-3">
          <div class="flex items-center gap-2">
            <Tag :color="testResult.success ? 'green' : 'red'">
              {{ testResult.success ? '成功' : '失败' }}
            </Tag>
            <span v-if="testResult.success">
              延迟：{{ testResult.latencyMs }}ms
            </span>
            <span v-else class="text-red-500">{{ testResult.message }}</span>
          </div>
        </div>
      </div>
    </Drawer>
  </Page>
</template>

<style scoped>
.aas-status-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.aas-main-grid {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 12px;
}

.aas-quality-card {
  display: flex;
  flex-direction: column;
}

/* 同步服务状态点 */
.aas-status-dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 9999px;
}

.aas-status-dot--green {
  background-color: #52c41a;
}

.aas-status-dot--red {
  background-color: #f5222d;
}

.aas-status-dot--gray {
  background-color: #bfbfbf;
}

.aas-status-dot--pulse {
  animation: aas-pulse 1.6s ease-in-out infinite;
}

@keyframes aas-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgb(82 196 26 / 60%);
  }
  50% {
    box-shadow: 0 0 0 6px rgb(82 196 26 / 0%);
  }
}

/* Bad 质量 Tag 行底色淡红 */
:deep(.aas-tag-row-bad) td {
  background-color: rgb(245 34 45 / 6%) !important;
}

/* Bad 质量超阈值时同步统计卡片闪烁告警 */
.aas-stats-card-alert {
  animation: aas-card-flash 1.5s ease-in-out infinite;
  border-color: #f5222d !important;
}

@keyframes aas-card-flash {
  0%,
  100% {
    background-color: rgb(245 34 45 / 4%);
  }
  50% {
    background-color: rgb(245 34 45 / 12%);
  }
}

@media (max-width: 1024px) {
  .aas-status-grid {
    grid-template-columns: 1fr;
  }

  .aas-main-grid {
    grid-template-columns: 1fr;
  }
}
</style>
