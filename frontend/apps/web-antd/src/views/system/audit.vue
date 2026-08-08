<script lang="ts" setup>
/**
 * S5-SYS-005 审计日志页
 *
 * 对齐 IDS v3.2 §2.6 + PRD §4.6 + UI/UX v4.1 §6.6.2
 * - 筛选栏（用户/操作类型/时间范围）
 * - 表格展示日志列表（时间/用户/操作/资源/详情）
 * - 详情抽屉展示变更前后值对比（JSON）
 * - 仅查看，无编辑功能
 * - 仅 ADMIN 可见
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { SystemApi } from '#/api/system';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  DatePicker,
  Descriptions,
  DescriptionsItem,
  Drawer,
  Select,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getAuditLogListApi } from '#/api/system';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'SystemAudit' });

const loading = ref(false);
const auditList = ref<SystemApi.AuditLog[]>([]);
const total = ref(0);

const query = reactive({
  operationType: undefined as SystemApi.OperationType | undefined,
  date_range: undefined as [dayjs.Dayjs, dayjs.Dayjs] | undefined,
  page: 1,
  pageSize: 20,
});

/** 操作类型选项 */
const operationOptions: { label: string; value: SystemApi.OperationType }[] = [
  { label: '创建', value: 'CREATE' },
  { label: '更新', value: 'UPDATE' },
  { label: '删除', value: 'DELETE' },
  { label: '登录', value: 'LOGIN' },
  { label: '登出', value: 'LOGOUT' },
];

/** 操作类型颜色映射 */
const operationColorMap: Record<SystemApi.OperationType, string> = {
  CREATE: 'green',
  DELETE: 'red',
  LOGIN: 'cyan',
  LOGOUT: 'default',
  UPDATE: 'blue',
};

/** 资源类型选项 */
const resourceOptions: { label: string; value: SystemApi.ResourceType }[] = [
  { label: '用户', value: 'USER' },
  { label: '回路', value: 'LOOP' },
  { label: '指标', value: 'METRIC' },
  { label: '诊断', value: 'DIAGNOSIS' },
  { label: '报表', value: 'REPORT' },
];

const columns: TableColumnsType = [
  {
    title: '时间',
    dataIndex: 'operatedAt',
    key: 'operatedAt',
    width: 170,
  },
  {
    title: '用户',
    dataIndex: 'operator',
    key: 'operator',
    width: 130,
  },
  {
    title: '操作类型',
    dataIndex: 'operationType',
    key: 'operationType',
    width: 100,
  },
  {
    title: '资源类型',
    dataIndex: 'targetType',
    key: 'targetType',
    width: 110,
  },
  {
    title: '资源 ID',
    dataIndex: 'targetId',
    key: 'targetId',
    width: 160,
    ellipsis: true,
  },
  {
    title: 'IP 地址',
    dataIndex: 'clientIp',
    key: 'clientIp',
    width: 140,
  },
  { title: '操作', key: 'action', width: 90, fixed: 'right' },
];

// 详情抽屉
const drawerVisible = ref(false);
const selectedLog = ref<null | SystemApi.AuditLog>(null);

/** 加载审计日志列表 */
async function loadList() {
  loading.value = true;
  try {
    const params: SystemApi.AuditLogListQueryParams = {
      page: query.page,
      pageSize: query.pageSize,
      operationType: query.operationType,
    };
    if (query.date_range && query.date_range.length === 2) {
      const [start, end] = query.date_range;
      params.startTime = start.format('YYYY-MM-DD HH:mm:ss');
      params.endTime = end.format('YYYY-MM-DD HH:mm:ss');
    }
    const data = await getAuditLogListApi(params);
    auditList.value = data.items || [];
    total.value = data.total || 0;
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

/** 打开详情抽屉 */
function handleViewDetail(record: SystemApi.AuditLog) {
  selectedLog.value = record;
  drawerVisible.value = true;
}

function operationLabel(op: SystemApi.OperationType): string {
  return operationOptions.find((o) => o.value === op)?.label || op;
}

function resourceLabel(rt?: null | string): string {
  if (!rt) return '—';
  // 后端 targetType 为首字母大写（如 "User"），统一转大写后映射
  const upper = rt.toUpperCase();
  return resourceOptions.find((r) => r.value === upper)?.label || rt;
}

/** 格式化 JSON 值用于展示 */
function formatJsonValue(value: unknown): string {
  if (value === undefined || value === null) return '—';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

onMounted(() => {
  loadList();
});

/** 工具栏刷新：重新加载审计日志列表 */
function handleRefresh() {
  loadList();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '审计日志 帮助',
    content:
      '审计日志页：按操作类型、时间范围和资源类型筛选查看系统关键变更记录（创建/更新/删除/登录/登出）。点击「查看详情」可在抽屉中查看变更前后值对比（JSON）。仅 ADMIN 可见，数据只读不可编辑。刷新按钮重新拉取当前筛选条件下的日志列表。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="审计日志"
      subtitle="按操作类型、时间和资源查看关键变更记录。"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <ClpmDataCanvas class="mt-4" title="审计日志列表" :loading="loading">
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="query.operationType"
          placeholder="操作类型"
          style="width: 140px"
          allow-clear
          :options="operationOptions"
          @change="handleSearch"
        />
        <DatePicker.RangePicker
          v-model:value="query.date_range"
          style="width: 360px"
          show-time
          format="YYYY-MM-DD HH:mm:ss"
          :placeholder="['开始时间', '结束时间']"
          @change="handleSearch"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>

      <Table
        :columns="columns"
        :data-source="auditList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: SystemApi.AuditLog) => record.logId"
        :scroll="{ x: 1100 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'operatedAt'">
            <span class="font-mono text-xs">
              {{ formatTime(record.operatedAt) }}
            </span>
          </template>
          <template v-else-if="column.key === 'operationType'">
            <Tag
              :color="
                operationColorMap[
                  record.operationType as SystemApi.OperationType
                ]
              "
            >
              {{
                operationLabel(record.operationType as SystemApi.OperationType)
              }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'targetType'">
            <Tag color="default">
              {{ resourceLabel(record.targetType) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'targetId'">
            <span class="font-mono text-xs">{{
              record.targetId || '—'
            }}</span>
          </template>
          <template v-else-if="column.key === 'clientIp'">
            <span class="font-mono text-xs">{{
              record.clientIp || '—'
            }}</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              type="link"
              size="small"
              @click="handleViewDetail(record as SystemApi.AuditLog)"
            >
              查看详情
            </Button>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 详情抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      title="审计日志详情"
      width="640px"
      placement="right"
    >
      <div v-if="selectedLog" class="space-y-4">
        <Descriptions title="基本信息" bordered :column="1" size="small">
          <DescriptionsItem label="日志 ID">
            <span class="font-mono">{{ selectedLog.logId }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="操作时间">
            <span class="font-mono">{{
              formatTime(selectedLog.operatedAt)
            }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="用户">
            {{ selectedLog.operator }}
          </DescriptionsItem>
          <DescriptionsItem label="IP 地址">
            <span class="font-mono">{{ selectedLog.clientIp || '—' }}</span>
          </DescriptionsItem>
        </Descriptions>

        <Descriptions title="操作信息" bordered :column="1" size="small">
          <DescriptionsItem label="操作类型">
            <Tag :color="operationColorMap[selectedLog.operationType]">
              {{ operationLabel(selectedLog.operationType) }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="资源类型">
            <Tag>{{ resourceLabel(selectedLog.targetType) }}</Tag>
          </DescriptionsItem>
          <DescriptionsItem label="资源 ID">
            <span class="font-mono">{{ selectedLog.targetId || '—' }}</span>
          </DescriptionsItem>
        </Descriptions>

        <!-- 变更前后值对比 -->
        <div>
          <div class="mb-2 font-medium">变更前后值对比</div>
          <div class="grid grid-cols-2 gap-3">
            <div class="rounded border border-gray-200">
              <div
                class="border-b border-gray-200 bg-gray-50 px-3 py-2 text-sm font-medium"
              >
                变更前
              </div>
              <pre
                class="max-h-80 overflow-auto p-3 text-xs font-mono whitespace-pre-wrap break-all"
                >{{ formatJsonValue(selectedLog.beforeValue) }}
              </pre>
            </div>
            <div class="rounded border border-gray-200">
              <div
                class="border-b border-gray-200 bg-gray-50 px-3 py-2 text-sm font-medium"
              >
                变更后
              </div>
              <pre
                class="max-h-80 overflow-auto p-3 text-xs font-mono whitespace-pre-wrap break-all"
                >{{ formatJsonValue(selectedLog.afterValue) }}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </Drawer>
  </Page>
</template>
