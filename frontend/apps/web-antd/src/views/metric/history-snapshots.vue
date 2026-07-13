<script lang="ts" setup>
/**
 * 评估历史 — 回路小时指标快照列表（Tab 内嵌组件）
 *
 * 对齐后端 GET /api/v1/performance/loops/snapshots
 * - 筛选区：装置 TreeSelect + 回路 Select + 时间 RangePicker + 状态 + 可信度
 * - 表格：回路名 / 时间窗 / 综合评分 / 8 大 KPI + 粘滞指数 / 稳态时间 / 输出行程指数
 *   / 可信度徽章 / 状态 / 操作（详情按钮）
 * - 详情抽屉：点击"详情"按钮从右侧滑出，展示完整 24 字段（含数据血缘）
 *
 * 嵌入位置：评估任务模块 → "评估历史" Tab
 * 权限：所有角色可查看
 */
import type { TableColumnsType } from 'ant-design-vue';

import type {
  ConfidenceLevel,
  KpiSnapshotItem,
  KpiStatus,
} from '#/api/metric';

import { computed, onMounted, ref } from 'vue';

import { RotateCw } from '@vben/icons';

import {
  Button,
  DatePicker,
  Descriptions,
  DescriptionsItem,
  Drawer,
  message,
  Select,
  Table,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import { getLoopSnapshotsApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import ScoreSparkline from '#/components/metric/score-sparkline.vue';

defineOptions({ name: 'MetricHistorySnapshots' });

// ============ 列表状态 ============
const loading = ref(false);
const loadError = ref(false);
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

// ============ 详情抽屉状态 ============
const drawerVisible = ref(false);
const drawerRecord = ref<KpiSnapshotItem | null>(null);
const drawerTrendSnapshots = ref<KpiSnapshotItem[]>([]);
const drawerTrendLoading = ref(false);

/** 点击"详情"按钮：打开抽屉并加载该行完整数据及趋势 */
async function openDetail(record: Record<string, any>) {
  drawerRecord.value = record as unknown as KpiSnapshotItem;
  drawerVisible.value = true;
  drawerTrendLoading.value = true;
  drawerTrendSnapshots.value = [];

  const loopId = record.loopId;
  if (loopId) {
    try {
      // 后端 pageSize 上限 100，分页拉取近 24 小时数据
      const params: any = {
        loopId,
        page: 1,
        pageSize: 24,
        latestOnly: false,
      };
      const result = await getLoopSnapshotsApi(params);
      drawerTrendSnapshots.value = (result.items || []).toSorted((a, b) => {
        const aTs = a.tsStart || '';
        const bTs = b.tsStart || '';
        return aTs.localeCompare(bTs);
      });
    } catch {
      drawerTrendSnapshots.value = [];
    }
  }
  drawerTrendLoading.value = false;
}

/** 关闭抽屉 */
function closeDetail() {
  drawerVisible.value = false;
  drawerRecord.value = null;
  drawerTrendSnapshots.value = [];
}

// ============ 表格列定义 ============
// 列顺序：回路 → 时间窗 → 综合评分 → 8 大 KPI（好值率/自控率/有效自控率/
// 稳定率/准确率/快速率/振荡率/饱和率）→ 粘滞指数 / 稳态时间 / 输出行程指数
// → 可信度 → 状态 → 操作
// 8 大 KPI 顺序对齐 GB/T 44693.2-2024
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
    width: 110,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
    sorter: true,
  },
  {
    title: '好值率',
    key: 'goodValueRate',
    dataIndex: 'goodValueRate',
    width: 80,
  },
  {
    title: '自控率',
    key: 'autoModeRate',
    dataIndex: 'autoModeRate',
    width: 80,
  },
  {
    title: '有效自控率',
    key: 'effectiveAutoRate',
    dataIndex: 'effectiveAutoRate',
    width: 100,
  },
  {
    title: '稳定率',
    key: 'steadyRate',
    dataIndex: 'steadyRate',
    width: 80,
  },
  {
    title: '准确率',
    key: 'accuracyRate',
    dataIndex: 'accuracyRate',
    width: 80,
  },
  {
    title: '快速率',
    key: 'fastRate',
    dataIndex: 'fastRate',
    width: 80,
  },
  {
    title: '振荡率',
    key: 'oscillationRate',
    dataIndex: 'oscillationRate',
    width: 80,
  },
  {
    title: '饱和率',
    key: 'saturationRate',
    dataIndex: 'saturationRate',
    width: 80,
  },
  {
    title: '粘滞指数',
    key: 'stictionIndex',
    dataIndex: 'stictionIndex',
    width: 90,
  },
  {
    title: '稳态时间',
    key: 'settlingTime',
    dataIndex: 'settlingTime',
    width: 90,
  },
  {
    title: '输出行程指数',
    key: 'outputTravelIndex',
    dataIndex: 'outputTravelIndex',
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
    width: 100,
  },
  {
    title: '操作',
    key: 'action',
    width: 80,
    fixed: 'right' as const,
  },
]);

// ============ 加载列表 ============
async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const params: any = {
      page: currentPage.value,
      pageSize: pageSize.value,
      latestOnly: false,
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
    loadError.value = true;
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
/**
 * 加载回路列表。
 * 后端 loops API pageSize 上限 100，循环分页加载全部回路，避免截断。
 * @param plantNodeId 装置 ID；传入时只加载该装置下的回路，不传则加载全部。
 */
async function loadLoops(plantNodeId?: string) {
  try {
    const allLoops: any[] = [];
    let page = 1;
    const loopPageSize = 100;
    let total = 0;
    do {
      const params: any = { page, pageSize: loopPageSize };
      if (plantNodeId) params.plantNodeId = plantNodeId;
      const result = await getLoopListApi(params);
      total = result.total;
      allLoops.push(...(result.items || []));
      page += 1;
    } while ((page - 1) * loopPageSize < total);
    loopOptions.value = allLoops.map((l: any) => ({
      label: l.tagName,
      value: l.loopId,
    }));
  } catch {
    loopOptions.value = [];
  }
}

// ============ 装置筛选联动 ============
/** 装置变更：重新加载该装置下的回路，并清空已选回路 */
function handlePlantNodeChange(value: string | undefined) {
  filterLoopId.value = undefined;
  loadLoops(value);
  loadList();
}

// ============ 工具函数 ============
/**
 * 时间窗：只显示结束时间的「MM-DD HH:00」。
 *
 * PostgreSQL ts_start 字段是 TIMESTAMP WITHOUT TIME ZONE，存储的是 UTC 时间。
 * 假定不带时区的时间字符串为 UTC，手动加 Z 标记后再转本地时区显示。
 */
function formatTsEnd(ts: null | string | undefined): string {
  if (!ts) return '—';
  const hasTimezone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(ts);
  const normalized = hasTimezone ? ts : `${ts}Z`;
  return dayjs(normalized).format('MM-DD HH:00');
}

/**
 * 完整时间格式化（用于抽屉详情）。
 *
 * 显式标注 UTC+8，避免用户误认为显示的是 UTC 时间。
 */
function formatFullTime(ts: null | string | undefined): string {
  if (!ts) return '—';
  const hasTimezone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(ts);
  const normalized = hasTimezone ? ts : `${ts}Z`;
  return dayjs(normalized).format('YYYY-MM-DD HH:mm:ss [UTC+8]');
}

function formatNumber(val: null | number | undefined, suffix = ''): string {
  if (val === null || val === undefined) return '—';
  return `${val.toFixed(2)}${suffix}`;
}

const STATUS_COLOR_MAP: Record<string, string> = {
  SUCCESS: 'success',
  PARTIAL: 'warning',
  INCONCLUSIVE: 'default',
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
  <div>
    <!-- 顶部工具栏 -->
    <ClpmPageToolbar
      class="mb-4"
      title="评估历史"
      subtitle="按小时快照展示回路性能指标，支持多维度筛选和详情查看。"
      :loading="loading"
    >
      <template #actions>
        <Button @click="loadList">
          <template #icon><RotateCw /></template>
          刷新
        </Button>
      </template>
    </ClpmPageToolbar>

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
        @change="handlePlantNodeChange"
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
    <ClpmDataCanvas
      :loading="loading"
      :error="loadError"
      :empty="!loading && !loadError && snapshotList.length === 0"
      @retry="loadList"
    >
      <Table
        :columns="columns"
        :data-source="snapshotList"
        :pagination="{
          current: currentPage,
          pageSize,
          total: totalCount,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :scroll="{ x: 1800 }"
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
            {{ formatTsEnd(record.tsEnd) }}
          </span>
        </template>
        <template v-else-if="column.key === 'score'">
          <span class="clpm-num font-semibold">
            {{ formatNumber(record.score) }}
          </span>
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
            ([
              'goodValueRate',
              'autoModeRate',
              'effectiveAutoRate',
              'steadyRate',
              'accuracyRate',
              'fastRate',
              'oscillationRate',
              'saturationRate',
            ] as string[]).includes(column.key as string)
          "
        >
          <span class="clpm-num">
            {{ formatNumber(record[column.dataIndex as string], '%') }}
          </span>
        </template>
        <!-- 粘滞指数 / 稳态时间 / 输出行程指数 -->
        <template
          v-else-if="
            (['stictionIndex', 'settlingTime', 'outputTravelIndex'] as string[]).includes(
              column.key as string,
            )
          "
        >
          <span class="clpm-num font-mono">
            {{
              column.key === 'settlingTime'
                ? formatNumber(record[column.dataIndex as string], 's')
                : formatNumber(record[column.dataIndex as string])
            }}
          </span>
        </template>
        <!-- 操作列：详情按钮 -->
        <template v-else-if="column.key === 'action'">
          <Button type="link" size="small" @click="openDetail(record)">
            详情
          </Button>
        </template>
      </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 详情抽屉：从右侧滑出，展示完整字段 -->
    <Drawer
      :open="drawerVisible"
      title="回路指标详情"
      placement="right"
      :width="720"
      :mask-closable="true"
      @close="closeDetail"
    >
      <template v-if="drawerRecord">
        <Descriptions
          :column="2"
          size="small"
          bordered
          :label-style="{ width: '120px' }"
        >
          <DescriptionsItem label="回路 ID">
            {{ drawerRecord.loopId || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="回路名">
            {{ drawerRecord.loopTagName || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="时间窗起">
            {{ formatFullTime(drawerRecord.tsStart) }}
          </DescriptionsItem>
          <DescriptionsItem label="时间窗止">
            {{ formatFullTime(drawerRecord.tsEnd) }}
          </DescriptionsItem>
          <DescriptionsItem label="综合评分">
            <span class="clpm-num font-semibold">
              {{ formatNumber(drawerRecord.score) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="可信度">
            <Tag
              v-if="drawerRecord.confidenceLevel"
              :color="CONFIDENCE_COLOR_MAP[drawerRecord.confidenceLevel] || 'default'"
            >
              {{ CONFIDENCE_LABEL_MAP[drawerRecord.confidenceLevel] || drawerRecord.confidenceLevel }}
            </Tag>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="状态">
            <Tag :color="STATUS_COLOR_MAP[drawerRecord.status] || 'default'">
              {{ STATUS_LABEL_MAP[drawerRecord.status] || drawerRecord.status }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="算法版本">
            {{ drawerRecord.algorithmVersion || '—' }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 8 大 KPI -->
        <div class="mt-4 mb-2 text-sm font-medium">8 大 KPI 指标</div>
        <Descriptions :column="2" size="small" bordered :label-style="{ width: '120px' }">
          <DescriptionsItem label="好值率">
            {{ formatNumber(drawerRecord.goodValueRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="自控率">
            {{ formatNumber(drawerRecord.autoModeRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="有效自控率">
            {{ formatNumber(drawerRecord.effectiveAutoRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="稳定率">
            {{ formatNumber(drawerRecord.steadyRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="准确率">
            {{ formatNumber(drawerRecord.accuracyRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="快速率">
            {{ formatNumber(drawerRecord.fastRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="振荡率">
            {{ formatNumber(drawerRecord.oscillationRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="饱和率">
            {{ formatNumber(drawerRecord.saturationRate, '%') }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 诊断指标 -->
        <div class="mt-4 mb-2 text-sm font-medium">诊断指标</div>
        <Descriptions :column="2" size="small" bordered :label-style="{ width: '120px' }">
          <DescriptionsItem label="粘滞指数">
            {{ formatNumber(drawerRecord.stictionIndex) }}
          </DescriptionsItem>
          <DescriptionsItem label="稳态时间">
            {{ formatNumber(drawerRecord.settlingTime, 's') }}
          </DescriptionsItem>
          <DescriptionsItem label="输出行程指数">
            {{ formatNumber(drawerRecord.outputTravelIndex) }}
          </DescriptionsItem>
          <DescriptionsItem label="理想稳态时间">
            {{ formatNumber(drawerRecord.idealSettlingTime, 's') }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 数据血缘 -->
        <div class="mt-4 mb-2 text-sm font-medium">数据血缘</div>
        <Descriptions :column="2" size="small" bordered :label-style="{ width: '120px' }">
          <DescriptionsItem label="有效数据率">
            {{ formatNumber(drawerRecord.validRate) }}
          </DescriptionsItem>
          <DescriptionsItem label="采样频率">
            {{ drawerRecord.samplingFreq || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="质量策略">
            {{ drawerRecord.qualityPolicy || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="可信度等级">
            {{ drawerRecord.confidenceLevel || '—' }}
          </DescriptionsItem>
        </Descriptions>

        <template v-if="drawerRecord.dataLineage">
          <div class="mt-4 mb-2 text-sm font-medium">数据血缘详情</div>
          <div class="rounded p-3 font-mono text-xs" :style="{ background: 'hsl(var(--muted) / 42%)' }">
            <div>采样频率: {{ drawerRecord.dataLineage.samplingFreq }}</div>
            <div>聚合策略: {{ drawerRecord.dataLineage.aggregationPolicy }}</div>
            <div>质量策略: {{ drawerRecord.dataLineage.qualityPolicy }}</div>
            <div>tagGroup: {{ drawerRecord.dataLineage.tagGroup }}</div>
            <div>
              数据块: {{ drawerRecord.dataLineage.dataBlockIds?.join(', ') || '—' }}
            </div>
            <div>有效数据率: {{ drawerRecord.dataLineage.validRate }}</div>
            <div>预处理版本: {{ drawerRecord.dataLineage.dataPolicyVersion }}</div>
            <div>算法版本: {{ drawerRecord.dataLineage.algorithmVersion }}</div>
          </div>
        </template>

        <!-- 评分趋势 -->
        <div class="mt-4 mb-2 text-sm font-medium">评分趋势（最近 24 小时）</div>
        <div v-if="drawerTrendLoading" class="text-center py-4">加载中...</div>
        <template v-else-if="drawerTrendSnapshots.length > 0">
          <div class="p-3 rounded-lg border border-border bg-muted/30">
            <div class="flex items-center justify-center gap-2">
              <ScoreSparkline
                :data="drawerTrendSnapshots.map((s) => (s.score ?? 0))"
                :width="560"
                :height="50"
              />
            </div>
            <div class="flex justify-between mt-2 text-xs text-muted-foreground">
              <span>{{ formatTsEnd(drawerTrendSnapshots[0]?.tsEnd) }}</span>
              <span>{{ formatTsEnd(drawerTrendSnapshots[drawerTrendSnapshots.length - 1]?.tsEnd) }}</span>
            </div>
          </div>
          <div class="mt-2 max-h-[200px] overflow-y-auto space-y-1">
            <div
              v-for="(item, index) in drawerTrendSnapshots"
              :key="index"
              class="flex items-center gap-3 text-xs"
            >
              <span class="font-mono w-16 text-muted-foreground">{{ formatTsEnd(item.tsEnd) }}</span>
              <span
                class="clpm-num font-medium w-12 text-right"
                :style="{
                  color:
                    item.score !== null && item.score !== undefined
                      ? (item.score >= 80 ? '#10B981' : item.score >= 60 ? '#F59E0B' : '#F43F5E')
                      : '#9CA3AF',
                }"
              >
                {{ formatNumber(item.score) }}
              </span>
              <Tag
                v-if="item.confidenceLevel"
                :color="CONFIDENCE_COLOR_MAP[item.confidenceLevel] || 'default'"
                size="small"
              >
                {{ item.confidenceLevel }}
              </Tag>
            </div>
          </div>
        </template>
        <div v-else class="text-center py-4 text-muted-foreground">暂无趋势数据</div>
      </template>
    </Drawer>
  </div>
</template>
