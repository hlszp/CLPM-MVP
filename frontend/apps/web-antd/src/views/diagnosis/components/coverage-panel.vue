<script setup lang="ts">
import type { DiagnosisApi } from '#/api/diagnosis';

/**
 * 诊断健康度折叠块（16 号文 F3 · D6=a：诊断工作台概览区折叠块，
 * 默认展开，Calm UI 单行摘要 + 展开明细）。
 *
 * 结构：
 * - 头部单行摘要：5 档新鲜度计数（色点+计数），从未诊断计数徽标，
 *   点击折叠/展开明细
 * - 明细① 新鲜度占比条：5 档堆叠条（点击下钻诊断记录页），悬浮可见回路清单
 * - 明细② 调度执行（仅 ADMIN：后端非管理员返回 schedule=null，整块隐藏
 *   而非置灰）：1 级（每日）/2 级（每周）应跑 vs 滞后，3 级"不排程，仅手动"
 * - 明细③ 数据不足 Top5：近 30d DATA_INSUFFICIENT 占比最高回路（提示先补数据）
 *
 * 空态：无活跃回路 → 文字占位；加载失败 → Empty + 重试。
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Button, Empty, Spin } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisCoverageApi } from '#/api/diagnosis';

import {
  FRESHNESS_META,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
} from '../constants';

/** 语义色复用 FRESHNESS_META 色板（常量级共享，避免新增 hex 债务） */
const COLOR_OK = FRESHNESS_META.within24h.color;
const COLOR_LAG = FRESHNESS_META.stale.color;
const COLOR_DI = FRESHNESS_META.within30d.color;

const router = useRouter();

const loading = ref(false);
const data = ref<DiagnosisApi.CoverageResult | null>(null);
/** 默认展开（D6） */
const collapsed = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  try {
    data.value = await getDiagnosisCoverageApi();
  } catch {
    data.value = null; // 错误提示由请求拦截器统一弹出
  } finally {
    loading.value = false;
  }
}

onMounted(load);

/** 后端时间为 naive UTC ISO（无 Z 后缀），补 Z 后按本地时区展示 */
function fmtUtc(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso)
    ? naiveIso
    : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

const buckets = computed(() => data.value?.freshness.buckets ?? []);
const totalLoops = computed(() => data.value?.freshness.totalLoops ?? 0);
const neverCount = computed(
  () => buckets.value.find((b) => b.key === 'never')?.count ?? 0,
);

function bucketMeta(key: DiagnosisApi.FreshnessBucketKey) {
  return FRESHNESS_META[key];
}

function bucketPct(count: number): string {
  if (totalLoops.value <= 0) return '0%';
  return `${(count / totalLoops.value) * 100}%`;
}

/** 分档悬浮摘要：回路位号列表（最多列 8 个） */
function bucketTitle(b: DiagnosisApi.CoverageBucket): string {
  const meta = bucketMeta(b.key);
  const tags = b.loops.map((l) => l.loopTagName);
  const shown = tags.slice(0, 8).join('、');
  const more = tags.length > 8 ? ` 等 ${tags.length} 个` : '';
  return `${meta.label}：${b.count} 个回路${tags.length > 0 ? `（${shown}${more}）` : ''}\n点击查看诊断记录`;
}

/** 分档点击 → 下钻诊断记录页（13 号矩阵深链规范：/diagnosis/records） */
function drillRecords(b: DiagnosisApi.CoverageBucket): void {
  if (b.count <= 0) return;
  router.push({ path: '/diagnosis/records' });
}

const scheduleLevels = computed(() => data.value?.schedule?.levels ?? []);
const diTop = computed(() => data.value?.dataInsufficient.top ?? []);
const diWindowDays = computed(() => data.value?.dataInsufficient.windowDays ?? 30);

function pctText(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}
</script>

<template>
  <div class="coverage-panel">
    <!-- 头部：标题 + Calm UI 单行摘要 + 折叠开关 -->
    <div class="coverage-panel__head" @click="collapsed = !collapsed">
      <span class="coverage-panel__title">
        诊断健康度
        <span v-if="data" class="coverage-panel__total">
          共 {{ totalLoops }} 个回路
        </span>
      </span>
      <span v-if="data && totalLoops > 0" class="coverage-panel__summary">
        <span
          v-for="b in buckets"
          :key="b.key"
          class="coverage-panel__chip"
          :title="bucketTitle(b)"
        >
          <i
            class="coverage-panel__dot"
            :style="{ backgroundColor: bucketMeta(b.key).color }"
          ></i>
          {{ bucketMeta(b.key).label }} {{ b.count }}
        </span>
        <span
          v-if="neverCount > 0"
          class="coverage-panel__never-badge"
          :style="{ color: COLOR_LAG }"
        >
          从未诊断 {{ neverCount }}
        </span>
      </span>
      <span class="coverage-panel__toggle">
        {{ collapsed ? '展开 ▾' : '收起 ▴' }}
      </span>
    </div>

    <!-- 展开明细 -->
    <div v-if="!collapsed" class="coverage-panel__body">
      <Spin :spinning="loading" size="small">
        <Empty
          v-if="!loading && !data"
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
          description="覆盖台账加载失败"
        >
          <Button size="small" @click="load">重试</Button>
        </Empty>
        <div v-else-if="!loading && data && totalLoops === 0" class="coverage-empty">
          暂无活跃回路，覆盖台账无统计数据
        </div>

        <template v-else-if="data">
          <!-- ① 新鲜度占比条 -->
          <div class="coverage-sec">
            <div class="coverage-sec__title">
              新鲜度分布（按各回路最新成功诊断时间）
            </div>
            <div class="coverage-bar">
              <div
                v-for="b in buckets"
                :key="b.key"
                class="coverage-bar__seg"
                :class="{ 'coverage-bar__seg--clickable': b.count > 0 }"
                :style="{
                  width: bucketPct(b.count),
                  backgroundColor: bucketMeta(b.key).color,
                }"
                :title="bucketTitle(b)"
                @click.stop="drillRecords(b)"
              ></div>
            </div>
            <div class="coverage-bar__legend">
              <span
                v-for="b in buckets"
                :key="`lg-${b.key}`"
                class="coverage-panel__chip"
                :title="bucketTitle(b)"
              >
                <i
                  class="coverage-panel__dot"
                  :style="{ backgroundColor: bucketMeta(b.key).color }"
                ></i>
                {{ bucketMeta(b.key).label }} {{ b.count }}
                <span class="coverage-bar__pct">
                  （{{ totalLoops > 0 ? Math.round((b.count / totalLoops) * 100) : 0 }}%）
                </span>
              </span>
            </div>
          </div>

          <!-- ② 调度执行（仅 ADMIN；后端 schedule=null 时整块隐藏） -->
          <div v-if="data.schedule" class="coverage-sec">
            <div class="coverage-sec__title">调度执行（分级定时诊断）</div>
            <div
              v-for="lv in scheduleLevels"
              :key="lv.level"
              class="coverage-level"
            >
              <span
                class="coverage-level__name"
                :style="{ color: IMPORTANCE_LEVEL_COLOR[lv.level] }"
              >
                {{ IMPORTANCE_LEVEL_TEXT[lv.level] ?? `${lv.level}级` }}
              </span>
              <span class="coverage-level__cadence">{{ lv.cadenceLabel }}</span>
              <template v-if="lv.cadence !== 'manual'">
                <span>应跑 {{ lv.expectedLoops }}</span>
                <span
                  class="coverage-level__lag-flag"
                  :style="{
                    color: (lv.laggingCount ?? 0) > 0 ? COLOR_LAG : COLOR_OK,
                  }"
                >
                  滞后 {{ lv.laggingCount ?? 0 }}
                </span>
                <span class="coverage-level__last">
                  最近排程 {{ fmtUtc(lv.lastScheduledAt) }} · 阈值
                  {{ lv.lagThresholdHours }}h
                </span>
              </template>
              <span v-else class="coverage-level__last">{{ lv.note }}</span>
              <!-- 滞后回路列表 -->
              <div
                v-if="(lv.lagging?.length ?? 0) > 0"
                class="coverage-level__lagging"
                :style="{ color: COLOR_LAG }"
              >
                <span
                  v-for="l in lv.lagging"
                  :key="l.loopId"
                  class="coverage-level__lag-item"
                >
                  {{ l.loopTagName }}（{{
                    l.lastScheduledAt ? fmtUtc(l.lastScheduledAt) : '从未排程'
                  }}）
                </span>
              </div>
            </div>
          </div>

          <!-- ③ 数据不足 Top5（近 30d DATA_INSUFFICIENT 占比） -->
          <div class="coverage-sec">
            <div class="coverage-sec__title">
              数据不足 Top5（近 {{ diWindowDays }} 天 DATA_INSUFFICIENT 占比）
            </div>
            <div v-if="diTop.length === 0" class="coverage-empty">
              近 {{ diWindowDays }} 天无 DATA_INSUFFICIENT 记录
            </div>
            <div v-else class="coverage-di">
              <div v-for="t in diTop" :key="t.loopId" class="coverage-di__row">
                <span class="coverage-di__tag">{{ t.loopTagName ?? t.loopId }}</span>
                <span class="coverage-di__nums tabular-nums">
                  {{ t.insufficientRuns }}/{{ t.totalRuns }} 次
                </span>
                <span
                  class="coverage-di__ratio tabular-nums"
                  :style="{ color: COLOR_DI }"
                >
                  {{ pctText(t.ratio) }}
                </span>
                <span class="coverage-di__hint">建议先补齐数据再诊断</span>
              </div>
            </div>
          </div>
        </template>
      </Spin>
    </div>
  </div>
</template>

<style scoped>
.coverage-panel {
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.coverage-panel__head {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  padding: 6px 12px;
  cursor: pointer;
  user-select: none;
}

.coverage-panel__title {
  font-size: 12px;
  font-weight: 600;
}

.coverage-panel__total {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
}

.coverage-panel__summary {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.coverage-panel__chip {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  white-space: nowrap;
}

.coverage-panel__dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.coverage-panel__never-badge {
  padding: 0 6px;
  font-size: 11px;
  font-weight: 500;
  background: hsl(var(--accent));
  border-radius: 8px;
}

.coverage-panel__toggle {
  margin-left: auto;
  font-size: 11px;
  color: hsl(var(--primary));
}

.coverage-panel__body {
  padding: 4px 12px 10px;
  border-top: 1px solid hsl(var(--border));
}

.coverage-sec {
  margin-top: 8px;
}

.coverage-sec__title {
  margin-bottom: 4px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.coverage-empty {
  padding: 6px 0;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

/* 新鲜度占比条 */
.coverage-bar {
  display: flex;
  height: 12px;
  overflow: hidden;
  background: hsl(var(--accent) / 40%);
  border-radius: 4px;
}

.coverage-bar__seg {
  min-width: 0;
  height: 100%;
}

.coverage-bar__seg--clickable {
  cursor: pointer;
}

.coverage-bar__seg--clickable:hover {
  filter: brightness(1.15);
}

.coverage-bar__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 14px;
  margin-top: 4px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.coverage-bar__pct {
  color: hsl(var(--muted-foreground) / 70%);
}

/* 调度执行 */
.coverage-level {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: baseline;
  padding: 3px 0;
  font-size: 12px;
}

.coverage-level__name {
  font-weight: 600;
}

.coverage-level__cadence {
  color: hsl(var(--muted-foreground));
}

.coverage-level__lag-flag {
  font-weight: 500;
}

.coverage-level__last {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.coverage-level__lagging {
  flex-basis: 100%;
  padding-left: 8px;
  font-size: 11px;
}

.coverage-level__lag-item {
  margin-right: 12px;
}

/* 数据不足 Top5 */
.coverage-di__row {
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 2px 0;
  font-size: 12px;
}

.coverage-di__tag {
  min-width: 90px;
  font-weight: 500;
}

.coverage-di__nums {
  color: hsl(var(--muted-foreground));
}

.coverage-di__ratio {
  font-weight: 600;
}

.coverage-di__hint {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}
</style>
