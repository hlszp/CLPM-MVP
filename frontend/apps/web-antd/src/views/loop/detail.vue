<script lang="ts" setup>
/**
 * S2-LOOP-012 回路详情页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.9 + §2.2.14
 * - 顶部：回路基本信息 + 7 Tag 关联状态
 * - 中部：ECharts 波形图展示 PV/SP/OP 趋势（PV 线按质量码断线渲染）
 * - 底部：6 大 KPI 摘要网格 + composite_score
 * - 支持时间范围切换（1h/24h/7d）
 * - 波形数据超过 1 万点时前端平滑渲染（ECharts dataZoom）
 */
import type { LoopApi } from '#/api/loop';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Spin,
  Tag,
} from 'ant-design-vue';

import { getLoopDetailApi, getLoopMonitorDetailApi } from '#/api/loop';
import QualityTag from '#/components/loop/quality-tag.vue';
import StatusBadge from '#/components/loop/status-badge.vue';
import WaveformChart from '#/components/loop/waveform-chart.vue';

defineOptions({ name: 'LoopDetail' });

const route = useRoute();
const loopId = route.params.id as string;

const loading = ref(false);
const monitorLoading = ref(false);
const loopDetail = ref<LoopApi.LoopDetail | null>(null);
const monitorDetail = ref<LoopApi.MonitorDetail | null>(null);

const trendWindow = ref<LoopApi.TrendWindow>('last_24_hours');

const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '近 1 小时', value: 'last_1_hour' },
  { label: '近 24 小时', value: 'last_24_hours' },
  { label: '近 7 天', value: 'last_7_days' },
];

/** 7 Tag 槽位配置 */
const tagSlots: {
  key: keyof LoopApi.LoopTagMapping;
  label: string;
  required: boolean;
}[] = [
  { key: 'pv', label: 'PV', required: true },
  { key: 'sp', label: 'SP', required: true },
  { key: 'op', label: 'OP', required: true },
  { key: 'mode', label: 'MODE', required: true },
  { key: 'pid_p', label: 'PID_P', required: false },
  { key: 'pid_i', label: 'PID_I', required: false },
  { key: 'pid_d', label: 'PID_D', required: false },
];

/** 8 大 KPI 配置（对齐 GB/T 44693.2-2024） */
const kpiItems: {
  desc: string;
  key: keyof LoopApi.KpiSummary;
  label: string;
  unit: string;
}[] = [
  { desc: '优良值率', key: 'good_value_rate', label: '优良值率', unit: '%' },
  { desc: '自动模式率', key: 'auto_mode_rate', label: '自动模式率', unit: '%' },
  { desc: '有效自控率', key: 'effective_auto_rate', label: '有效自控率', unit: '%' },
  { desc: '稳定率', key: 'steady_rate', label: '稳定率', unit: '%' },
  { desc: '准确度', key: 'accuracy_rate', label: '准确度', unit: '%' },
  { desc: '快速率', key: 'fast_response_rate', label: '快速率', unit: '%' },
  { desc: '振荡率', key: 'oscillation_rate', label: '振荡率', unit: '%' },
  { desc: '饱和率', key: 'saturation_rate', label: '饱和率', unit: '%' },
];

const kpiStatusMap: Record<string, { color: string; label: string }> = {
  SUCCESS: { color: 'green', label: '良好' },
  INCONCLUSIVE: { color: 'default', label: '未确定' },
  PARTIAL: { color: 'orange', label: '部分' },
};

const pageTitle = computed(() => {
  if (loopDetail.value) {
    return `回路详情 - ${loopDetail.value.basicInfo.tagName}`;
  }
  return '回路详情';
});

/** 加载回路详情 */
async function loadDetail() {
  loading.value = true;
  try {
    loopDetail.value = await getLoopDetailApi(loopId);
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 加载监控详情（含趋势和 KPI） */
async function loadMonitorDetail() {
  monitorLoading.value = true;
  try {
    monitorDetail.value = await getLoopMonitorDetailApi(
      loopId,
      trendWindow.value,
    );
  } catch {
    // 错误已由拦截器处理
  } finally {
    monitorLoading.value = false;
  }
}

function handleTrendWindowChange() {
  loadMonitorDetail();
}

function formatTime(t: null | string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

watch(trendWindow, () => {
  loadMonitorDetail();
});

onMounted(() => {
  loadDetail();
  loadMonitorDetail();
});
</script>

<template>
  <Page :title="pageTitle">
    <Spin :spinning="loading">
      <div class="space-y-4">
        <!-- 顶部：基本信息 + Tag 关联状态 -->
        <Card title="回路基本信息">
          <Descriptions
            v-if="loopDetail"
            :column="{ xs: 1, sm: 2, md: 3 }"
            bordered
            size="small"
          >
            <DescriptionsItem label="回路位号">
              {{ loopDetail.basicInfo.tagName }}
            </DescriptionsItem>
            <DescriptionsItem label="描述">
              {{ loopDetail.basicInfo.description }}
            </DescriptionsItem>
            <DescriptionsItem label="所属单元">
              {{ loopDetail.basicInfo.unitName }}
            </DescriptionsItem>
            <DescriptionsItem label="状态">
              <StatusBadge
                :status="loopDetail.basicInfo.status"
                :is-active="loopDetail.basicInfo.isActive"
              />
            </DescriptionsItem>
            <DescriptionsItem label="控制方式">
              {{ loopDetail.runtimeParams.controlMode }}
            </DescriptionsItem>
            <DescriptionsItem label="PID 参数">
              P={{ loopDetail.runtimeParams.pidP }}, I={{
                loopDetail.runtimeParams.pidI
              }}, D={{ loopDetail.runtimeParams.pidD }}
            </DescriptionsItem>
            <DescriptionsItem label="创建时间">
              {{ formatTime(loopDetail.basicInfo.createdAt) }}
            </DescriptionsItem>
            <DescriptionsItem label="创建人">
              {{ loopDetail.basicInfo.createdBy }}
            </DescriptionsItem>
            <DescriptionsItem label="更新时间">
              {{ formatTime(loopDetail.basicInfo.updatedAt) }}
            </DescriptionsItem>
            <DescriptionsItem label="AAS 最后同步">
              {{ formatTime(loopDetail.aasSyncStatus.lastSyncAt) }}
            </DescriptionsItem>
            <DescriptionsItem label="关联 Tag 数">
              {{ loopDetail.aasSyncStatus.associatedTagCount }}
            </DescriptionsItem>
            <DescriptionsItem label="备注">
              {{ loopDetail.basicInfo.remark || '—' }}
            </DescriptionsItem>
          </Descriptions>
        </Card>

        <!-- Tag 关联状态 -->
        <Card title="Tag 关联状态">
          <div
            v-if="loopDetail"
            class="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7"
          >
            <div
              v-for="slot in tagSlots"
              :key="slot.key"
              class="rounded border p-3 text-center"
              :class="
                loopDetail.tagMapping[slot.key].associated
                  ? 'border-green-200 bg-green-50'
                  : slot.required
                    ? 'border-red-200 bg-red-50'
                    : 'border-gray-200 bg-gray-50'
              "
            >
              <div class="mb-1 flex items-center justify-center gap-1">
                <span class="font-medium">{{ slot.label }}</span>
                <span v-if="slot.required" class="text-red-500">*</span>
              </div>
              <div
                v-if="loopDetail.tagMapping[slot.key].associated"
                class="text-xs text-green-600"
              >
                {{ loopDetail.tagMapping[slot.key].tagName }}
              </div>
              <div v-else class="text-xs text-gray-400">未关联</div>
            </div>
          </div>
        </Card>

        <!-- 中部：波形图 -->
        <Card title="PV/SP/OP 趋势波形">
          <template #extra>
            <div class="flex items-center gap-2">
              <span class="text-sm text-gray-500">时间范围：</span>
              <a-radio-group
                v-model:value="trendWindow"
                :options="trendWindowOptions"
                option-type="button"
                button-style="solid"
                size="small"
                @change="handleTrendWindowChange"
              />
            </div>
          </template>

          <Spin :spinning="monitorLoading">
            <div v-if="monitorDetail" class="space-y-3">
              <!-- 当前值快照 -->
              <div class="flex flex-wrap items-center gap-4 rounded border p-3">
                <div>
                  <span class="text-xs text-gray-400">PV</span>
                  <span class="ml-2 font-medium text-blue-600">
                    {{ monitorDetail.currentValues.pv ?? '—' }}
                  </span>
                  <QualityTag
                    :quality="monitorDetail.currentValues.pvQuality"
                    class="ml-2"
                  />
                </div>
                <div>
                  <span class="text-xs text-gray-400">SP</span>
                  <span class="ml-2 font-medium">
                    {{ monitorDetail.currentValues.sp ?? '—' }}
                  </span>
                </div>
                <div>
                  <span class="text-xs text-gray-400">OP</span>
                  <span class="ml-2 font-medium">
                    {{ monitorDetail.currentValues.op ?? '—' }}
                  </span>
                </div>
                <div>
                  <span class="text-xs text-gray-400">MODE</span>
                  <Tag
                    class="ml-2"
                    :color="
                      monitorDetail.currentValues.modeLabel === 'Auto'
                        ? 'green'
                        : 'orange'
                    "
                  >
                    {{ monitorDetail.currentValues.modeLabel || '—' }}
                  </Tag>
                </div>
                <div>
                  <span class="text-xs text-gray-400">读取时间</span>
                  <span class="ml-2 text-sm">
                    {{ formatTime(monitorDetail.currentValues.readAt) }}
                  </span>
                </div>
              </div>

              <!-- 波形图 -->
              <WaveformChart :trend="monitorDetail.trend" height="360px" />
            </div>
            <div v-else class="py-12 text-center text-gray-400">
              暂无趋势数据
            </div>
          </Spin>
        </Card>

        <!-- 底部：KPI 摘要 -->
        <Card title="KPI 摘要">
          <Spin :spinning="monitorLoading">
            <div v-if="monitorDetail">
              <!-- 综合评分 -->
              <div
                class="mb-4 flex items-center justify-between rounded border p-4"
              >
                <div>
                  <div class="text-xs text-gray-400">
                    综合评分（composite_score）
                  </div>
                  <div
                    class="mt-1 text-3xl font-bold"
                    :class="{
                      'text-green-600':
                        monitorDetail.kpiSummary.composite_score >= 80,
                      'text-orange-500':
                        monitorDetail.kpiSummary.composite_score >= 60 &&
                        monitorDetail.kpiSummary.composite_score < 80,
                      'text-red-500':
                        monitorDetail.kpiSummary.composite_score < 60,
                    }"
                  >
                    {{ monitorDetail.kpiSummary.composite_score?.toFixed(1) ?? '--' }}
                  </div>
                </div>
                <div class="text-right">
                  <div class="text-xs text-gray-400">KPI 状态</div>
                  <Tag
                    :color="
                      kpiStatusMap[monitorDetail.kpiSummary.status]?.color
                    "
                    class="mt-1"
                  >
                    {{
                      kpiStatusMap[monitorDetail.kpiSummary.status]?.label ||
                      monitorDetail.kpiSummary.status
                    }}
                  </Tag>
                  <div class="mt-1 text-xs text-gray-400">
                    算法版本：{{ monitorDetail.kpiSummary.algorithm_version }}
                  </div>
                  <div class="text-xs text-gray-400">
                    计算时间：{{
                      formatTime(monitorDetail.kpiSummary.calculatedAt)
                    }}
                  </div>
                </div>
              </div>

              <!-- 6 大 KPI 网格 -->
              <div class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
                <div
                  v-for="item in kpiItems"
                  :key="item.key"
                  class="rounded border p-3 text-center"
                >
                  <div class="text-xs text-gray-400">{{ item.label }}</div>
                  <div class="mt-1 text-xl font-medium">
                    {{
                      (monitorDetail.kpiSummary[item.key] as number | null)?.toFixed(1) ?? '--'
                    }}{{ item.unit }}
                  </div>
                  <div class="mt-1 text-xs text-gray-400">{{ item.desc }}</div>
                </div>
              </div>
            </div>
            <div v-else class="py-12 text-center text-gray-400">
              暂无 KPI 数据
            </div>
          </Spin>
        </Card>

        <!-- 返回按钮 -->
        <div class="flex justify-center">
          <Button @click="$router.back()">返回</Button>
        </div>
      </div>
    </Spin>
  </Page>
</template>
