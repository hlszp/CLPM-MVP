<script setup lang="ts">
import type { DiagnosisApi } from '#/api/diagnosis';

/**
 * 诊断历史抽屉 —— 单回路诊断历史（纵向地铁进度条时间线，倒序）。
 *
 * 概览列表"历史"操作专用（2026-08-18）：调 GET /runs?loopId= 分页拉取，
 * 倒序展示每次诊断的结论 / 置信度 / 诊断时间；首节点高亮为"最新"。
 */
import { ref, watch } from 'vue';

import { Drawer, Empty, Spin } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisRunsApi } from '#/api/diagnosis';

import { CATEGORY_META } from '../constants';

const props = defineProps<{
  loopId: null | string;
  loopTagName?: null | string;
}>();

const open = defineModel<boolean>('open', { default: false });

const loading = ref(false);
const items = ref<DiagnosisApi.RunListItem[]>([]);
const total = ref(0);

const PAGE_SIZE = 50;

/** naive UTC → 本地时间（对齐断流时段修复口径：补 Z 解析） */
function fmtLocal(naiveUtc?: null | string): string {
  if (!naiveUtc) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveUtc)
    ? naiveUtc
    : `${naiveUtc}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

async function load(loopId: string) {
  loading.value = true;
  try {
    const res = await getDiagnosisRunsApi({
      loopId,
      page: 1,
      pageSize: PAGE_SIZE,
    });
    items.value = res.items;
    total.value = res.total;
  } finally {
    loading.value = false;
  }
}

watch(open, (v) => {
  if (v && props.loopId) {
    load(props.loopId);
  }
});

function catColor(record: DiagnosisApi.RunListItem): string {
  return record.primaryCategory
    ? (CATEGORY_META[record.primaryCategory]?.color ?? '#6c757d')
    : '#6c757d';
}
</script>

<template>
  <Drawer
    v-model:open="open"
    :title="`诊断历史 · ${loopTagName ?? ''}（共 ${total} 次）`"
    width="480"
    :destroy-on-close="true"
  >
    <Spin :spinning="loading">
      <Empty
        v-if="!loading && items.length === 0"
        description="该回路暂无诊断记录"
      />
      <!-- 纵向地铁进度条：左侧轨道线 + 节点圆点，倒序（最新在顶部） -->
      <div v-else class="diag-history">
        <div
          v-for="(rec, idx) in items"
          :key="rec.id"
          class="diag-history__item"
        >
          <div class="diag-history__rail">
            <span
              class="diag-history__dot"
              :class="{ 'diag-history__dot--latest': idx === 0 }"
              :style="{ borderColor: catColor(rec) }"
            ></span>
          </div>
          <div class="diag-history__body">
            <div class="diag-history__head">
              <span v-if="idx === 0" class="diag-history__badge">最新</span>
              <span class="diag-history__time">{{
                fmtLocal(rec.createdAt)
              }}</span>
            </div>
            <div class="diag-history__cat" :style="{ color: catColor(rec) }">
              {{ rec.primaryCategoryLabel ?? '未判定' }}
              <span class="diag-history__conf">
                置信度
                {{
                  rec.primaryConfidence == null
                    ? '—'
                    : `${Math.round(rec.primaryConfidence * 100)}%`
                }}
              </span>
            </div>
            <div class="diag-history__meta">
              {{ rec.triggerTypeLabel ?? '手动诊断' }}
              <template v-if="rec.reviewStatus === 'REVIEWED'">
                · 已复核{{
                  rec.reviewResultLabels?.length
                    ? `：${rec.reviewResultLabels.join('、')}`
                    : ''
                }}
              </template>
            </div>
          </div>
        </div>
      </div>
    </Spin>
  </Drawer>
</template>

<style scoped>
.diag-history {
  display: flex;
  flex-direction: column;
}

.diag-history__item {
  position: relative;
  display: flex;
  gap: 12px;
  padding-bottom: 20px;
}

/* 左侧轨道线（地铁进度条主线） */
.diag-history__rail {
  position: relative;
  display: flex;
  flex-shrink: 0;
  justify-content: center;
  width: 14px;
}

.diag-history__item:not(:last-child) .diag-history__rail::after {
  position: absolute;
  top: 14px;
  bottom: -6px;
  width: 2px;
  content: '';
  background: hsl(var(--border));
}

.diag-history__dot {
  z-index: 1;
  box-sizing: border-box;
  width: 12px;
  height: 12px;
  margin-top: 3px;
  background: #fff;
  border: 3px solid #6c757d;
  border-radius: 50%;
}

.diag-history__dot--latest {
  width: 14px;
  height: 14px;
  margin-top: 2px;
  box-shadow: 0 0 0 3px rgb(0 0 0 / 6%);
}

.diag-history__body {
  flex: 1;
  min-width: 0;
}

.diag-history__head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 2px;
}

.diag-history__badge {
  padding: 0 6px;
  font-size: 11px;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 10%);
  border-radius: 4px;
}

.diag-history__time {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--accent-foreground) / 55%);
}

.diag-history__cat {
  font-size: 13px;
  font-weight: 500;
}

.diag-history__conf {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: hsl(var(--accent-foreground) / 55%);
}

.diag-history__meta {
  margin-top: 2px;
  font-size: 12px;
  color: hsl(var(--accent-foreground) / 45%);
}
</style>
