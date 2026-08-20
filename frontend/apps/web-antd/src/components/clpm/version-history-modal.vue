<script setup lang="ts">
/**
 * ClpmVersionHistoryModal — 版本历史弹窗（配置类 Tab 内嵌版本查看）
 *
 * 展示版本号（当前生效徽章）、生效时间—失效时间、操作人、变更说明，
 * 支持回滚到历史版本（回滚生成新版本号，保留完整追溯链）。
 *
 * 与指标定义/定级阈值/数据可信度/权重模板等 sys_config 版本化配置共用。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { MetricApi } from '#/api/metric';

import { computed, ref } from 'vue';

import { Modal, Table, Tag, Tooltip } from 'ant-design-vue';

import { ClpmDangerConfirmModal } from '#/components/clpm';
import { formatTime } from '#/utils/format';

interface Props {
  /** 弹窗开关（v-model:open） */
  open: boolean;
  /** 弹窗标题 */
  title: string;
  /** 版本列表（含当前版本，倒序） */
  items: MetricApi.VersionHistoryItem[];
  loading?: boolean;
  /** 回滚执行中 */
  rollingBack?: boolean;
  /** 是否提供回滚操作（调用方按权限控制） */
  rollbackable?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  rollingBack: false,
  rollbackable: true,
});

const emit = defineEmits<{
  (e: 'rollback', version: number): void;
  (e: 'update:open', value: boolean): void;
}>();

const columns: TableColumnsType = [
  { title: '版本号', dataIndex: 'version', key: 'version', width: 100 },
  { title: '生效时间', dataIndex: 'effectiveAt', key: 'effectiveAt', width: 160 },
  { title: '失效时间', dataIndex: 'expiresAt', key: 'expiresAt', width: 160 },
  { title: '操作人', dataIndex: 'updatedBy', key: 'updatedBy', width: 90 },
  { title: '变更说明', dataIndex: 'remark', key: 'remark', ellipsis: true },
  { title: '操作', key: 'action', width: 110 },
];

const open = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
});

/** 回滚确认弹窗 */
const rollbackTarget = ref<null | number>(null);
const rollbackConfirmOpen = ref(false);

function requestRollback(item: MetricApi.VersionHistoryItem) {
  if (item.isCurrent) return;
  rollbackTarget.value = item.version;
  rollbackConfirmOpen.value = true;
}

function confirmRollback() {
  if (rollbackTarget.value !== null) {
    emit('rollback', rollbackTarget.value);
  }
  rollbackConfirmOpen.value = false;
}

/** 回滚按钮提示文案 */
function rollbackTip(item: MetricApi.VersionHistoryItem): string {
  if (item.isCurrent) return '当前生效版本，无需回滚';
  if (item.version === 0) return '回滚到出厂默认值';
  return `将版本 ${item.version} 的配置以新版本号重新生效`;
}
</script>

<template>
  <Modal
    v-model:open="open"
    :title="title"
    :footer="null"
    width="780px"
  >
    <Table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="false"
      :row-key="
        (record: MetricApi.VersionHistoryItem) => String(record.version)
      "
      size="small"
      :scroll="{ y: 360 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'version'">
          <span class="font-mono">v{{ record.version }}</span>
          <Tag v-if="record.isCurrent" color="green" class="ml-1"> 当前 </Tag>
          <Tag v-else-if="record.version === 0" class="ml-1"> 默认 </Tag>
        </template>
        <template v-else-if="column.key === 'effectiveAt'">
          <span class="text-xs">
            {{ record.effectiveAt ? formatTime(record.effectiveAt) : '—' }}
          </span>
        </template>
        <template v-else-if="column.key === 'expiresAt'">
          <span v-if="record.isCurrent" class="text-xs" style="color: hsl(var(--status-ok))">
            生效中
          </span>
          <span v-else class="text-xs">
            {{ record.expiresAt ? formatTime(record.expiresAt) : '—' }}
          </span>
        </template>
        <template v-else-if="column.key === 'updatedBy'">
          <span class="text-xs">{{ record.updatedBy ?? '—' }}</span>
        </template>
        <template v-else-if="column.key === 'remark'">
          <span class="text-xs">{{ record.remark ?? '—' }}</span>
        </template>
        <template v-else-if="column.key === 'action'">
          <Tooltip
            v-if="rollbackable"
            :title="rollbackTip(record as MetricApi.VersionHistoryItem)"
          >
            <span
              role="button"
              class="text-xs"
              :style="
                record.isCurrent
                  ? { color: '#999', cursor: 'not-allowed' }
                  : { color: 'hsl(var(--primary))', cursor: 'pointer' }
              "
              @click="requestRollback(record as MetricApi.VersionHistoryItem)"
            >
              回滚到此版本
            </span>
          </Tooltip>
          <span v-else class="text-xs" style="color: #999">—</span>
        </template>
      </template>
    </Table>

    <!-- 回滚二次确认 -->
    <ClpmDangerConfirmModal
      v-model:open="rollbackConfirmOpen"
      title="确认回滚"
      action="回滚"
      :target="`v${rollbackTarget}`"
      impact-scope="将以新版本号恢复该配置并立即生效，当前版本自动归档到历史。"
      :loading="rollingBack"
      @confirm="confirmRollback"
    />
  </Modal>
</template>
