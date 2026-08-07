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
  operation_type: undefined as SystemApi.OperationType | undefined,
  date_range: undefined as [dayjs.Dayjs, dayjs.Dayjs] | undefined,
  page: 1,
  page_size: 20,
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
    dataIndex: 'operated_at',
    key: 'operated_at',
    width: 170,
  },
  {
    title: '用户',
    dataIndex: 'username',
    key: 'username',
    width: 130,
  },
  {
    title: '操作类型',
    dataIndex: 'operation_type',
    key: 'operation_type',
    width: 100,
  },
  {
    title: '资源类型',
    dataIndex: 'resource_type',
    key: 'resource_type',
    width: 110,
  },
  {
    title: '资源 ID',
    dataIndex: 'resource_id',
    key: 'resource_id',
    width: 160,
    ellipsis: true,
  },
  {
    title: 'IP 地址',
    dataIndex: 'ip_address',
    key: 'ip_address',
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
      page_size: query.page_size,
      operation_type: query.operation_type,
    };
    if (query.date_range && query.date_range.length === 2) {
      const [start, end] = query.date_range;
      params.start_time = start.format('YYYY-MM-DD HH:mm:ss');
      params.end_time = end.format('YYYY-MM-DD HH:mm:ss');
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
  query.page_size = pagination.pageSize || 20;
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

function resourceLabel(rt: SystemApi.ResourceType): string {
  return resourceOptions.find((r) => r.value === rt)?.label || rt;
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
          v-model:value="query.operation_type"
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
          pageSize: query.page_size,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: SystemApi.AuditLog) => record.id"
        :scroll="{ x: 1100 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'operated_at'">
            <span class="font-mono text-xs">
              {{ formatTime(record.operated_at) }}
            </span>
          </template>
          <template v-else-if="column.key === 'operation_type'">
            <Tag
              :color="
                operationColorMap[
                  record.operation_type as SystemApi.OperationType
                ]
              "
            >
              {{
                operationLabel(record.operation_type as SystemApi.OperationType)
              }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'resource_type'">
            <Tag color="default">
              {{
                resourceLabel(record.resource_type as SystemApi.ResourceType)
              }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'resource_id'">
            <span class="font-mono text-xs">{{
              record.resource_id || '—'
            }}</span>
          </template>
          <template v-else-if="column.key === 'ip_address'">
            <span class="font-mono text-xs">{{
              record.ip_address || '—'
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
            <span class="font-mono">{{ selectedLog.id }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="操作时间">
            <span class="font-mono">{{
              formatTime(selectedLog.operated_at)
            }}</span>
          </DescriptionsItem>
          <DescriptionsItem label="用户">
            {{ selectedLog.username }}
            <span class="ml-2 text-xs text-gray-400 font-mono">
              ({{ selectedLog.user_id }})
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="IP 地址">
            <span class="font-mono">{{ selectedLog.ip_address || '—' }}</span>
          </DescriptionsItem>
        </Descriptions>

        <Descriptions title="操作信息" bordered :column="1" size="small">
          <DescriptionsItem label="操作类型">
            <Tag :color="operationColorMap[selectedLog.operation_type]">
              {{ operationLabel(selectedLog.operation_type) }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="资源类型">
            <Tag>{{ resourceLabel(selectedLog.resource_type) }}</Tag>
          </DescriptionsItem>
          <DescriptionsItem label="资源 ID">
            <span class="font-mono">{{ selectedLog.resource_id || '—' }}</span>
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
                >{{ formatJsonValue(selectedLog.before_value) }}
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
                >{{ formatJsonValue(selectedLog.after_value) }}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </Drawer>
  </Page>
</template>
