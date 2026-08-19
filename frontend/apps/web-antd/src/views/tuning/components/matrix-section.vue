<script lang="ts" setup>
/**
 * 整定工作台 · 锚点② 整定矩阵（09 设计方案 §4.2/§6.2）
 *
 * 当前 PID 对照行（灰底固定首行）+ 6 算法行（5 算法 + 手动整定，P/I/D/特性备注/复选框）；
 * 行内算法参数微调（IMC/LAMBDA: λ ratio；SIMC: τc ratio）+ 单行重算；
 * 手动整定行 P/I/D 直接编辑（紫色 Tag），预填当前 PID；
 * 最多勾选 5 组进入仿真（+ 当前 PID 共 6 条曲线，超出禁选并提示）。
 */
import type { TuningWorkbenchContext } from '../composables/use-tuning-workbench';
import type { MatrixRow } from '../composables/use-tuning-workbench';

import { computed } from 'vue';

import {
  Alert,
  Button,
  Card,
  Checkbox,
  InputNumber,
  Spin,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { fmtNum2, tuningAlgoLabel } from '../constants';

const props = defineProps<{ ctx: TuningWorkbenchContext }>();
const { ctx } = props;

/** 算法特性备注（静态口径） */
const ALGO_NOTES: Record<string, string> = {
  IMC: '内模控制，λ 可调，偏平缓',
  LAMBDA: 'Lambda 整定，PI 结构，偏平缓',
  ZN: 'Z-N 1/4 衰减比，偏激进',
  COHEN_COON: 'Cohen-Coon，大滞后适用，偏激进',
  SIMC: 'SIMC 简化 IMC，PI 结构，稳健',
  MANUAL_TUNING: '手工设定 P/I/D，不经算法计算',
};

/** 有可调参数的算法 → 参数名 */
const ALGO_PARAM_LABEL: Record<string, string> = {
  IMC: 'λ/θ',
  LAMBDA: 'λ/τ',
  SIMC: 'τc/θ',
};

/** 矩阵可勾选上限（与 composable MAX_SIM_CANDIDATES 一致；+ 当前 PID = 6 条曲线） */
const MAX_CHECKED = 5;

const rows = computed(() => ctx.matrixRows.value);
const currentPid = computed(() => ctx.currentPid.value);
const checkedCount = computed(() => ctx.checkedRows.value.length);

function fmtPid(v: null | number | undefined): string {
  return fmtNum2(v);
}

/** 手动整定行判断 */
function isManualRow(record: Record<string, any>): boolean {
  return record.algorithm === 'MANUAL_TUNING';
}

function handleCheck(record: Record<string, any>) {
  const row = record as MatrixRow;
  if (!row.checked && checkedCount.value >= MAX_CHECKED) return; // 最多 5 组
  ctx.toggleRow(row);
}

const columns = [
  { key: 'select', title: '仿真', width: 56 },
  { key: 'algorithm', title: '算法', width: 180 },
  { key: 'kp', title: 'P（比例增益）', width: 110 },
  { key: 'ti', title: 'I（积分时间 s）', width: 120 },
  { key: 'td', title: 'D（微分时间 s）', width: 120 },
  { key: 'note', title: '特性备注' },
  { key: 'tune', title: '参数微调', width: 210 },
];
</script>

<template>
  <Card id="tuning-anchor-matrix" size="small" class="tuning-section">
    <template #title>
      <span class="section-title">② 整定矩阵（全算法对比）</span>
      <span class="ml-2 text-xs font-normal text-neutral-400"
        >勾选 1~5 组进入仿真（+ 当前 PID 共 6 条曲线）</span
      >
    </template>

    <Alert
      v-if="ctx.matrixError.value"
      class="mb-2"
      type="error"
      :message="ctx.matrixError.value"
      show-icon
    />
    <Alert
      v-if="ctx.currentPidMissing.value"
      class="mb-2"
      type="warning"
      message="当前回路未配置 PID 参数测点（PID_P/PID_I），矩阵无对照基准且方案确认不可用"
      show-icon
    />

    <Spin :spinning="ctx.matrixLoading.value">
      <Table
        :columns="columns"
        :data-source="rows"
        :pagination="false"
        size="small"
        row-key="algorithm"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'select'">
            <Checkbox
              :checked="record.checked"
              :disabled="
                !record.ok ||
                !record.pid ||
                (!record.checked && checkedCount >= MAX_CHECKED)
              "
              @change="handleCheck(record)"
            />
          </template>
          <template v-else-if="column.key === 'algorithm'">
            <Tag
              :color="
                record.ok
                  ? isManualRow(record)
                    ? 'purple'
                    : 'processing'
                  : 'default'
              "
            >
              {{ tuningAlgoLabel(record.algorithm) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'kp'">
            <InputNumber
              v-if="isManualRow(record)"
              v-model:value="(record.pid as any).kp"
              size="small"
              :precision="2"
              :min="0"
              :step="0.1"
              style="width: 88px"
            />
            <span v-else class="clpm-num">{{
              record.ok ? fmtPid(record.pid?.kp) : '—'
            }}</span>
          </template>
          <template v-else-if="column.key === 'ti'">
            <InputNumber
              v-if="isManualRow(record)"
              v-model:value="(record.pid as any).ti"
              size="small"
              :precision="2"
              :min="0"
              :step="0.5"
              style="width: 88px"
            />
            <span v-else class="clpm-num">{{
              record.ok ? fmtPid(record.pid?.ti) : '—'
            }}</span>
          </template>
          <template v-else-if="column.key === 'td'">
            <InputNumber
              v-if="isManualRow(record)"
              v-model:value="(record.pid as any).td"
              size="small"
              :precision="2"
              :min="0"
              :step="0.5"
              style="width: 88px"
            />
            <span v-else class="clpm-num">{{
              record.ok ? fmtPid(record.pid?.td) : '—'
            }}</span>
          </template>
          <template v-else-if="column.key === 'note'">
            <Tooltip v-if="!record.ok" :title="record.error">
              <span class="text-red-500">计算失败</span>
            </Tooltip>
            <span v-else class="text-xs text-neutral-500">{{
              ALGO_NOTES[record.algorithm]
            }}</span>
          </template>
          <template v-else-if="column.key === 'tune'">
            <div
              v-if="ALGO_PARAM_LABEL[record.algorithm]"
              class="flex items-center gap-1"
            >
              <span class="text-xs text-neutral-400">{{
                ALGO_PARAM_LABEL[record.algorithm]
              }}</span>
              <InputNumber
                v-model:value="record.paramValue"
                size="small"
                :min="0.1"
                :max="10"
                :step="0.1"
                style="width: 76px"
              />
              <Button
                size="small"
                :loading="(record as MatrixRow).recomputing"
                @click="ctx.recomputeRow(record as MatrixRow)"
              >
                重算
              </Button>
            </div>
            <span
              v-else-if="isManualRow(record)"
              class="text-xs text-neutral-400"
            >
              手工设定（左侧直接编辑）
            </span>
            <span v-else class="text-xs text-neutral-300">无</span>
          </template>
        </template>
      </Table>

      <!-- 当前 PID 对照行（固定灰底） -->
      <div class="current-pid-row">
        <span class="text-xs font-medium text-neutral-500"
          >当前 DCS PID 对照</span
        >
        <span class="clpm-num"
          >P {{ currentPid ? fmtPid(currentPid.kp) : '—' }}</span
        >
        <span class="clpm-num"
          >I {{ currentPid ? fmtPid(currentPid.ti) : '—' }}</span
        >
        <span class="clpm-num"
          >D {{ currentPid ? fmtPid(currentPid.td) : '—' }}</span
        >
      </div>
    </Spin>
  </Card>
</template>

<style scoped>
.section-title {
  font-size: 13px;
  font-weight: 600;
}

.current-pid-row {
  display: flex;
  gap: 24px;
  align-items: center;
  padding: 6px 12px;
  margin-top: 8px;
  background: rgb(0 0 0 / 4%);
  border-radius: 4px;
}
</style>
