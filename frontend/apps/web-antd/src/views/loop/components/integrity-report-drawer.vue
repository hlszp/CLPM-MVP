<!--
  CLPM 数据完整性检查报告抽屉

  展示完整性检查结果：
  - 顶部 4 个 KPI 卡片（整体完整度/完整/部分/缺失回路数）
  - 双 Tab 表格（按回路 / 按时间）
  - 底部操作栏：勾选缺失回路后一键补齐（强制 skip 策略）

  父组件通过 @backfill 事件接收选中回路，复用既有 startImportApi。
-->
<script setup lang="ts">
import { computed, h, ref, watch } from 'vue';

import {
  Button,
  Drawer,
  Progress,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { ClpmKpiCard } from '#/components/clpm';
import type { LoopDataApi } from '#/api/loop-data';

interface Props {
  visible: boolean;
  result: LoopDataApi.IntegrityCheckResult | null;
  loading: boolean;
  tsStart: string;
  tsEnd: string;
  expectedInterval: number;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  'update:visible': [visible: boolean];
  backfill: [loopIds: string[], tsStart: string, tsEnd: string];
}>();

const activeTab = ref<'loop' | 'time'>('loop');

// 已选回路（待补齐）
const selectedLoopIds = ref<string[]>([]);

// 默认勾选 PARTIAL + MISSING 回路
watch(
  () => props.result,
  (result) => {
    if (!result) {
      selectedLoopIds.value = [];
      return;
    }
    selectedLoopIds.value = result.loopDetails
      .filter((d) => d.status !== 'COMPLETE')
      .map((d) => d.loopId);
  },
  { immediate: true },
);

const drawerVisible = computed({
  get: () => props.visible,
  set: (val) => emit('update:visible', val),
});

// 整体完整度状态色
const overallStatus = computed<'error' | 'ok' | 'warning'>(() => {
  if (!props.result) return 'error';
  const r = props.result.overallCompleteness;
  if (r >= 0.95) return 'ok';
  if (r >= 0.2) return 'warning';
  return 'error';
});

// 按回路表格列
const loopColumns = computed(() => [
  { title: '位号', dataIndex: 'tagName', key: 'tagName', width: 140, ellipsis: true },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 90,
  },
  {
    title: '完整度',
    dataIndex: 'completeness',
    key: 'completeness',
    width: 160,
  },
  {
    title: '实际/预期点数',
    key: 'points',
    width: 140,
  },
  { title: '缺失小时', dataIndex: 'missingHourCount', key: 'missingHourCount', width: 90 },
  {
    title: '首条时间',
    dataIndex: 'firstTs',
    key: 'firstTs',
    customRender: ({ text }: { text: string | null }) =>
      text ? dayjs(text).format('MM-DD HH:mm') : '—',
  },
  {
    title: '末条时间',
    dataIndex: 'lastTs',
    key: 'lastTs',
    customRender: ({ text }: { text: string | null }) =>
      text ? dayjs(text).format('MM-DD HH:mm') : '—',
  },
]);

// 按时间表格列
const timeColumns = computed(() => [
  {
    title: '时间段',
    key: 'range',
    customRender: ({ record }: { record: LoopDataApi.TimeGap }) =>
      `${dayjs(record.startTs).format('MM-DD HH:mm')} ~ ${dayjs(record.endTs).format('MM-DD HH:mm')}`,
  },
  {
    title: '影响回路数',
    dataIndex: 'affectedLoopCount',
    key: 'affectedLoopCount',
    width: 110,
    sorter: (a: LoopDataApi.TimeGap, b: LoopDataApi.TimeGap) =>
      a.affectedLoopCount - b.affectedLoopCount,
    defaultSortOrder: 'descend' as const,
  },
  {
    title: '影响回路',
    key: 'affectedLoops',
    width: 120,
    customRender: ({ record }: { record: LoopDataApi.TimeGap }) => {
      const count = record.affectedLoopIds.length;
      return h(
        Tooltip,
        { title: record.affectedLoopIds.slice(0, 20).join(', ') + (count > 20 ? '...' : '') },
        () => `${count} 个回路`,
      );
    },
  },
]);

// 表格行选择配置
const rowSelection = computed(() => ({
  selectedRowKeys: selectedLoopIds.value,
  onChange: (keys: (string | number)[]) => {
    selectedLoopIds.value = keys.map(String);
  },
}));

const hasSelected = computed(() => selectedLoopIds.value.length > 0);

function statusTag(status: LoopDataApi.IntegrityStatus) {
  const map: Record<LoopDataApi.IntegrityStatus, { color: string; text: string }> = {
    COMPLETE: { color: 'success', text: '完整' },
    PARTIAL: { color: 'warning', text: '部分' },
    MISSING: { color: 'error', text: '缺失' },
  };
  return map[status] ?? { color: 'default', text: status };
}

function handleBackfill() {
  if (!hasSelected.value) return;
  emit('backfill', selectedLoopIds.value, props.tsStart, props.tsEnd);
}
</script>

<template>
  <Drawer
    v-model:open="drawerVisible"
    title="数据完整性检查报告"
    placement="right"
    :width="1000"
    :body-style="{ paddingBottom: '60px' }"
  >
    <template v-if="result">
      <!-- 顶部摘要区：4 个 KPI 卡片 -->
      <div class="integrity-summary">
        <ClpmKpiCard
          title="整体完整度"
          :value="result.overallCompleteness * 100"
          unit="%"
          :precision="1"
          :status="overallStatus"
          :progress="result.overallCompleteness * 100"
          icon="ant-design:pie-chart-outlined"
        />
        <ClpmKpiCard
          title="完整回路"
          :value="result.completeLoopCount"
          unit="个"
          status="ok"
          icon="ant-design:check-circle-outlined"
        />
        <ClpmKpiCard
          title="部分回路"
          :value="result.partialLoopCount"
          unit="个"
          status="warning"
          icon="ant-design:warning-outlined"
        />
        <ClpmKpiCard
          title="缺失回路"
          :value="result.missingLoopCount"
          unit="个"
          status="error"
          icon="ant-design:close-circle-outlined"
        />
      </div>

      <!-- 检查范围提示 -->
      <div class="integrity-meta">
        检查范围：{{ dayjs(result.tsStart).format('YYYY-MM-DD HH:mm') }} ~
        {{ dayjs(result.tsEnd).format('YYYY-MM-DD HH:mm') }}
        ｜ 采样间隔：{{ result.expectedInterval }}s
        ｜ 检查时间：{{ dayjs(result.checkedAt).format('YYYY-MM-DD HH:mm:ss') }}
      </div>

      <!-- 双 Tab 表格 -->
      <Tabs v-model:activeKey="activeTab">
        <Tabs.TabPane key="loop" tab="按回路">
          <Table
            :columns="loopColumns"
            :data-source="result.loopDetails"
            row-key="loopId"
            :row-selection="rowSelection"
            size="small"
            :pagination="{ pageSize: 20, showSizeChanger: false }"
            :scroll="{ x: 900, y: 400 }"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'status'">
                <Tag :color="statusTag(record.status).color">
                  {{ statusTag(record.status).text }}
                </Tag>
              </template>
              <template v-else-if="column.key === 'completeness'">
                <Progress
                  :percent="Math.round(record.completeness * 100)"
                  size="small"
                  :status="
                    record.completeness >= 0.95
                      ? 'success'
                      : record.completeness >= 0.2
                        ? 'active'
                        : 'exception'
                  "
                />
              </template>
              <template v-else-if="column.key === 'points'">
                {{ record.actualPoints.toLocaleString() }} /
                {{ record.expectedPoints.toLocaleString() }}
              </template>
            </template>
          </Table>
        </Tabs.TabPane>
        <Tabs.TabPane key="time" tab="按时间缺口">
          <Table
            :columns="timeColumns"
            :data-source="result.timeGaps"
            row-key="startTs"
            size="small"
            :pagination="{ pageSize: 20, showSizeChanger: false }"
            :scroll="{ y: 400 }"
          />
        </Tabs.TabPane>
      </Tabs>
    </template>

    <!-- 加载占位 -->
    <div v-else-if="loading" class="integrity-loading">
      <Progress type="circle" :percent="100" status="active" />
      <p>正在检查数据完整性...</p>
    </div>

    <!-- 底部操作栏 -->
    <template #footer>
      <div class="integrity-footer">
        <span class="integrity-footer-count">
          已选 {{ selectedLoopIds.length }} 个回路待补齐
        </span>
        <Tooltip
          title="补齐采用 skip 策略，保留已有数据，仅从远端拉取缺失时段"
        >
          <Button
            type="primary"
            :disabled="!hasSelected"
            @click="handleBackfill"
          >
            一键补齐缺失数据
          </Button>
        </Tooltip>
      </div>
    </template>
  </Drawer>
</template>

<style scoped>
.integrity-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

.integrity-meta {
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 12px;
  color: hsl(var(--foreground) / 60%);
  background: hsl(var(--accent) / 30%);
  border-radius: 6px;
}

.integrity-loading {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: center;
  justify-content: center;
  height: 300px;
  color: hsl(var(--foreground) / 60%);
}

.integrity-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.integrity-footer-count {
  font-size: 13px;
  color: hsl(var(--foreground) / 70%);
}

@media (max-width: 768px) {
  .integrity-summary {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
