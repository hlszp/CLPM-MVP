<script lang="ts" setup>
import type { TableColumnsType } from 'ant-design-vue';

/**
 * S3-METRIC-007 性能指标定义页（v6.1 修订）
 *
 * 12 项 KPI 指标定义直接写入（对齐 GB/T 44693.2-2024 + 3+1+8 体系）：
 * - CORE（核心质量）：准确率 A / 快速率 F / 稳定率 S — 参与综合评分加权
 * - COMMISSIONING（投用）：有效自控率 R — 综合评分折扣因子
 * - AUXILIARY_DIAGNOSTIC（辅助诊断）：好值率/自控率/振荡率/饱和率/稳态时间/理想稳态时间/粘滞指数/输出行程指数
 */
import { Page } from '@vben/common-ui';

import { Alert, Card, Table, Tag } from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'MetricConfigDefinition' });

const { themeColors } = useClpmTheme();

// ===== 类型定义 =====

type MetricCategory = 'AUXILIARY_DIAGNOSTIC' | 'COMMISSIONING' | 'CORE';

interface MetricDefinition {
  metricCode: string;
  metricName: string;
  category: MetricCategory;
  formula: string;
  description: string;
  unit?: string;
}

// ===== 指标类别配置 =====

const categoryConfig: Record<
  MetricCategory,
  { color: string; label: string; order: number }
> = {
  CORE: { color: 'default', label: '核心质量', order: 0 },
  COMMISSIONING: { color: 'default', label: '投用', order: 1 },
  AUXILIARY_DIAGNOSTIC: { color: 'default', label: '辅助诊断', order: 2 },
};

// ===== 12 项 KPI 指标定义（对齐 GB/T 44693.2-2024 + 3+1+8 体系）=====

const metricList: MetricDefinition[] = [
  // --- CORE 核心质量（3 项）---
  {
    metricCode: 'ACCURACY_RATE',
    metricName: '准确率',
    category: 'CORE',
    formula: 'max(0, (1 - mean_abs_error / e_max)) × 100',
    description:
      '衡量 PV 与 SP 的偏离程度。mean_abs_error 为评估窗内 |PV-SP| 均值，e_max 为工艺允许最大偏差。对齐 GB/T 44693.2-2024 §6.4.2。',
  },
  {
    metricCode: 'FAST_RATE',
    metricName: '快速率',
    category: 'CORE',
    formula: 'ideal_settling_time / actual_settling_time × 100',
    description:
      '衡量回路响应速度。理想稳态时间与实际稳态时间之比，基于 ARMA 模型辨识 + Green 函数法计算。对齐 GB/T 44693.2-2024 §6.4.3。',
  },
  {
    metricCode: 'STEADY_RATE',
    metricName: '稳定率',
    category: 'CORE',
    formula: 'max(0, (1 - osc_rate - k×std_norm) / (1 - osc_rate)) × 100',
    description:
      '衡量回路在稳态下的波动程度。结合振荡率与标准化标准差综合评定。对齐 GB/T 44693.2-2024 §6.4.4。',
  },
  // --- COMMISSIONING 投用（1 项）---
  {
    metricCode: 'EFFECTIVE_AUTO_RATE',
    metricName: '有效自控率',
    category: 'COMMISSIONING',
    formula:
      'count(auto AND op NOT saturated AND pv_quality=Good) / count(*) × 100',
    description:
      '综合考量自动模式、输出未饱和、PV 质量良好三个条件同时满足的占比。作为综合评分的折扣因子 R，非加权项。对齐 GB/T 44693.2-2024 §6.4.5。',
  },
  // --- AUXILIARY_DIAGNOSTIC 辅助诊断（8 项）---
  {
    metricCode: 'GOOD_VALUE_RATE',
    metricName: '好值率',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'count(pv_quality=Good) / count(*) × 100',
    description:
      'PV 质量码为 Good 的数据点占比。支持 TDengine schema（1=Good）和 OPC DA（192=Good）两种质量码体系。',
  },
  {
    metricCode: 'AUTO_MODE_RATE',
    metricName: '自控率',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'count(mode IN (Auto, Cascade, Remote)) / count(*) × 100',
    description:
      '回路处于自动模式（Auto/Cascade/Remote）的时长占比。投用定义可按回路单独配置（loop_mode_mapping）。',
  },
  {
    metricCode: 'OSCILLATION_RATE',
    metricName: '振荡率',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'min(S_A, S_B) × 100',
    description:
      '基于 IAE 零交叉相似性法检测振荡。S_A、S_B 分别为归一化后的 IAE 积分特征值。对齐 GB/T 44693.2-2024 §6.4.6。',
  },
  {
    metricCode: 'SATURATION_RATE',
    metricName: '饱和率',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'saturated_duration / total_duration × 100',
    description:
      'OP 输出处于饱和区间（高限或低限）的时长占比。饱和判定阈值可配置，默认 ±2% 量程范围。',
  },
  {
    metricCode: 'SETTLING_TIME',
    metricName: '稳态时间',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'arma_green_function_settling_time',
    description:
      '基于 ARMA 模型辨识与 Green 函数法计算的回路实际稳态时间（秒）。需输入阶跃响应或扰动恢复数据段。',
    unit: 's',
  },
  {
    metricCode: 'IDEAL_SETTLING_TIME',
    metricName: '理想稳态时间',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'α × (τ + θ) 或按控制类型默认值',
    description:
      '按控制类型（稳定型/慢速型/快速型/逻辑型）的理想稳态时间。α 为系数，τ 为时间常数，θ 为纯滞后时间。',
    unit: 's',
  },
  {
    metricCode: 'STICTION_INDEX',
    metricName: '粘滞指数',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'cross_correlation_based_stiction_detection',
    description:
      '基于互相关分析的阀门粘滞检测指数。值域 [0, 1]，>0.5 提示存在粘滞。对齐 Choudhury-Horch-Shah 方法。',
  },
  {
    metricCode: 'OUTPUT_TRIP_INDEX',
    metricName: '输出行程指数',
    category: 'AUXILIARY_DIAGNOSTIC',
    formula: 'std(op_diff) / range',
    description:
      'OP 输出变化量的标准差与量程之比，衡量阀门动作频繁程度。值过大提示可能存在整定不当或噪声干扰。',
  },
];

// ===== 表格列定义 =====

const columns: TableColumnsType = [
  {
    title: '指标名称',
    dataIndex: 'metricName',
    key: 'metricName',
    width: 120,
    fixed: 'left',
  },
  {
    title: '指标代码',
    dataIndex: 'metricCode',
    key: 'metricCode',
    width: 180,
  },
  {
    title: '类别',
    key: 'category',
    width: 110,
  },
  {
    title: '算法公式',
    dataIndex: 'formula',
    key: 'formula',
    ellipsis: true,
  },
  {
    title: '说明',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
];

// ===== 工具函数 =====

function categoryColor(cat: MetricCategory): string {
  return categoryConfig[cat].color;
}

function categoryLabel(cat: MetricCategory): string {
  return categoryConfig[cat].label;
}

/** 综合评分公式说明 */
const compositeFormula = 'P = (A·a + F·f + S·s) / (a + f + s) × R';
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="指标定义"
      subtitle="12 项 KPI 指标定义（3+1+8 体系，对齐 GB/T 44693.2-2024）"
    />

    <!-- 顶部提示条 -->
    <Alert class="mt-3" type="info" show-icon>
      <template #message>
        <span>综合评分公式：{{ compositeFormula }}</span>
      </template>
      <template #description>
        <span class="text-xs">
          A/F/S 为核心质量指标（准确率/快速率/稳定率），a/f/s
          为对应权重（权重总和 100），R
          为有效自控率（折扣因子，非加权项）。权重配置请前往"权重配置"页，定级阈值请前往"定级阈值"页。
        </span>
      </template>
    </Alert>

    <Card class="mt-3">
      <div class="mb-4">
        <p class="text-sm" :style="{ color: themeColors.NEUTRAL }">
          按 3+1+8 分组展示：核心质量指标（CORE · 准确率 A / 快速率 F / 稳定率
          S）+ 投用指标（COMMISSIONING · 有效自控率 R）+
          辅助诊断指标（AUXILIARY_DIAGNOSTIC · 好值率/自控率/振荡率/饱和率等 8
          项）。
        </p>
      </div>

      <Table
        :columns="columns"
        :data-source="metricList"
        :pagination="false"
        :row-key="(record: MetricDefinition) => record.metricCode"
        :scroll="{ x: 900 }"
        size="middle"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'category'">
            <Tag :color="categoryColor((record as MetricDefinition).category)">
              {{ categoryLabel((record as MetricDefinition).category) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'formula'">
            <span
              class="font-mono text-xs"
              :style="{ color: themeColors.NEUTRAL }"
            >
              {{ (record as MetricDefinition).formula || '—' }}
            </span>
          </template>
          <template v-else-if="column.key === 'description'">
            <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
              {{ (record as MetricDefinition).description || '—' }}
            </span>
          </template>
        </template>
      </Table>
    </Card>
  </Page>
</template>
