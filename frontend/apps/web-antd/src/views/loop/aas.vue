<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S2-LOOP-008 AAS 连接配置页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.5 ~ §2.2.6
 * - 表单展示 AAS 连接配置（endpoint/syncInterval/enabled）
 * - "测试连接"按钮调用 POST /aas/config/test
 * - "立即同步"按钮调用 POST /aas/sync
 * - 同步日志/状态展示
 * - Tag 列表表格（分页、搜索、质量码筛选）
 */
import type { AasApi } from '#/api/aas';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Badge,
  Button,
  Card,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
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
import QualityTag from '#/components/loop/quality-tag.vue';

defineOptions({ name: 'LoopAas' });

// Config form
const configLoading = ref(false);
const configSaving = ref(false);
const configForm = reactive({
  endpoint: '',
  syncInterval: 60,
  enabled: false,
});
const lastSyncAt = ref<null | string>(null);
const lastSyncStatus = ref<AasApi.SyncStatus | null>(null);

// Test connection
const testing = ref(false);
const testResult = ref<AasApi.AasConfigTestResult | null>(null);

// Sync
const syncing = ref(false);

// Tag list
const tagLoading = ref(false);
const tagList = ref<AasApi.AasTag[]>([]);
const tagTotal = ref(0);
const tagQuery = reactive({
  keyword: '',
  quality: undefined as 'Bad' | 'Good' | 'Uncertain' | undefined,
  associated: undefined as boolean | undefined,
  page: 1,
  pageSize: 20,
});
const tagLastSyncAt = ref<null | string>(null);
const tagSyncStatus = ref<AasApi.SyncStatus | null>(null);

const qualityOptions = [
  { label: '全部', value: undefined },
  { label: 'Good', value: 'Good' },
  { label: 'Bad', value: 'Bad' },
  { label: 'Uncertain', value: 'Uncertain' },
];

const associatedOptions = [
  { label: '全部', value: undefined },
  { label: '已关联', value: true },
  { label: '未关联', value: false },
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
    return new Date(t).toLocaleString('zh-CN');
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
    configForm.syncInterval = data.syncInterval;
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
      syncInterval: configForm.syncInterval,
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
      syncInterval: configForm.syncInterval,
      enabled: configForm.enabled,
    });
    testResult.value = await testAasConfigApi();
    if (testResult.value.success) {
      message.success(`连接成功，延迟 ${testResult.value.latency}ms`);
    } else {
      message.error(`连接失败：${testResult.value.message}`);
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    testing.value = false;
  }
}

/** 立即同步 */
async function handleSync() {
  syncing.value = true;
  try {
    const task = await triggerAasSyncApi();
    message.success(`同步任务已触发，任务 ID：${task.taskId}`);
    // 延迟刷新 Tag 列表
    setTimeout(() => {
      loadTags();
      loadConfig();
    }, 2000);
  } catch {
    // 错误已由拦截器处理
  } finally {
    syncing.value = false;
  }
}

/** 加载 Tag 列表 */
async function loadTags() {
  tagLoading.value = true;
  try {
    const data = await getAasTagsApi({
      keyword: tagQuery.keyword || undefined,
      quality: tagQuery.quality,
      associated: tagQuery.associated,
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
</script>

<template>
  <Page title="AAS 连接配置">
    <div class="space-y-4">
      <!-- 配置卡片 -->
      <Card title="连接配置" :loading="configLoading">
        <Form :model="configForm" layout="vertical">
          <div class="grid grid-cols-1 gap-4 md:grid-cols-3">
            <FormItem label="AAS Endpoint" name="endpoint">
              <Input
                v-model:value="configForm.endpoint"
                placeholder="例如：opc.tcp://192.168.1.100:4840"
              />
            </FormItem>
            <FormItem label="同步周期（秒）" name="syncInterval">
              <InputNumber
                v-model:value="configForm.syncInterval"
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
              延迟：{{ testResult.latency }}ms
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
      </Card>

      <!-- Tag 列表 -->
      <Card title="Tag 列表">
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
          <a-select
            v-model:value="tagQuery.quality"
            :options="qualityOptions"
            placeholder="质量码"
            style="width: 140px"
            allow-clear
            @change="handleSearch"
          />
          <a-select
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
          :row-key="(record: any) => record.tagId"
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
      </Card>
    </div>
  </Page>
</template>
