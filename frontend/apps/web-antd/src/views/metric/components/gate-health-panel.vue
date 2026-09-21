<script setup lang="ts">
/**
 * 评估门禁健康面板（0921 监控面板）。
 *
 * 回答"哪些回路为何没有评估得分"：
 * - 覆盖摘要：有得分 / 门禁失败 / 无快照（轮次没算到）三段计数
 * - 断点比例分布：5 档横条，30% 门槛线可视标注（门槛右一档=门禁失败区）
 * - 失败原因榜：按 reason 计数降序
 * - TOP 榜：断点比例最高的回路（回路/断点比例/等级/原因）
 *
 * Calm UI：默认折叠单行摘要，点击展开明细（对齐 DiagnosisCoveragePanel）。
 */
import type { MetricApi } from '#/api/metric';

import { computed, onActivated, onMounted, ref } from 'vue';

import { Empty, Spin, Table, Tooltip } from 'ant-design-vue';

import { getGateOverviewApi } from '#/api/metric';

const loading = ref(false);
const data = ref<MetricApi.GateOverview | null>(null);
/** 默认折叠（D6） */
const collapsed = ref(true);

async function load(): Promise<void> {
  loading.value = true;
  try {
    data.value = await getGateOverviewApi();
  } catch {
    data.value = null; // 错误提示由请求拦截器统一弹出
  } finally {
    loading.value = false;
  }
}

onMounted(load);
onActivated(load);

const coverage = computed(() => data.value?.coverage);
const gate = computed(() => data.value?.gate);
const threshold = computed(() => data.value?.threshold);

/** 摘要行：有得分 x · 门禁失败 y · 无快照 z / 总数 */
const summaryText = computed(() => {
  const c = coverage.value;
  if (!c) return '—';
  return `有得分 ${c.scored} · 门禁失败 ${gate.value?.failed ?? 0} · 无快照 ${c.noSnapshot} / ${c.totalLoops}`;
});

/** 分档健康色：门槛内绿系、门槛档琥珀、超标红系 */
function bucketColor(b: { from: number; to: number }): string {
  const t = threshold.value?.maxGapRatio ?? 0.3;
  if (b.to <= t * 0.66) return 'hsl(140 60% 42%)';
  if (b.to <= t) return 'hsl(45 80% 45%)';
  return 'hsl(8 72% 48%)';
}

const bucketMax = computed(() =>
  Math.max(1, ...(data.value?.gapBuckets ?? []).map((b) => b.count)),
);

const offenderColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 150 },
  { dataIndex: 'gapRatioText', title: '断点比例', width: 90 },
  { dataIndex: 'fitnessLevel', title: '适用性', width: 70 },
  { dataIndex: 'reason', title: '门禁未过原因', ellipsis: true },
];

const offenderRows = computed(() =>
  (data.value?.topOffenders ?? []).map((o) => ({
    ...o,
    gapRatioText: `${Math.round(o.gapRatio * 100)}%`,
  })),
);
</script>

<template>
  <div class="gate-panel">
    <div class="gate-panel__head" @click="collapsed = !collapsed">
      <span class="gate-panel__title">
        评估门禁健康
        <span v-if="coverage" class="gate-panel__total">
          （门槛：断点 ≤
          {{ Math.round((threshold?.maxGapRatio ?? 0.3) * 100) }}%）
        </span>
      </span>
      <span v-if="coverage" class="gate-panel__summary">
        {{ summaryText }}
      </span>
      <span class="gate-panel__toggle">
        {{ collapsed ? '展开 ▾' : '收起 ▴' }}
      </span>
    </div>

    <div v-if="!collapsed" class="gate-panel__body">
      <Spin :spinning="loading" size="small">
        <Empty
          v-if="!loading && !data"
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
          description="门禁数据加载失败"
        />
        <template v-else-if="data">
          <!-- ① 断点比例分布（门槛线右侧=门禁失败区） -->
          <div class="gate-sec">
            <div class="gate-sec__title">
              断点比例分布（每回路最新一次评估，门槛
              {{ Math.round((threshold?.maxGapRatio ?? 0.3) * 100) }}%）
            </div>
            <div class="gate-buckets">
              <Tooltip
                v-for="b in data.gapBuckets"
                :key="b.label"
                :title="`${b.label}：${b.count} 个回路`"
              >
                <div class="gate-bucket">
                  <div class="gate-bucket__bar-wrap">
                    <div
                      class="gate-bucket__bar"
                      :class="{
                        'gate-bucket__bar--over':
                          b.from >= (threshold?.maxGapRatio ?? 0.3),
                      }"
                      :style="{
                        width: `${(b.count / bucketMax) * 100}%`,
                        backgroundColor: bucketColor(b),
                      }"
                    ></div>
                  </div>
                  <div class="gate-bucket__label">
                    {{ b.label
                    }}<span class="gate-bucket__n">{{ b.count }}</span>
                  </div>
                </div>
              </Tooltip>
            </div>
            <div class="gate-legend">
              <span
                >门槛线：断点比例 ≤
                {{ Math.round((threshold?.maxGapRatio ?? 0.3) * 100) }}%
                才允许出分；右侧两档（红）= 门禁失败区</span
              >
            </div>
          </div>

          <!-- ② 门禁失败原因榜 -->
          <div v-if="gate && gate.failed > 0" class="gate-sec">
            <div class="gate-sec__title">
              门禁失败原因（共 {{ gate.failed }} 个回路未过）
            </div>
            <div
              v-for="(n, reason) in gate.failReasons"
              :key="reason"
              class="gate-reason"
            >
              <span class="gate-reason__n">{{ n }}</span>
              <span class="gate-reason__text">{{ reason }}</span>
            </div>
          </div>

          <!-- ③ 断点比例 TOP 榜 -->
          <div v-if="offenderRows.length > 0" class="gate-sec">
            <div class="gate-sec__title">断点比例 TOP 榜（最缺数据的回路）</div>
            <Table
              :columns="offenderColumns"
              :data-source="offenderRows"
              :pagination="false"
              row-key="loopTagName"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.dataIndex === 'gapRatioText'">
                  <span class="font-medium tabular-nums text-red-600">
                    {{ record.gapRatioText }}
                  </span>
                </template>
              </template>
            </Table>
          </div>

          <!-- ④ 无快照提示 -->
          <div v-if="coverage && coverage.noSnapshot > 0" class="gate-note">
            另有 {{ coverage.noSnapshot }} 个活跃回路近
            {{ '26' }} 小时无评估快照（计算轮次未覆盖或数据完全缺失）——检查 KPI
            计算任务是否在时限内完成。
          </div>
        </template>
      </Spin>
    </div>
  </div>
</template>

<style scoped>
.gate-panel {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.gate-panel__head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  padding: 6px 12px;
  cursor: pointer;
  user-select: none;
}

.gate-panel__title {
  font-size: 12px;
  font-weight: 600;
}

.gate-panel__total {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}

.gate-panel__summary {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.gate-panel__toggle {
  margin-left: auto;
  font-size: 11px;
  color: hsl(var(--primary));
}

.gate-panel__body {
  padding: 4px 12px 10px;
  border-top: 1px solid hsl(var(--border));
}

.gate-sec {
  margin-top: 8px;
}

.gate-sec__title {
  margin-bottom: 4px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

/* 断点比例分档横条 */
.gate-bucket {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 1px 0;
}

.gate-bucket__bar-wrap {
  flex: 0 0 200px;
  height: 10px;
  background: hsl(var(--accent) / 40%);
  border-radius: 3px;
}

.gate-bucket__bar {
  height: 100%;
  border-radius: 3px;
}

.gate-bucket__label {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.gate-bucket__n {
  margin-left: 6px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.gate-legend {
  margin-top: 4px;
  font-size: 10px;
  color: hsl(var(--muted-foreground) / 80%);
}

/* 失败原因榜 */
.gate-reason {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 1px 0;
  font-size: 12px;
}

.gate-reason__n {
  flex: 0 0 32px;
  font-weight: 600;
}

.gate-reason__text {
  color: hsl(var(--muted-foreground));
}

.gate-note {
  padding: 6px 8px;
  margin-top: 8px;
  font-size: 11px;
  color: hsl(35 80% 30%);
  background: hsl(45 90% 50% / 8%);
  border-radius: 4px;
}
</style>
