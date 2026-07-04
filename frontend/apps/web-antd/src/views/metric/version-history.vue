<script lang="ts" setup>
/**
 * 权重模板版本历史（P5-T3）
 *
 * 对齐 UI/UX v5.3 §6.1.4 + FDS v5.1 §5.2.2
 * - 版本列表数据表：版本号 / 变更类型 / 变更内容摘要 / 操作人 / 变更时间 / 操作（回滚到此处）
 * - 按版本号降序排列
 * - 当前生效版本以绿色"生效中"徽章标识
 * - 回滚操作：二次确认弹窗
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { onMounted, ref } from 'vue';

import { Button, message, Modal, Table, Tag } from 'ant-design-vue';

import { ClpmToolbarButton } from '#/components/clpm';
import {
  getWeightTemplateHistoryApi,
  rollbackWeightTemplateApi,
} from '#/api/metric';

defineOptions({ name: 'MetricVersionHistory' });

const loading = ref(false);
const rollingBack = ref<number | null>(null);
const list = ref<MetricApi.VersionHistoryItem[]>([]);
const currentVersion = ref<number | undefined>(undefined);

const columns: TableColumnsType = [
  { title: '版本号', dataIndex: 'version', key: 'version', width: 120 },
  {
    title: '变更类型',
    dataIndex: 'changeType',
    key: 'changeType',
    width: 140,
  },
  {
    title: '变更内容摘要',
    dataIndex: 'remark',
    key: 'remark',
    ellipsis: true,
  },
  {
    title: '操作人',
    dataIndex: 'updatedBy',
    key: 'updatedBy',
    width: 140,
  },
  {
    title: '变更时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 180,
  },
  {
    title: '操作',
    key: 'action',
    width: 140,
    fixed: 'right',
    align: 'center',
  },
];

/** 推断变更类型 */
function inferChangeType(item: MetricApi.VersionHistoryItem): string {
  if (item.version === 1) return '初始版本';
  if (item.remark?.includes('回滚')) return '回滚';
  if (item.remark?.includes('恢复默认') || item.remark?.includes('国标'))
    return '恢复默认';
  return '编辑';
}

/** 加载版本历史 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getWeightTemplateHistoryApi();
    list.value = data.items ?? [];
    currentVersion.value = data.currentVersion;
    // 按版本号降序
    list.value.sort((a, b) => b.version - a.version);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 回滚确认 */
function handleRollback(item: MetricApi.VersionHistoryItem) {
  if (item.isCurrent) {
    message.info('当前版本已是生效版本，无需回滚');
    return;
  }
  Modal.confirm({
    title: '确认回滚到该版本',
    content: `确认将权重模板回滚到版本 v${item.version}？回滚后将生成新版本，原版本历史保留。`,
    okText: '确认回滚',
    cancelText: '取消',
    okType: 'danger',
    onOk: async () => {
      rollingBack.value = item.version;
      try {
        await rollbackWeightTemplateApi(item.version);
        message.success(`已回滚到 v${item.version}（生成新版本生效）`);
        await loadList();
      } catch {
        // 错误已由拦截器处理
      } finally {
        rollingBack.value = null;
      }
    },
  });
}

onMounted(() => {
  loadList();
});
</script>

<template>
  <div class="metric-version-history">
    <div class="mb-3 flex items-center justify-between">
      <p class="text-sm text-gray-500">
        权重模板的版本变更历史。支持回滚到任意历史版本（回滚操作将生成新版本，原历史保留）。
      </p>
      <ClpmToolbarButton
        icon="ant-design:reload-outlined"
        :loading="loading"
        label="刷新"
        @click="loadList"
      />
    </div>

    <Table
      :columns="columns"
      :data-source="list"
      :loading="loading"
      :pagination="false"
      :row-key="(record: MetricApi.VersionHistoryItem) => record.version"
      :scroll="{ x: 900 }"
      size="middle"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'version'">
          <span class="font-mono font-medium">v{{ record.version }}</span>
          <Tag
            v-if="record.isCurrent || record.version === currentVersion"
            color="green"
            class="ml-2"
          >
            生效中
          </Tag>
        </template>
        <template v-else-if="column.key === 'changeType'">
          <Tag
            :color="
              inferChangeType(record as MetricApi.VersionHistoryItem) === '回滚'
                ? 'orange'
                : 'blue'
            "
          >
            {{ inferChangeType(record as MetricApi.VersionHistoryItem) }}
          </Tag>
        </template>
        <template v-else-if="column.key === 'remark'">
          <span v-if="record.remark">{{ record.remark }}</span>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'updatedBy'">
          <span v-if="record.updatedBy">{{ record.updatedBy }}</span>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'updatedAt'">
          <span v-if="record.updatedAt" class="font-mono text-xs">
            {{ record.updatedAt }}
          </span>
          <span v-else class="text-gray-400">—</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <Button
            v-permission="['ADMIN']"
            type="link"
            size="small"
            :disabled="record.isCurrent || record.version === currentVersion"
            :loading="rollingBack === record.version"
            @click="handleRollback(record as MetricApi.VersionHistoryItem)"
          >
            回滚到此处
          </Button>
        </template>
      </template>
    </Table>

    <div class="mt-3 text-xs text-gray-500">
      <p>
        <strong>说明：</strong>
        每次保存权重模板将生成新版本，旧版本不可删除。
        回滚操作会基于历史版本创建新版本号，原版本记录保留以供审计。
      </p>
    </div>
  </div>
</template>
