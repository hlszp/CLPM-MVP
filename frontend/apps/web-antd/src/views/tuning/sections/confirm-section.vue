<script lang="ts" setup>
/**
 * 整定方案确认（Phase D §4.4.2 第④步）
 *
 * 安全边界：仅输出建议+证据+风险+回退+留痕，绝不直写 DCS。
 * - 汇总：辨识模型 + 推荐 PID + 仿真改善指标
 * - 风险评估：基于可信度等级与改善幅度
 * - 回退方案：原始 PID 参数记录
 * - 留痕：创建/更新整定任务记录，记录操作人与时间
 */
import type { TuningApi } from '#/api/tuning';

import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';

import { IconifyIcon } from '@vben/icons';

import {
  Alert,
  Button,
  Descriptions,
  DescriptionsItem,
  message,
  Spin,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { createTuningTaskApi } from '#/api/tuning';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useTuningStore } from '#/store/tuning';

defineOptions({ name: 'TuningConfirmSection' });

const { themeColors } = useClpmTheme();
const route = useRoute();
const store = useTuningStore();

const saving = ref(false);
const savedTaskId = ref('');

// ===== 数据汇总 =====

const identifySummary = computed(() => {
  const r = store.identifyResult;
  if (!r) return null;
  return {
    modelType: r.modelType ?? '—',
    K: r.params?.K ?? '—',
    tau: r.params?.tau ?? '—',
    theta: r.params?.theta ?? '—',
    fittingScore: r.fittingScore,
    confidenceLevel: r.confidenceLevel ?? '—',
  };
});

const recommendedPid = computed<null | TuningApi.PidParamsWithLabel>(() => {
  if (store.pidCandidates.length === 0) return null;
  return store.pidCandidates[0] ?? null;
});

const simulationImprovement = computed(() => {
  const sim = store.simulationResult;
  if (!sim) return null;
  return sim.improvement;
});

// ===== 风险评估 =====

const riskLevel = computed<{ color: string; desc: string; label: string }>(
  () => {
    const level = identifySummary.value?.confidenceLevel;
    const improvement = simulationImprovement.value;
    const hasImprovement =
      improvement &&
      Object.values(improvement).some((v) => v !== null && v > 0);
    if (level === 'E' || level === 'D') {
      return {
        label: '高',
        color: 'red',
        desc: '辨识可信度不足，建议补充数据重新辨识或采用阶跃实验兜底。',
      };
    }
    if (!hasImprovement) {
      return {
        label: '中',
        color: 'orange',
        desc: '仿真改善幅度有限，建议谨慎评估是否需要实施。',
      };
    }
    return {
      label: '低',
      color: 'green',
      desc: '辨识可信度良好且仿真改善明显，可按建议实施。',
    };
  },
);

// ===== 方案确认 =====

const canConfirm = computed(() => {
  return !!recommendedPid.value && !!store.identifyResult;
});

async function handleConfirm() {
  if (!store.identifyResult || !recommendedPid.value) {
    message.warning('缺少辨识结果或推荐 PID，无法确认方案');
    return;
  }
  saving.value = true;
  try {
    const r = store.identifyResult;
    const pid = recommendedPid.value;
    const modelParams: TuningApi.ModelParams = {
      K: r.params?.K ?? 0,
      tau: r.params?.tau ?? null,
      theta: r.params?.theta ?? null,
    };
    const algorithm =
      (route.query.algorithm as TuningApi.Algorithm | undefined) || 'IMC';

    const created = await createTuningTaskApi({
      loopId: store.currentLoopId,
      modelType: (r.modelType as TuningApi.ModelType) || 'FOPDT',
      modelParams,
      algorithm,
      recommendedPid: {
        kp: pid.kp,
        ti: pid.ti,
        td: pid.td,
      },
      fittingScore: r.fittingScore ?? null,
      simulationResult:
        (store.simulationResult as TuningApi.SimulationResult) ?? null,
      status: 'SIMULATED',
      confidenceLevel: r.confidenceLevel ?? null,
      identifyMethod: r.identifyMethod ?? null,
    });
    savedTaskId.value = created.id;
    message.success('整定方案已确认并留痕');
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

const confirmedAt = computed(() => {
  if (!savedTaskId.value) return null;
  return dayjs().format('YYYY-MM-DD HH:mm:ss');
});
</script>

<template>
  <div class="space-y-4 p-4">
    <ClpmPageToolbar
      title="方案确认"
      subtitle="确认整定建议，导出方案，查看风险与回退。"
    >
      <template #actions>
        <Button
          type="primary"
          :loading="saving"
          :disabled="!canConfirm"
          @click="handleConfirm"
        >
          <template #icon>
            <IconifyIcon icon="ant-design:check-outlined" />
          </template>
          确认方案并留痕
        </Button>
      </template>
    </ClpmPageToolbar>

    <!-- 安全边界提示 -->
    <Alert
      type="info"
      show-icon
      message="只读建议 · 人工实施 · 需留痕"
      description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
    />

    <Spin :spinning="saving">
      <!-- 方案汇总 -->
      <ClpmDataCanvas
        title="整定方案汇总"
        description="辨识模型、推荐 PID 参数与仿真改善指标一览。"
      >
        <Descriptions :column="2" bordered size="small">
          <DescriptionsItem label="回路">
            {{ store.currentLoopId || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="辨识模型">
            <span v-if="identifySummary">
              {{ identifySummary.modelType }}
              <Tag
                :color="
                  identifySummary.confidenceLevel === 'A' ||
                  identifySummary.confidenceLevel === 'B'
                    ? 'success'
                    : 'warning'
                "
                class="!m-0 ml-1"
              >
                {{ identifySummary.confidenceLevel }}
              </Tag>
            </span>
            <span v-else :style="{ color: themeColors.NEUTRAL }"
              >未完成辨识</span
            >
          </DescriptionsItem>
          <DescriptionsItem label="过程增益 K">
            <span
              v-if="identifySummary && identifySummary.K !== '—'"
              class="font-mono"
            >
              {{ Number(identifySummary.K).toFixed(4) }}
            </span>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="时间常数 τ">
            <span
              v-if="identifySummary && identifySummary.tau !== '—'"
              class="font-mono"
            >
              {{ Number(identifySummary.tau).toFixed(2) }}s
            </span>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="纯滞后 θ">
            <span
              v-if="identifySummary && identifySummary.theta !== '—'"
              class="font-mono"
            >
              {{ Number(identifySummary.theta).toFixed(2) }}s
            </span>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="拟合度">
            <span
              v-if="
                identifySummary?.fittingScore !== null &&
                identifySummary?.fittingScore !== undefined
              "
              class="font-mono"
            >
              {{ Number(identifySummary.fittingScore).toFixed(4) }}
            </span>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="推荐 PID" :span="2">
            <span v-if="recommendedPid" class="font-mono text-base font-medium">
              P={{ recommendedPid.kp }} &nbsp; I={{ recommendedPid.ti }} &nbsp;
              D={{ recommendedPid.td }}
            </span>
            <span v-else :style="{ color: themeColors.NEUTRAL }"
              >未生成候选 PID</span
            >
          </DescriptionsItem>
          <DescriptionsItem
            v-if="simulationImprovement"
            label="仿真改善"
            :span="2"
          >
            <div class="flex flex-wrap gap-2">
              <Tag
                v-for="(value, key) in simulationImprovement"
                :key="key"
                :color="value !== null && value > 0 ? 'success' : 'default'"
                class="!m-0"
              >
                {{ key }}:
                {{ value !== null ? `${(value * 100).toFixed(1)}%` : '—' }}
              </Tag>
            </div>
          </DescriptionsItem>
        </Descriptions>
      </ClpmDataCanvas>

      <!-- 风险评估 -->
      <ClpmDataCanvas
        title="风险评估"
        description="基于辨识可信度与仿真改善幅度推导。"
        class="mt-4"
      >
        <div class="flex items-start gap-3">
          <Tag :color="riskLevel.color" class="!m-0 text-sm font-medium">
            风险等级：{{ riskLevel.label }}
          </Tag>
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }">
            {{ riskLevel.desc }}
          </span>
        </div>
      </ClpmDataCanvas>

      <!-- 回退方案 -->
      <ClpmDataCanvas
        title="回退方案"
        description="实施前记录原始 PID 参数，如效果异常可快速回退。"
        class="mt-4"
      >
        <Alert
          type="warning"
          show-icon
          message="实施前请记录当前 DCS 中的 PID 参数，如整定后效果异常，可按原始参数回退。"
        />
        <div class="mt-3 text-sm" :style="{ color: themeColors.NEUTRAL }">
          回退操作由授权人员在 DCS 端执行，本平台仅提供原始参数记录与建议。
        </div>
      </ClpmDataCanvas>

      <!-- 留痕记录 -->
      <ClpmDataCanvas
        v-if="savedTaskId"
        title="留痕记录"
        description="方案确认已记录，可追溯。"
        class="mt-4"
      >
        <Descriptions :column="2" size="small">
          <DescriptionsItem label="任务 ID">
            {{ savedTaskId }}
          </DescriptionsItem>
          <DescriptionsItem label="确认时间">
            {{ confirmedAt }}
          </DescriptionsItem>
          <DescriptionsItem label="状态">
            <Tag color="success" class="!m-0">已确认（SIMULATED）</Tag>
          </DescriptionsItem>
          <DescriptionsItem label="下一步">
            由授权人员在 DCS 端实施参数变更
          </DescriptionsItem>
        </Descriptions>
      </ClpmDataCanvas>

      <!-- 未完成提示 -->
      <Alert
        v-if="!canConfirm"
        type="warning"
        show-icon
        class="mt-4"
        message="尚不具备确认条件"
        description="请先完成过程辨识与 PID 推荐，生成候选 PID 后再确认方案。"
      />
    </Spin>
  </div>
</template>
