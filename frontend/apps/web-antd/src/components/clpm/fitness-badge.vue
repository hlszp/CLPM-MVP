<script lang="ts" setup>
/**
 * ClpmFitnessBadge - 回路适用性 L0~L4 标签
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §3.5
 * 采用「图标 + 1px描边 + 透明背景」，不使用五色渐变：
 * - L0 不可评估（数据不足）  slate 灰 + 数据库图标
 * - L1 仅可监视（手动主导）  slate 灰 + 眼睛图标
 * - L2 条件异常（饱和/偏离）amber 琥珀 + 警告三角
 * - L3 待激励（无激励/弱响应）blue 蓝 + 脉冲虚线
 * - L4 可优化（可诊断+可整定）emerald 绿 + 勾选
 *
 * L0/L1 使用中性灰而非红色："不可评估" ≠ "坏"。
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Tooltip } from 'ant-design-vue';

defineOptions({ name: 'ClpmFitnessBadge' });

const props = withDefaults(
  defineProps<{
    /** 适用性等级；null/undefined/空字符串 → 展示 "待评估" 中性灰 */
    level?: FitnessLevel | null | string;
    /** 是否显示中文标签文字（false 只显示图标） */
    showLabel?: boolean;
    /** 大小：sm(紧凑) / md(默认) */
    size?: 'md' | 'sm';
    /** 判定原因 tags（字符串数组，与 fitness_tags 口径一致）；
     *  建议传英文 TAG（DATA_INSUFFICIENT/OP_SATURATED/...），组件内部转中文 */
    tags?: null | string[];
    /** 自定义 Tooltip 详情；优先级 > tags */
    tip?: string;
  }>(),
  { level: null, size: 'md', showLabel: true, tags: null, tip: '' },
);

type FitnessLevel = 'L0' | 'L1' | 'L2' | 'L3' | 'L4';

const TAG_HUMAN: Record<string, string> = {
  DATA_INSUFFICIENT: '数据不足',
  MANUAL_DOMINANT: '手动模式主导（>80%）',
  LOW_AUTO_RATE: '自控率偏低（<20%）',
  OP_SATURATED: 'OP 在量程±2%内时间>30%（输出饱和）',
  SP_PV_DEVIATION: '|SP-PV|>量程10% 时间>30%（设定偏离）',
  NO_EXCITATION: 'OP 变化范围 < 量程2%（无激励）',
  WEAK_RESPONSE: 'PV 对 OP 响应增益低于阈值（弱响应）',
};

interface LevelMeta {
  label: string;
  shortLabel: string;
  icon: string;
  color: string;
  desc: string;
}

const UNKNOWN_META: LevelMeta = {
  label: '待评估',
  shortLabel: '—',
  icon: 'lucide:help-circle',
  color: 'var(--color-slate-400)',
  desc: '暂无适用性分层结果（KPI 尚未计算）',
};

const LEVEL_META: Record<FitnessLevel, LevelMeta> = {
  L0: {
    label: '不可评估',
    shortLabel: 'L0',
    icon: 'lucide:database-off',
    color: 'var(--color-slate-500)',
    desc: '数据严重不足或质量极差，不计算 KPI，不允许进入诊断/整定。',
  },
  L1: {
    label: '仅可监视',
    shortLabel: 'L1',
    icon: 'lucide:eye',
    color: 'var(--color-slate-500)',
    desc: '有实时数据但历史不足，或手动主导/自控率过低，仅用于监视，不允许诊断/整定。',
  },
  L2: {
    label: '条件异常',
    shortLabel: 'L2',
    icon: 'lucide:triangle-alert',
    color: 'var(--color-amber-600)',
    desc: 'OP 饱和或 SP-PV 持续大偏离；可发起诊断但结果含「条件异常」横幅，不允许整定。',
  },
  L3: {
    label: '待激励',
    shortLabel: 'L3',
    icon: 'lucide:circle-dot-dashed',
    color: 'var(--color-blue-600)',
    desc: '数据/控制正常，但无有效激励或响应偏弱；允许诊断，需补充激励才可整定。',
  },
  L4: {
    label: '可优化',
    shortLabel: 'L4',
    icon: 'lucide:check',
    color: 'var(--color-emerald-600)',
    desc: '数据充分、控制正常、有有效激励，诊断/整定全链路开放。',
  },
};

const normalizedLevel = computed<FitnessLevel | null>(() => {
  const lv = props.level;
  if (lv === 'L0' || lv === 'L1' || lv === 'L2' || lv === 'L3' || lv === 'L4') {
    return lv;
  }
  return null;
});

const meta = computed<LevelMeta>(() => {
  if (!normalizedLevel.value) return UNKNOWN_META;
  return LEVEL_META[normalizedLevel.value];
});

const humanTags = computed(() => {
  if (!Array.isArray(props.tags)) return [];
  const out: string[] = [];
  for (const t of props.tags) {
    out.push(TAG_HUMAN[t] ?? t);
  }
  return out;
});

const tooltipText = computed(() => {
  if (props.tip) return props.tip;
  const header = `${meta.value.shortLabel} / ${meta.value.label}`;
  const tagsText =
    humanTags.value.length > 0 ? `\n原因：${humanTags.value.join('；')}` : '';
  return `${header} — ${meta.value.desc}${tagsText}`;
});

const iconSize = computed(() => (props.size === 'sm' ? 11 : 12));

const tagStyle = computed(() => ({
  color: meta.value.color,
  borderColor: meta.value.color,
  background: 'transparent',
  margin: 0,
  padding: props.size === 'sm' ? '0 5px' : '0 8px',
  fontSize: props.size === 'sm' ? '11px' : '12px',
  lineHeight: props.size === 'sm' ? '18px' : '20px',
  borderRadius: '3px',
  fontWeight: 600,
  whiteSpace: 'nowrap',
  display: 'inline-flex',
  alignItems: 'center',
  gap: '4px',
  border: '1px solid',
  cursor: 'help',
}));
</script>

<template>
  <Tooltip :title="tooltipText" placement="top">
    <span :style="tagStyle">
      <IconifyIcon :icon="meta.icon" :size="iconSize" style="vertical-align: -2px" />
      <template v-if="showLabel">{{ meta.label }}</template>
    </span>
  </Tooltip>
</template>
