/**
 * 装置总览页共享：定级阈值（配置化，禁硬编码）+ 数字格式化
 *
 * 原 workbench.vue 内联实现，随管理者版重排拆出，
 * 供 conclusion-cards / health-structure / node-ranking / perf-trend / focus-loops 共用。
 * 模块级单例：本页为唯一消费方，gradeCfgs 由 workbench.vue onMounted 时加载一次。
 */
import { computed, ref } from 'vue';

import { getGradingThresholdsApi } from '#/api';

/** 等级五档默认配置（/configs/grading-thresholds 加载失败时兜底） */
export const DEFAULT_GRADES = [
  { label: '优秀', color: '#1a7f4b', min: 95 },
  { label: '良好', color: '#2563eb', min: 85 },
  { label: '合格', color: '#b45309', min: 70 },
  { label: '警告', color: '#c23434', min: 60 },
  { label: '不合格', color: '#a12222', min: 0 },
];

/** 数字格式化（null/undefined → --） */
export function fmt(v: null | number | undefined, digits = 1): string {
  return v === null || v === undefined ? '--' : v.toFixed(digits);
}

// ================ 定级阈值 ================
export const gradeCfgs = ref([...DEFAULT_GRADES]);

export async function loadGradeCfgs() {
  try {
    const res = await getGradingThresholdsApi();
    const items = (res.thresholds ?? [])
      .filter((t) => Number.isFinite(t.minScore))
      .toSorted((a, b) => b.minScore - a.minScore)
      .map((t) => ({
        label: t.label || t.name,
        color:
          t.color ||
          DEFAULT_GRADES.find((g) => g.label === (t.label || t.name))?.color ||
          '#94a3b8',
        min: t.minScore,
      }));
    if (items.length > 0) gradeCfgs.value = items;
  } catch {
    /* 配置加载失败回落默认五档 */
  }
}

export function getGrade(score: null | number | undefined): {
  color: string;
  label: string;
  letter: string;
} {
  if (score === null || score === undefined) {
    return { label: '—', color: '#94a3b8', letter: '—' };
  }
  for (let i = 0; i < gradeCfgs.value.length; i++) {
    const g = gradeCfgs.value[i]!;
    if (score >= g.min) {
      return { ...g, letter: String.fromCodePoint(65 + i) }; // A=0,B=1...
    }
  }
  const last = gradeCfgs.value[gradeCfgs.value.length - 1]!;
  return {
    ...last,
    letter: String.fromCodePoint(65 + gradeCfgs.value.length - 1),
  };
}

/** 告警线阈值：警告等级（倒数第二档）的 minScore；默认60 */
export const warningThreshold = computed(() => {
  // 倒数第二档即"警告"等级；若配置不足两档则回落60
  const warn = gradeCfgs.value[gradeCfgs.value.length - 2];
  return warn?.min ?? 60;
});

/** 告警线颜色：取警告等级配置色，默认 #c23434 */
export const warningColor = computed(() => {
  const warn = gradeCfgs.value[gradeCfgs.value.length - 2];
  return warn?.color ?? '#c23434';
});
