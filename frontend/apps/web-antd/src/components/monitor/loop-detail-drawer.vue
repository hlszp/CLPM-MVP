<script lang="ts" setup>
/**
 * 回路详情抽屉（监控模块菜单重构 Phase1）
 *
 * 回路监视列表的"佐证"承载：列表只呈现干净结论（评分/等级/适用性），
 * 点击位号打开右侧抽屉查看回路基本信息、最新性能评估指标、
 * 性能等级与适用性等级及原因；需要深入处置时进入回路工作台。
 *
 * 数据来源为列表行记录（MonitorListItem），不额外发起请求——
 * kpiSummary 随列表接口返回，保证抽屉打开即完整。
 */
import type { LoopApi } from '#/api/loop';

import { computed } from 'vue';

import { Button, Drawer, Tag, Tooltip } from 'ant-design-vue';

import { ClpmFitnessBadge } from '#/components/clpm';
import {
  LOOP_TYPE_LABEL_MAP,
  MODE_LABEL_MAP,
} from '#/composables/use-loop-palettes';
import { fitnessTagToLabel } from '#/constants/clpm-ui';

defineOptions({ name: 'LoopDetailDrawer' });

const props = defineProps<{
  /** 当前回路（列表行记录；null 时抽屉内容留白） */
  loop: LoopApi.MonitorListItem | null;
  /** 抽屉开关 */
  open: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  /** 进入回路工作台（由父页面携带监控上下文导航） */
  (e: 'gotoWorkbench', loopId: string): void;
}>();

function close() {
  emit('update:open', false);
}

// ===== 性能等级五档（对齐 loop-fleet-view GRADE_CONFIG / GB/T 44693.2-2024 §6.3 默认阈值）=====
const GRADE_CONFIG = [
  { label: '优秀', minScore: 90, tagColor: 'green' },
  { label: '良好', minScore: 80, tagColor: 'blue' },
  { label: '合格', minScore: 60, tagColor: 'gold' },
  { label: '警告', minScore: 40, tagColor: 'orange' },
  { label: '不合格', minScore: 0, tagColor: 'red' },
] as const;

const grade = computed(() => {
  const score = props.loop?.score;
  if (score == null || Number.isNaN(score)) return null;
  for (const cfg of GRADE_CONFIG) {
    if (score >= cfg.minScore) return cfg;
  }
  return null;
});

// ===== 最新性能评估指标（kpiSummary；比率指标 0-1 → 百分比展示）=====
const kpiRows = computed(() => {
  const k = props.loop?.kpiSummary;
  if (!k) return [];
  const pct = (v: null | number | undefined) =>
    v == null || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(1)}%`;
  return [
    { label: '有效自控率', value: pct(k.effective_auto_rate) },
    { label: '平稳率', value: pct(k.steady_rate) },
    { label: '快速率', value: pct(k.fast_rate) },
    { label: '准确率', value: pct(k.accuracy_rate) },
    { label: '自控率', value: pct(k.auto_mode_rate) },
    { label: '好值率', value: pct(k.good_value_rate) },
    { label: '振荡率', value: pct(k.oscillation_rate) },
    { label: '饱和率', value: pct(k.saturation_rate) },
  ];
});

const kpiCalculatedAt = computed(() => {
  const at = props.loop?.kpiSummary?.calculatedAt;
  if (!at) return '';
  const d = new Date(at);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('zh-CN');
});

const fitnessTags = computed(() => props.loop?.fitnessTags ?? []);

function fmtNum(v: null | number | undefined): string {
  return v == null || Number.isNaN(v) ? '—' : v.toFixed(2);
}
</script>

<template>
  <Drawer
    :open="open"
    :width="440"
    placement="right"
    :closable="true"
    destroy-on-close
    :title="loop ? loop.tagName : '回路详情'"
    @close="close"
  >
    <template v-if="loop">
      <!-- 头部：描述 + 双等级结论 -->
      <div class="mb-3">
        <div class="text-xs text-gray-500">{{ loop.description || '—' }}</div>
        <div class="mt-2 flex items-center gap-2">
          <!-- 性能等级 -->
          <Tag v-if="grade" :color="grade.tagColor" class="m-0">
            性能：{{ grade.label }}
          </Tag>
          <Tag v-else color="default" class="m-0">性能：待评估</Tag>
          <!-- 适用性等级 -->
          <ClpmFitnessBadge
            :level="loop.fitnessLevel"
            size="sm"
            :show-label="true"
          />
          <!-- 可信度 -->
          <Tooltip v-if="loop.confidenceLevel" title="数据可信度等级">
            <Tag color="default" class="m-0"
              >可信度 {{ loop.confidenceLevel }}</Tag
            >
          </Tooltip>
        </div>
        <!-- 综合评分 -->
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-2xl font-bold tabular-nums">{{
            fmtNum(loop.score)
          }}</span>
          <span class="text-xs text-gray-400">综合评分（0-100）</span>
        </div>
      </div>

      <!-- 适用性原因 -->
      <div class="mb-3 rounded border border-gray-200 bg-gray-50/60 p-2.5">
        <div class="mb-1.5 text-xs font-bold text-gray-600">
          适用性等级及原因
        </div>
        <div v-if="loop.fitnessLevel" class="mb-1.5">
          <ClpmFitnessBadge
            :level="loop.fitnessLevel"
            size="sm"
            :show-label="true"
          />
        </div>
        <div v-if="fitnessTags.length > 0" class="flex flex-wrap gap-1">
          <Tag v-for="t in fitnessTags" :key="t" color="default" class="m-0">
            {{ fitnessTagToLabel(t) }}
          </Tag>
        </div>
        <div v-else class="text-xs text-gray-400">无异常原因标签</div>
      </div>

      <!-- 最新性能评估指标 -->
      <div class="mb-3 rounded border border-gray-200 p-2.5">
        <div class="mb-1.5 flex items-center text-xs font-bold text-gray-600">
          最新性能评估指标
          <span v-if="kpiCalculatedAt" class="ml-auto font-normal text-gray-400"
            >{{ kpiCalculatedAt }}</span
          >
        </div>
        <div v-if="kpiRows.length > 0" class="grid grid-cols-2 gap-1.5">
          <div
            v-for="row in kpiRows"
            :key="row.label"
            class="flex items-center justify-between rounded bg-gray-50 px-2 py-1"
          >
            <span class="text-xs text-gray-500">{{ row.label }}</span>
            <span class="font-mono text-xs font-bold text-gray-700">{{
              row.value
            }}</span>
          </div>
        </div>
        <div v-else class="py-2 text-center text-xs text-gray-300">
          暂无评估数据（KPI 快照未生成）
        </div>
      </div>

      <!-- 实时值 -->
      <div class="mb-3 rounded border border-gray-200 p-2.5">
        <div class="mb-1.5 text-xs font-bold text-gray-600">实时值</div>
        <div class="grid grid-cols-4 gap-1.5 text-center">
          <div class="rounded bg-gray-50 px-1 py-1.5">
            <div class="text-[10px] text-gray-400">SP</div>
            <div class="font-mono text-xs font-bold">
              {{ fmtNum(loop.currentValues?.sp) }}
            </div>
          </div>
          <div class="rounded bg-gray-50 px-1 py-1.5">
            <div class="text-[10px] text-gray-400">PV</div>
            <div class="font-mono text-xs font-bold">
              {{ fmtNum(loop.currentValues?.pv) }}
            </div>
          </div>
          <div class="rounded bg-gray-50 px-1 py-1.5">
            <div class="text-[10px] text-gray-400">OP(%)</div>
            <div class="font-mono text-xs font-bold">
              {{ fmtNum(loop.currentValues?.op) }}
            </div>
          </div>
          <div class="rounded bg-gray-50 px-1 py-1.5">
            <div class="text-[10px] text-gray-400">MODE</div>
            <div class="font-mono text-xs font-bold">
              {{
                loop.currentValues?.modeLabel ||
                MODE_LABEL_MAP[String(loop.currentValues?.mode)] ||
                '—'
              }}
            </div>
          </div>
        </div>
      </div>

      <!-- 基本信息 -->
      <div class="mb-3 rounded border border-gray-200 p-2.5">
        <div class="mb-1.5 text-xs font-bold text-gray-600">基本信息</div>
        <div class="space-y-1 text-xs">
          <div class="flex">
            <span class="w-20 flex-none text-gray-400">装置·单元</span>
            <span class="text-gray-700">{{ loop.unitName || '—' }}</span>
          </div>
          <div class="flex">
            <span class="w-20 flex-none text-gray-400">回路类型</span>
            <span class="text-gray-700">{{
              LOOP_TYPE_LABEL_MAP[loop.loopType ?? 'OTHER'] ?? '其他'
            }}</span>
          </div>
          <div class="flex">
            <span class="w-20 flex-none text-gray-400">测量量程</span>
            <span class="text-gray-700">
              {{
                loop.pvRange?.min != null || loop.pvRange?.max != null
                  ? `${loop.pvRange?.min ?? '—'} ~ ${loop.pvRange?.max ?? '—'}${loop.pvUnit ? ` ${loop.pvUnit}` : ''}`
                  : '—'
              }}
            </span>
          </div>
          <div class="flex">
            <span class="w-20 flex-none text-gray-400">回路状态</span>
            <span class="text-gray-700">{{ loop.loopStatus || '—' }}</span>
          </div>
          <div class="flex">
            <span class="w-20 flex-none text-gray-400">数据健康度</span>
            <span class="text-gray-700">
              {{
                loop.dataHealth?.validRate != null
                  ? `有效数据率 ${(loop.dataHealth.validRate * 100).toFixed(1)}%`
                  : '—'
              }}
            </span>
          </div>
        </div>
      </div>

      <!-- 底部操作 -->
      <div class="flex justify-end gap-2 border-t border-gray-100 pt-3">
        <Button size="small" @click="close">关闭</Button>
        <Button
          size="small"
          type="primary"
          @click="emit('gotoWorkbench', loop.loopId)"
        >
          进入回路工作台 →
        </Button>
      </div>
    </template>
    <div v-else class="py-8 text-center text-sm text-gray-300">暂无数据</div>
  </Drawer>
</template>
