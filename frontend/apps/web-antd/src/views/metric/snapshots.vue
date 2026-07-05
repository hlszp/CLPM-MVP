<script lang="ts" setup>
/**
 * 回路小时指标快照列表页
 *
 * 对齐后端 GET /api/v1/performance/loops/snapshots
 * - 顶部工具栏：刷新
 * - 筛选区：装置 TreeSelect + 回路 Select + 时间 RangePicker + 状态 + 可信度
 * - 表格：回路名 / 时间窗 / 综合评分 / 准确率 / 快速率 / 稳定率 / 有效自控率 / 可信度徽章 / 状态
 * - 行展开：显示完整 24 字段（含数据血缘）
 *
 * 路由：/metric/snapshots
 * 权限：所有角色可查看
 */
import type { TableColumnsType } from 'ant-design-vue';

import { computed, onMounted, ref } from 'vue';

import { RotateCw } from '@vben/icons';

import {
  Button,
  DatePicker,
  Descriptions,
  DescriptionsItem,
  Select,
  Space,
  Table,
  Tag,
  TreeSelect,
  message,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopSnapshotsApi } from '#/api/metric';
import type {
  ConfidenceLevel,
  KpiSnapshotItem,
  KpiStatus,
} from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { getLoopListApi } from '#/api/loop';

defineOptions({ name: 'MetricSnapshots' });

// ============ 列表状态 ============
const loading = ref(false);
const snapshotList = ref<KpiSnapshotItem[]>([]);
const totalCount = ref(0);
const currentPage = ref(1);
const pageSize = ref(20);

// 筛选状态
const filterLoopId = ref<string | undefined>();
const filterPlantNodeId = ref<string | undefined>();
const filterStatus = ref<KpiStatus | undefined>();
const filterConfidence = ref<ConfidenceLevel | undefined>();
const filterDateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>();

// 装置树 + 回路列表
const plantNodeTree = ref<any[]>([]);
const loopOptions = ref<{ label: string; value: string }[]>([]);

// ============ 表格列定义 ============
const columns = computed<TableColumnsType>(() => [
  {
    title: '回路',
    key: 'loopTagName',
    dataIndex: 'loopTagName',
    width: 180,
    fixed: 'left',
    ellipsis: true,
  },
  {
    title: '时间窗',
    key: 'tsRange',
    width: 280,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 100,
    sorter: true,
  },
  {
    title: '准确率',
    key: 'accuracyRate',
    dataIndex: 'accuracyRate',
    width: 90,
  },
  {
    title: '快速率',
    key: 'fastRate',
    dataIndex: 'fastRate',
    width: 90,
  },
  {
    title: '稳定率',
    key: 'steadyRate',
    dataIndex: 'steadyRate',
    width: 90,
  },
  {
    title: '有效自控率',
    key: 'effectiveAutoRate',
    dataIndex: 'effectiveAutoRate',
    width: 110,
  },
  {
    title: '可信度',
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    width: 80,
  },
  {
    title: '状态',
    key: 'status',
    dataIndex: 'status',
    width: 110,
    fixed: 'right' as const,
  },
]);

// ============ 加载列表 ============
async function loadList() {
  loading.value = true;
  try {
    const params: any = {
      page: currentPage.value,
      pageSize: pageSize.value,
    };
    if (filterLoopId.value) params.loopId = filterLoopId.value;
    if (filterPlantNodeId.value) params.plantNodeId = filterPlantNodeId.value;
    if (filterStatus.value) params.status = filterStatus.value;
    if (filterConfidence.value) params.confidenceLevel = filterConfidence.value;
    if (filterDateRange.value) {
      params.startTime = filterDateRange.value[0].toISOString();
      params.endTime = filterDateRange.value[1].toISOString();
    }
    const result = await getLoopSnapshotsApi(params);
    snapshotList.value = result.items;
    totalCount.value = result.total;
  } catch (error: any) {
    console.error('加载指标快照列表失败:', error);
    message.error(error?.message || '加载失败');
  } finally {
    loading.value = false;
  }
}

// ============ 加载装置树 ============
async function loadPlantNodeTree() {
  try {
    const data = await getPlantNodeTreeApi();
    plantNodeTree.value = data || [];
  } catch {
    plantNodeTree.value = [];
  }
}

// ============ 加载回路列表 ============
async function loadLoops() {
  try {
    // 后端 loops API pageSize 上限 100，传 1000 会 422
    const result = await getLoopListApi({ page: 1, pageSize: 100 });
    loopOptions.value = (result.items || []).map((l: any) => ({
      label: l.tagName,
      value: l.id,
    }));
  } catch {
    loopOptions.value = [];
  }
}

// ============ 工具函数 ============
function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  return dayjs(ts).format('YYYY-MM-DD HH:mm:ss');
}

function formatNumber(val: number | null | undefined, suffix = ''): string {
  if (val === null || val === undefined) return '—';
  return `${val.toFixed(2)}${suffix}`;
}

const STATUS_COLOR_MAP: Record<string, string> = {
  SUCCESS: 'green',
  INCONCLUSIVE: 'orange',
  PARTIAL: 'red',
};

const STATUS_LABEL_MAP: Record<string, string> = {
  SUCCESS: '成功',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

const CONFIDENCE_COLOR_MAP: Record<string, string> = {
  A: 'green',
  B: 'blue',
  C: 'gold',
  D: 'orange',
  E: 'red',
};

const CONFIDENCE_LABEL_MAP: Record<string, string> = {
  A: 'A 优秀',
  B: 'B 良好',
  C: 'C 一般',
  D: 'D 较差',
  E: 'E 不足',
};

// ============ 生命周期 ============
onMounted(() => {
  loadPlantNodeTree();
  loadLoops();
  loadList();
});
</script>

<template>
  <div class="p-4">
    <!-- 顶部工具栏 -->
    <div class="mb-4 flex items-center justify-between">
      <div class="text-lg font-medium">回路小时指标明细</div>
      <Space>
        <Button @click="loadList">
          <template #icon><RotateCw /></template>
          刷新
        </Button>
      </Space>
    </div>

    <!-- 筛选区 -->
    <div class="mb-4 flex flex-wrap items-center gap-3">
      <TreeSelect
        v-model:value="filterPlantNodeId"
        :tree-data="plantNodeTree"
        :field-names="{ label: 'name', value: 'id', children: 'children' }"
        placeholder="装置筛选"
        allow-clear
        tree-default-expand-all
        style="width: 200px"
        @change="loadList"
      />
      <Select
        v-model:value="filterLoopId"
        :options="loopOptions"
        show-search
        placeholder="回路筛选"
        allow-clear
        :filter-option="(input: string, option: any) => option.label.toLowerCase().includes(input.toLowerCase())"
        style="width: 220px"
        @change="loadList"
      />
      <Select
        v-model:value="filterStatus"
        placeholder="状态"
        allow-clear
        style="width: 130px"
        @change="loadList"
      >
        <Select.Option value="SUCCESS">成功</Select.Option>
        <Select.Option value="INCONCLUSIVE">不确定</Select.Option>
        <Select.Option value="PARTIAL">部分</Select.Option>
      </Select>
      <Select
        v-model:value="filterConfidence"
        placeholder="可信度"
        allow-clear
        style="width: 130px"
        @change="loadList"
      >
        <Select.Option value="A">A 优秀</Select.Option>
        <Select.Option value="B">B 良好</Select.Option>
        <Select.Option value="C">C 一般</Select.Option>
        <Select.Option value="D">D 较差</Select.Option>
        <Select.Option value="E">E 不足</Select.Option>
      </Select>
      <DatePicker.RangePicker
        v-model:value="filterDateRange"
        :allow-clear="true"
        @change="loadList"
      />
      <Button type="primary" @click="loadList">查询</Button>
    </div>

    <!-- 快照列表 -->
    <Table
      :columns="columns"
      :data-source="snapshotList"
      :loading="loading"
      :pagination="{
        current: currentPage,
        pageSize: pageSize,
        total: totalCount,
        showSizeChanger: true,
        showTotal: (t: number) => `共 ${t} 条`,
      }"
      :expandable="{ expandedRowRender: undefined }"
      row-key="tsStart"
      size="small"
      @change="
        (p: any) => {
          currentPage = p.current;
          pageSize = p.pageSize;
          loadList();
        }
      "
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'tsRange'">
          <span class="font-mono text-xs">
            {{ formatTime(record.tsStart) }} ~ {{ formatTime(record.tsEnd) }}
          </span>
        </template>
        <template v-else-if="column.key === 'score'">
          <span class="font-semibold">{{ formatNumber(record.score) }}</span>
        </template>
        <template v-else-if="column.key === 'confidenceLevel'">
          <Tag
            v-if="record.confidenceLevel"
            :color="CONFIDENCE_COLOR_MAP[record.confidenceLevel] || 'default'"
          >
            {{ CONFIDENCE_LABEL_MAP[record.confidenceLevel] || record.confidenceLevel }}
          </Tag>
          <span v-else>—</span>
        </template>
        <template v-else-if="column.key === 'status'">
          <Tag :color="STATUS_COLOR_MAP[record.status] || 'default'">
            {{ STATUS_LABEL_MAP[record.status] || record.status }}
          </Tag>
        </template>
        <template
          v-else-if="
            (['accuracyRate', 'fastRate', 'steadyRate', 'effectiveAutoRate'] as string[]).includes(
              column.key as string,
            )
          "
        >
          {{ formatNumber(record[column.dataIndex as string], '%') }}
        </template>
      </template>

      <!-- 行展开：完整 24 字段详情 -->
      <template #expandedRowRender="{ record }">
        <Descriptions
          :column="4"
          size="small"
          bordered
          :label-style="{ width: '120px' }"
        >
          <DescriptionsItem label="回路 ID">
            {{ record.loopId || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="回路名">
            {{ record.loopTagName || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="好值率">
            {{ formatNumber(record.goodValueRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="自控率">
            {{ formatNumber(record.autoModeRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="振荡率">
            {{ formatNumber(record.oscillationRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="饱和率">
            {{ formatNumber(record.saturationRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="粘滞指数">
            {{ formatNumber(record.stictionIndex) }}
          </DescriptionsItem>
          <DescriptionsItem label="稳态时间">
            {{ formatNumber(record.settlingTime, 's') }}
          </DescriptionsItem>
          <DescriptionsItem label="输出行程指数">
            {{ formatNumber(record.outputTravelIndex) }}
          </DescriptionsItem>
          <DescriptionsItem label="理想稳态时间">
            {{ formatNumber(record.idealSettlingTime, 's') }}
          </DescriptionsItem>
          <DescriptionsItem label="有效数据率">
            {{ formatNumber(record.validRate) }}
          </DescriptionsItem>
          <DescriptionsItem label="可信度等级">
            <Tag
              v-if="record.confidenceLevel"
              :color="CONFIDENCE_COLOR_MAP[record.confidenceLevel] || 'default'"
            >
              {{ record.confidenceLevel }}
            </Tag>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="算法版本">
            {{ record.algorithmVersion || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="采样频率">
            {{ record.samplingFreq || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="质量策略">
            {{ record.qualityPolicy || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="状态">
            <Tag :color="STATUS_COLOR_MAP[record.status] || 'default'">
              {{ STATUS_LABEL_MAP[record.status] || record.status }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem v-if="record.dataLineage" label="数据血缘" :span="4">
            <div class="font-mono text-xs">
              <div>采样频率: {{ record.dataLineage.samplingFreq }}</div>
              <div>聚合策略: {{ record.dataLineage.aggregationPolicy }}</div>
              <div>质量策略: {{ record.dataLineage.qualityPolicy }}</div>
              <div>tagGroup: {{ record.dataLineage.tagGroup }}</div>
              <div>
                数据块: {{ record.dataLineage.dataBlockIds?.join(', ') || '—' }}
              </div>
              <div>有效数据率: {{ record.dataLineage.validRate }}</div>
              <div>预处理版本: {{ record.dataLineage.dataPolicyVersion }}</div>
              <div>算法版本: {{ record.dataLineage.algorithmVersion }}</div>
            </div>
          </DescriptionsItem>
        </Descriptions>
      </template>
    </Table>
  </div>
</template>
