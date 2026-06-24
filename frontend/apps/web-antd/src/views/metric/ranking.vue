<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * S3-METRIC-010 低效回路排行页
 *
 * 对齐 IDS v3.2 §2.3 + PRD §4.3
 * - 顶部 4 个统计 KPI 卡片（总回路数/低效回路数/平均评分/最低评分）
 * - 筛选栏（装置选择/评分范围/排序字段/排序方向/搜索框）
 * - 表格展示排行（排名/位号/装置/评分/6大KPI/状态/预诊标签/操作）
 * - 点击行打开侧边抽屉展示回路摘要
 * - 点击"查看详情"跳转回路详情页 /loop/detail/:id
 * - 排名前 3 用红/黄/橙色标示
 */
import type { KpiStatus, MetricApi, TimeWindow } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Drawer,
  Input,
  InputNumber,
  Select,
  Statistic,
  Table,
  Tag,
} from 'ant-design-vue';

import { getRankingApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'MetricRanking' });

const router = useRouter();

const loading = ref(false);
const rankingList = ref<MetricApi.RankingItem[]>([]);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

const filter = reactive({
  plantNodeId: undefined as string | undefined,
  timeWindow: 'today' as TimeWindow,
  scoreMin: undefined as number | undefined,
  scoreMax: undefined as number | undefined,
  sortBy: 'compositeScore' as string,
  sortOrder: 'asc' as 'asc' | 'desc',
  keyword: '',
});

const pagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
});

const timeWindowOptions = [
  { label: '今天', value: 'today' },
  { label: '昨天', value: 'yesterday' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

const sortByOptions = [
  { label: '综合评分', value: 'compositeScore' },
  { label: '好值率', value: 'goodValueRate' },
  { label: '自控率', value: 'autoModeRate' },
  { label: '有效自控率', value: 'effectiveAutoRate' },
  { label: '平稳率', value: 'steadyRate' },
  { label: '准确率', value: 'accuracyRate' },
  { label: '快速率', value: 'fastResponseRate' },
  { label: '振荡率', value: 'oscillationRate' },
  { label: '饱和率', value: 'saturationRate' },
];

const sortOrderOptions = [
  { label: '升序（低→高）', value: 'asc' },
  { label: '降序（高→低）', value: 'desc' },
];

// 状态色映射
const statusColorMap: Record<KpiStatus, string> = {
  SUCCESS: 'green',
  INCONCLUSIVE: 'default',
  PARTIAL: 'orange',
};

const statusLabelMap: Record<KpiStatus, string> = {
  SUCCESS: '良好',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

const actionStatusLabel: Record<string, string> = {
  PENDING: '待处理',
  IN_PROGRESS: '处理中',
  IMPLEMENTED: '已实施',
  IGNORED: '已忽略',
};

const columns: TableColumnsType = [
  { title: '排名', dataIndex: 'rank', key: 'rank', width: 70, align: 'center' },
  { title: '位号', dataIndex: 'tagName', key: 'tagName', width: 140 },
  {
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 160,
    ellipsis: true,
  },
  {
    title: '综合评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 100,
    align: 'right',
  },
  {
    title: '好值率',
    dataIndex: 'goodValueRate',
    key: 'goodValueRate',
    width: 90,
    align: 'right',
  },
  {
    title: '自控率',
    dataIndex: 'autoModeRate',
    key: 'autoModeRate',
    width: 90,
    align: 'right',
  },
  {
    title: '平稳率',
    dataIndex: 'steadyRate',
    key: 'steadyRate',
    width: 90,
    align: 'right',
  },
  {
    title: '准确率',
    dataIndex: 'accuracyRate',
    key: 'accuracyRate',
    width: 90,
    align: 'right',
  },
  {
    title: '振荡率',
    dataIndex: 'oscillationRate',
    key: 'oscillationRate',
    width: 90,
    align: 'right',
  },
  {
    title: '饱和率',
    dataIndex: 'saturationRate',
    key: 'saturationRate',
    width: 90,
    align: 'right',
  },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
  {
    title: '预诊',
    dataIndex: 'preDiagnosis',
    key: 'preDiagnosis',
    width: 140,
    ellipsis: true,
  },
  { title: '操作', key: 'action', width: 110, fixed: 'right' },
];

// 抽屉状态
const drawerVisible = ref(false);
const selectedLoop = ref<MetricApi.RankingItem | null>(null);

/** 统计 KPI */
const stats = computed(() => {
  const list = rankingList.value;
  if (list.length === 0) {
    return { total: 0, badCount: 0, avgScore: 0, minScore: 0 };
  }
  const total = list.length;
  let badCount = 0;
  let sum = 0;
  let min = 100;
  for (const item of list) {
    if (item.status === 'PARTIAL') badCount++;
    sum += Number(item.compositeScore) || 0;
    const score = Number(item.compositeScore) || 100;
    if (score < min) min = score;
  }
  const avg = sum / total;
  return {
    total,
    badCount,
    avgScore: Number(avg?.toFixed(1) ?? 0),
    minScore: Number(min?.toFixed(1) ?? 0),
  };
});

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载排行 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getRankingApi({
      plantNodeId: filter.plantNodeId,
      timeWindow: filter.timeWindow,
      limit: pagination.pageSize,
      sortBy: filter.sortBy,
      sortOrder: filter.sortOrder,
    });
    // 客户端过滤评分范围和关键字
    let list = data || [];
    if (filter.scoreMin !== null && filter.scoreMin !== undefined) {
      list = list.filter((i) => i.compositeScore >= (filter.scoreMin ?? 0));
    }
    if (filter.scoreMax !== null && filter.scoreMax !== undefined) {
      list = list.filter((i) => i.compositeScore <= (filter.scoreMax ?? 100));
    }
    if (filter.keyword) {
      const kw = filter.keyword.toLowerCase();
      list = list.filter(
        (i) =>
          i.tagName.toLowerCase().includes(kw) ||
          i.unitName.toLowerCase().includes(kw),
      );
    }
    rankingList.value = list;
    pagination.total = list.length;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  pagination.page = 1;
  loadList();
}

function handleTableChange(p: TablePaginationConfig) {
  pagination.page = p.current || 1;
  pagination.pageSize = p.pageSize || 20;
}

/** 点击行打开抽屉 */
function handleRowClick(record: MetricApi.RankingItem) {
  selectedLoop.value = record;
  drawerVisible.value = true;
}

/** 查看详情跳转 */
function handleViewDetail(loopId: string) {
  drawerVisible.value = false;
  router.push(`/loop/detail/${loopId}`);
}

/** 排名前 3 的颜色 */
function rankColor(rank: number): string {
  if (rank === 1) return '#ff4d4f'; // 红
  if (rank === 2) return '#faad14'; // 黄
  if (rank === 3) return '#fa8c16'; // 橙
  return '';
}

/** 格式化百分比 */
function fpct(val: number | undefined): string {
  return val === null || val === undefined ? '—' : `${Number(val).toFixed(1)}%`;
}

onMounted(() => {
  loadPlantNodes();
  loadList();
});
</script>

<template>
  <Page title="低效回路排行">
    <!-- 顶部统计 KPI 卡片 -->
    <div class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
      <Card size="small" :loading="loading">
        <Statistic title="总回路数" :value="stats.total" />
      </Card>
      <Card size="small" :loading="loading">
        <Statistic
          title="低效回路数"
          :value="stats.badCount"
          :value-style="{ color: '#ff4d4f' }"
        />
      </Card>
      <Card size="small" :loading="loading">
        <Statistic
          title="平均评分"
          :value="stats.avgScore"
          :precision="1"
          suffix=""
        />
      </Card>
      <Card size="small" :loading="loading">
        <Statistic
          title="最低评分"
          :value="stats.minScore"
          :precision="1"
          :value-style="{ color: '#ff4d4f' }"
        />
      </Card>
    </div>

    <Card>
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Select
          v-model:value="filter.plantNodeId"
          placeholder="装置/单元筛选"
          style="width: 220px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.timeWindow"
          style="width: 140px"
          :options="timeWindowOptions"
          @change="handleSearch"
        />
        <InputNumber
          v-model:value="filter.scoreMin"
          placeholder="最低分"
          :min="0"
          :max="100"
          style="width: 110px"
          @change="handleSearch"
        />
        <span class="text-gray-400">~</span>
        <InputNumber
          v-model:value="filter.scoreMax"
          placeholder="最高分"
          :min="0"
          :max="100"
          style="width: 110px"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.sortBy"
          style="width: 140px"
          :options="sortByOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.sortOrder"
          style="width: 150px"
          :options="sortOrderOptions"
          @change="handleSearch"
        />
        <Input
          v-model:value="filter.keyword"
          placeholder="搜索位号/装置"
          allow-clear
          style="width: 200px"
          @press-enter="handleSearch"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>

      <Table
        :columns="columns"
        :data-source="rankingList"
        :loading="loading"
        :pagination="{
          current: pagination.page,
          pageSize: pagination.pageSize,
          total: pagination.total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: MetricApi.RankingItem) => record.loopId"
        :scroll="{ x: 1500 }"
        size="middle"
        :custom-row="
          (record: MetricApi.RankingItem) => ({
            onClick: () => handleRowClick(record),
            style: { cursor: 'pointer' },
          })
        "
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'rank'">
            <span
              class="inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold"
              :style="
                rankColor(record.rank)
                  ? { backgroundColor: rankColor(record.rank), color: '#fff' }
                  : {}
              "
            >
              {{ record.rank }}
            </span>
          </template>
          <template v-else-if="column.key === 'compositeScore'">
            <span class="font-medium text-blue-600">
              {{ Number(record.compositeScore).toFixed(1) }}
            </span>
          </template>
          <template v-else-if="column.key === 'goodValueRate'">
            {{ Number(record.goodValueRate).toFixed(1) }}%
          </template>
          <template v-else-if="column.key === 'autoModeRate'">
            {{ Number(record.autoModeRate).toFixed(1) }}%
          </template>
          <template v-else-if="column.key === 'steadyRate'">
            {{ Number(record.steadyRate).toFixed(1) }}%
          </template>
          <template v-else-if="column.key === 'accuracyRate'">
            {{ Number(record.accuracyRate).toFixed(1) }}%
          </template>
          <template v-else-if="column.key === 'oscillationRate'">
            <span :class="record.oscillationRate > 30 ? 'text-red-500' : ''">
              {{ Number(record.oscillationRate).toFixed(1) }}%
            </span>
          </template>
          <template v-else-if="column.key === 'saturationRate'">
            <span :class="record.saturationRate > 20 ? 'text-orange-500' : ''">
              {{ Number(record.saturationRate).toFixed(1) }}%
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="statusColorMap[record.status as KpiStatus]">
              {{ statusLabelMap[record.status as KpiStatus] }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'preDiagnosis'">
            <Tag v-if="record.preDiagnosis" color="orange">
              {{ record.preDiagnosis }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'action'">
            <Button
              type="link"
              size="small"
              @click.stop="handleViewDetail(record.loopId)"
            >
              查看详情
            </Button>
          </template>
        </template>
      </Table>
    </Card>

    <!-- 侧边抽屉 -->
    <Drawer
      v-model:open="drawerVisible"
      title="回路摘要"
      width="480"
      placement="right"
    >
      <template v-if="selectedLoop">
        <div class="mb-4">
          <h3 class="text-lg font-semibold">{{ selectedLoop.tagName }}</h3>
          <p class="text-sm text-gray-500">{{ selectedLoop.unitName }}</p>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <Card size="small">
            <div class="text-xs text-gray-500">综合评分</div>
            <div class="text-2xl font-bold text-blue-600">
              {{ Number(selectedLoop.compositeScore).toFixed(1) }}
            </div>
          </Card>
          <Card size="small">
            <div class="text-xs text-gray-500">状态</div>
            <Tag :color="statusColorMap[selectedLoop.status]" class="mt-1">
              {{ statusLabelMap[selectedLoop.status] }}
            </Tag>
          </Card>
        </div>
        <div class="mt-4 space-y-2">
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">好值率</span>
            <b>{{ fpct(selectedLoop.goodValueRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">自控率</span>
            <b>{{ fpct(selectedLoop.autoModeRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">平稳率</span>
            <b>{{ fpct(selectedLoop.steadyRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">准确率</span>
            <b>{{ fpct(selectedLoop.accuracyRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">振荡率</span>
            <b>{{ fpct(selectedLoop.oscillationRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">饱和率</span>
            <b>{{ fpct(selectedLoop.saturationRate) }}</b>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">预诊</span>
            <Tag v-if="selectedLoop.preDiagnosis" color="orange">
              {{ selectedLoop.preDiagnosis }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">处理状态</span>
            <span>{{
              actionStatusLabel[selectedLoop.actionStatus] ||
              selectedLoop.actionStatus
            }}</span>
          </div>
          <div class="flex justify-between border-b pb-2">
            <span class="text-gray-500">算法版本</span>
            <span>{{ selectedLoop.algorithmVersion }}</span>
          </div>
        </div>
        <div class="mt-6">
          <Button
            type="primary"
            block
            @click="handleViewDetail(selectedLoop.loopId)"
          >
            查看回路详情
          </Button>
        </div>
      </template>
    </Drawer>
  </Page>
</template>
