<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S2-LOOP-008 AAS 连接配置页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.5 ~ §2.2.6
 * - 表单展示 AAS 连接配置（endpoint/syncIntervalSeconds/enabled）
 * - "测试连接"按钮调用 POST /aas/config/test
 * - "立即同步"按钮调用 POST /aas/sync
 * - 同步日志/状态展示
 * - Tag 列表表格（分页、搜索、质量码筛选）
 */
import type { AasApi } from '#/api/aas';
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, onUnmounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Badge,
  Button,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Select,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  getAasConfigApi,
  getAasTagsApi,
  testAasConfigApi,
  triggerAasSyncApi,
  updateAasConfigApi,
} from '#/api/aas';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import QualityTag from '#/components/loop/quality-tag.vue';

defineOptions({ name: 'LoopAas' });

// Config form
const configLoading = ref(false);
const configSaving = ref(false);
const configForm = reactive({
  endpoint: '',
  syncIntervalSeconds: 60,
  enabled: false,
});
const lastSyncAt = ref<null | string>(null);
const lastSyncStatus = ref<AasApi.SyncStatus | null>(null);

// Test connection
const testing = ref(false);
const testResult = ref<AasApi.AasConfigTestResult | null>(null);

// Sync
const syncing = ref(false);
/** 同步触发后已经过的秒数（用于 UI 提示与超时判断） */
const syncElapsedSec = ref(0);
/** 同步轮询定时器 */
let syncPollTimer: null | ReturnType<typeof setInterval> = null;
/** 同步耗时计时器（每秒更新 syncElapsedSec） */
let syncElapsedTimer: null | ReturnType<typeof setInterval> = null;
/** 同步最大等待时长（秒），超过则视为超时 */
const SYNC_TIMEOUT_SEC = 90;
/** 同步轮询间隔（毫秒） */
const SYNC_POLL_INTERVAL_MS = 2000;

/** 同步进度提示文案 */
const syncProgressText = computed(() => {
  if (!syncing.value) return '';
  const secs = syncElapsedSec.value;
  if (secs < 5) return '正在触发同步任务…';
  if (secs < 15) return '正在从 AAS 读取 Tag 数据…';
  if (secs < 30) return '正在比对与更新本地 Tag 注册表…';
  return `同步进行中（已耗时 ${secs}s）`;
});

/** 停止同步轮询与计时 */
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

/**
 * 轮询 GET /aas/config，根据 lastSyncStatus 判断同步是否结束。
 * - SUCCESS：提示成功 + 刷新 Tag 列表 + 重置 syncing
 * - FAILED：提示失败 + 重置 syncing
 * - PROCESSING/未知：继续轮询，直到超时
 */
async function pollSyncStatus(triggeredAt: number) {
  const elapsed = Math.floor((Date.now() - triggeredAt) / 1000);
  syncElapsedSec.value = elapsed;

  // 超时保护
  if (elapsed > SYNC_TIMEOUT_SEC) {
    stopSyncPolling();
    syncing.value = false;
    message.warning(
      `同步已超过 ${SYNC_TIMEOUT_SEC}s 未完成，可能仍在后台执行；请稍后刷新页面查看结果`,
    );
    return;
  }

  try {
    const cfg = await getAasConfigApi();
    lastSyncAt.value = cfg.lastSyncAt;
    lastSyncStatus.value = cfg.lastSyncStatus;

    if (cfg.lastSyncStatus === 'SUCCESS') {
      stopSyncPolling();
      syncing.value = false;
      message.success('AAS Tag 同步完成');
      // 刷新 Tag 列表与配置展示
      await Promise.all([loadTags(), loadConfig()]);
    } else if (cfg.lastSyncStatus === 'FAILED') {
      stopSyncPolling();
      syncing.value = false;
      message.error('AAS Tag 同步失败，请检查 AAS 连接或日志');
      // 仍刷新一次配置以拿到最新状态
      await loadConfig();
    }
    // PROCESSING 或 null：继续等下次轮询
  } catch {
    // 轮询本身失败：不立即终止，等待下次轮询或超时
  }
}

// Tag list
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
const tagLastSyncAt = ref<null | string>(null);
const tagSyncStatus = ref<AasApi.SyncStatus | null>(null);

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
  { title: '质量码', dataIndex: 'quality', key: 'quality', width: 110 },
  { title: '最后同步', dataIndex: 'lastSyncAt', key: 'lastSyncAt', width: 180 },
  {
    title: '关联回路',
    dataIndex: 'associatedLoopTagName',
    key: 'associatedLoopTagName',
    width: 160,
  },
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
    // 强制北京时间（UTC+8）
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
  }
}

/** 加载配置 */
async function loadConfig() {
  configLoading.value = true;
  try {
    const data = await getAasConfigApi();
    configForm.endpoint = data.endpoint;
    configForm.syncIntervalSeconds = data.syncIntervalSeconds;
    configForm.enabled = data.enabled;
    lastSyncAt.value = data.lastSyncAt;
    lastSyncStatus.value = data.lastSyncStatus;
  } catch {
    // 错误已由拦截器处理
  } finally {
    configLoading.value = false;
  }
}

/** 保存配置 */
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
  } catch {
    // 错误已由拦截器处理
  } finally {
    configSaving.value = false;
  }
}

/** 测试连接 */
async function handleTestConnection() {
  if (!configForm.endpoint) {
    message.warning('请先填写 AAS Endpoint');
    return;
  }
  testing.value = true;
  testResult.value = null;
  try {
    // 先保存配置再测试
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

/** 立即同步：触发后轮询 lastSyncStatus 直到 SUCCESS/FAILED 或超时 */
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
    lastSyncStatus.value = 'PROCESSING';

    // 启动轮询与计时
    const triggeredAt = Date.now();
    // 立即调用一次（同步触发返回后后端可能尚未设置 PROCESSING）
    pollSyncStatus(triggeredAt);
    syncPollTimer = setInterval(() => {
      pollSyncStatus(triggeredAt);
    }, SYNC_POLL_INTERVAL_MS);
    syncElapsedTimer = setInterval(() => {
      syncElapsedSec.value = Math.floor((Date.now() - triggeredAt) / 1000);
    }, 1000);
  } catch {
    syncing.value = false;
    // 错误已由拦截器处理
  }
}

/** 加载 Tag 列表 */
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
    tagLastSyncAt.value = data.lastSyncAt;
    tagSyncStatus.value = data.syncStatus;
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

onMounted(() => {
  loadConfig();
  loadTags();
});

onUnmounted(() => {
  stopSyncPolling();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="AAS 连接配置"
      subtitle="配置连接、测试连接并查看同步后的 Tag 列表。"
    />
    <div class="mt-4 space-y-4">
      <!-- 同步进度提示（仅在同步进行中显示） -->
      <Alert
        v-if="syncing"
        type="info"
        show-icon
        :message="`AAS Tag 同步进行中（已耗时 ${syncElapsedSec}s）`"
        :description="syncProgressText"
        class="!mb-0"
      >
        <template #action>
          <span class="text-xs text-gray-400">
            最长等待 {{ SYNC_TIMEOUT_SEC }}s，超时后请刷新页面查看结果
          </span>
        </template>
      </Alert>

      <!-- 配置卡片 -->
      <ClpmDataCanvas title="连接配置" :loading="configLoading">
        <Form :model="configForm" layout="vertical">
          <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
            <FormItem label="AAS Endpoint" name="endpoint">
              <Input
                v-model:value="configForm.endpoint"
                placeholder="例如：opc.tcp://192.168.1.100:4840"
              />
            </FormItem>
            <FormItem label="同步周期（秒）" name="syncIntervalSeconds">
              <InputNumber
                v-model:value="configForm.syncIntervalSeconds"
                :min="10"
                :max="3600"
                class="w-full"
              />
            </FormItem>
            <FormItem label="启用同步" name="enabled">
              <Switch v-model:checked="configForm.enabled" />
            </FormItem>
          </div>
          <div class="flex items-center gap-3">
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
            <Button
              v-permission="['ADMIN', 'IC_ENGINEER']"
              :loading="syncing"
              @click="handleSync"
            >
              立即同步
            </Button>
          </div>
        </Form>

        <!-- 测试结果 -->
        <div v-if="testResult" class="mt-4 rounded border p-3">
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

        <!-- 同步状态 -->
        <div class="mt-4 grid grid-cols-2 gap-4 border-t pt-3">
          <div>
            <span class="text-xs text-gray-400">最后同步时间</span>
            <div class="mt-1">{{ formatTime(lastSyncAt) }}</div>
          </div>
          <div>
            <span class="text-xs text-gray-400">最后同步状态</span>
            <div class="mt-1">
              <Badge
                v-if="lastSyncStatus"
                :color="syncStatusMap[lastSyncStatus]?.color"
                :status="syncStatusMap[lastSyncStatus]?.status"
                :text="syncStatusMap[lastSyncStatus]?.label"
              />
              <span v-else>—</span>
            </div>
          </div>
        </div>
      </ClpmDataCanvas>

      <!-- Tag 列表 -->
      <ClpmDataCanvas title="Tag 列表">
        <template #extra>
          <div class="flex items-center gap-2 text-xs text-gray-400">
            <span>同步时间：{{ formatTime(tagLastSyncAt) }}</span>
            <Badge
              v-if="tagSyncStatus"
              :color="syncStatusMap[tagSyncStatus]?.color"
              :status="syncStatusMap[tagSyncStatus]?.status"
              :text="syncStatusMap[tagSyncStatus]?.label"
            />
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
            placeholder="质量码"
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
              {{ record.currentValue ?? '—' }}
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </div>
  </Page>
</template>
