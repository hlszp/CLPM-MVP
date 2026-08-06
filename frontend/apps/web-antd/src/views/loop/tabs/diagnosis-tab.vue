<script lang="ts" setup>
/**
 * 回路工作台 · 诊断 Tab（IA 重构 Phase B·§4.1.1）
 *
 * 定位：单回路诊断摘要 —— 一眼看清"这个回路有什么病、有多确定"。
 * 遵循"摘要 + 1 主图 + 跳转入口"硬性规则，禁止内嵌完整诊断证据/可视化。
 *
 * 三区：
 * ① 跳转入口：查看诊断详情（带 loopId）/ 发起诊断任务
 * ② 摘要区：综合评分 + 融合置信度 + 可信度等级 + 风险等级 + 诊断时间 + 诊断标签
 * ③ 主图：问题定位路径 Steps（数据采集 → 特征提取 → 诊断标签）
 *
 * 数据来源：复用父级 workbench.vue provide 的 diagnosisDetail（概览 / 诊断 Tab 共用）
 * 后端零改动：全部组合现有 API。
 * 逻辑自 diagnosis/detail.vue 摘要区迁移精简而来。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { SummaryItem } from '#/components/clpm';

import { computed, inject, onMounted, ref, watch } from 'vue';
import type { Ref } from 'vue';
import { useRouter } from 'vue-router';

import {
  Button,
  Descriptions,
  DescriptionsItem,
  Empty,
  Spin,
  Steps,
  Tag,
} from 'ant-design-vue';

import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'LoopWorkbenchDiagnosisTab' });

const props = defineProps<{ loopId: string }>();

const router = useRouter();
const { themeColors } = useClpmTheme();

// ===== 诊断数据（由父级 workbench.vue 统一加载并 provide） =====
const diagnosisDetail = inject<Ref<DiagnosisApi.DiagnosisDetail | null>>(
  'diagnosisDetail',
  ref(null),
);
const diagnosisLoading = inject<Ref<boolean>>('diagnosisLoading', ref(false));
const loadDiagnosis = inject<(loopId: string) => Promise<void>>(
  'loadDiagnosis',
  async () => {},
);

// ===== 派生计算 =====

/** 诊断标签列表（概览只取标签+置信度，不展开完整证据） */
const diagnosisLabels = computed(
  () => diagnosisDetail.value?.diagnosisLabels ?? [],
);

/** 综合评分 → 风险等级 */
const riskLevel = computed<{
  label: string;
  status: SummaryItem['status'];
}>(() => {
  const score = diagnosisDetail.value?.compositeScore ?? 0;
  if (score < 60) return { label: 'HIGH', status: 'danger' };
  if (score < 80) return { label: 'MEDIUM', status: 'warning' };
  return { label: 'LOW', status: 'primary' };
});

/** 可信度等级（A/B/C/D/E） */
const confidenceLevel = computed(() => {
  const lv = diagnosisDetail.value?.confidenceLevel;
  if (lv === 'A' || lv === 'B' || lv === 'C' || lv === 'D' || lv === 'E') {
    return lv;
  }
  return '—';
});

const confidenceColor = computed(() => {
  const lv = confidenceLevel.value;
  if (lv === 'A' || lv === 'B') return 'green';
  if (lv === 'C') return 'orange';
  if (lv === '—') return 'default';
  return 'red';
});

/** 有效数据率文本（可信度等级旁边展示） */
const validRateText = computed(() => {
  const rate = diagnosisDetail.value?.validRate;
  if (rate === null || rate === undefined) return '';
  return `（有效数据 ${(rate * 100).toFixed(1)}%）`;
});

/** 问题定位路径 Steps（数据采集 → 特征提取 → 诊断标签） */
const problemPathSteps = computed(() => {
  if (!diagnosisDetail.value || diagnosisLabels.value.length === 0) {
    return [
      { title: '数据采集', description: '采集 PV/SP/OP 时序数据' },
      { title: '特征提取', description: 'FFT/散点拟合/质量码统计' },
      { title: '暂无诊断结论', description: '未检测到异常标签' },
    ];
  }
  const steps: { description: string; title: string }[] = [
    { title: '数据采集', description: '采集 PV/SP/OP 时序数据' },
    { title: '特征提取', description: 'FFT/散点拟合/质量码统计' },
  ];
  for (const item of diagnosisLabels.value) {
    steps.push({
      title:
        item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label] || item.label,
      description: `置信度 ${(item.confidence * 100).toFixed(1)}%`,
    });
  }
  return steps;
});

const currentStep = computed(() =>
  Math.max(0, problemPathSteps.value.length - 1),
);

/** 推理过程文本 */
const reasoning = computed(
  () => diagnosisDetail.value?.evidenceChain?.reasoning ?? '',
);

// ===== 跳转入口 =====
function goDiagnosisDetail() {
  router.push(`/diagnosis/detail/${props.loopId}`);
}

function goDiagnosisTasks() {
  router.push('/diagnosis/tasks');
}

// ===== 生命周期 =====
// 兜底：若父级未加载诊断数据（如直接激活诊断 Tab 时父级 watch 尚未触发），主动触发加载
onMounted(() => {
  if (props.loopId && !diagnosisDetail.value && !diagnosisLoading.value) {
    loadDiagnosis(props.loopId);
  }
});

// 工作台切换回路时，父级 watch 会重新加载；此处仅做兜底监听
watch(
  () => props.loopId,
  (newId) => {
    if (newId && !diagnosisDetail.value && !diagnosisLoading.value) {
      loadDiagnosis(newId);
    }
  },
);
</script>

<template>
  <div class="space-y-3 py-2">
    <!-- ① 跳转入口：快捷处置动作 -->
    <div class="flex items-center gap-2">
      <span class="text-xs text-gray-400">诊断处置：</span>
      <Button type="primary" size="small" @click="goDiagnosisDetail">
        查看诊断详情
      </Button>
      <Button size="small" @click="goDiagnosisTasks">发起诊断任务</Button>
    </div>

    <!-- ② 摘要区：综合评分 + 置信度 + 可信度 + 风险 + 诊断时间 + 诊断标签 -->
    <ClpmDataCanvas
      title="诊断摘要"
      :loading="diagnosisLoading"
      :empty="!diagnosisLoading && !diagnosisDetail"
      empty-text="暂无诊断数据"
      empty-reason="可能原因：该回路尚未触发诊断，或诊断任务尚未完成。"
      empty-action-text="发起诊断任务"
      @empty-action="goDiagnosisTasks"
    >
      <Spin :spinning="diagnosisLoading">
        <Descriptions
          v-if="diagnosisDetail"
          :column="{ xs: 1, sm: 2, md: 4 }"
          size="small"
          bordered
        >
          <DescriptionsItem label="综合评分">
            <span class="font-semibold text-blue-600">
              {{ Number(diagnosisDetail.compositeScore).toFixed(2) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="融合置信度">
            <span class="font-semibold">
              {{
                diagnosisDetail.fusedConfidence == null
                  ? '—'
                  : Number(diagnosisDetail.fusedConfidence).toFixed(2)
              }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="可信度">
            <Tag :color="confidenceColor">
              {{ confidenceLevel }}{{ validRateText }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="风险等级">
            <Tag
              :color="
                riskLevel.status === 'danger'
                  ? 'red'
                  : riskLevel.status === 'warning'
                    ? 'orange'
                    : 'blue'
              "
            >
              {{ riskLevel.label }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="诊断时间">
            {{ formatTime(diagnosisDetail.diagnosedAt) }}
          </DescriptionsItem>
          <DescriptionsItem label="算法版本">
            {{ diagnosisDetail.algorithmVersion || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="诊断标签" :span="2">
            <div v-if="diagnosisLabels.length > 0" class="flex flex-wrap gap-1">
              <Tag
                v-for="(item, idx) in diagnosisLabels"
                :key="idx"
                :color="DIAGNOSIS_LABEL_COLOR_MAP[item.label]"
              >
                {{ item.labelName || DIAGNOSIS_LABEL_NAME_MAP[item.label] }}
                <span class="ml-1 text-gray-400">
                  {{ Number(item.confidence).toFixed(2) }}
                </span>
              </Tag>
            </div>
            <span v-else class="text-xs text-gray-400">暂无诊断标签</span>
          </DescriptionsItem>
        </Descriptions>
      </Spin>
    </ClpmDataCanvas>

    <!-- ③ 主图：问题定位路径 Steps + 推理过程 -->
    <ClpmDataCanvas
      title="问题定位路径"
      description="诊断标签、置信度和推理证据按定位路径组织。"
      :loading="diagnosisLoading"
      :empty="!diagnosisLoading && !diagnosisDetail"
      empty-text="暂无诊断数据"
    >
      <div v-if="diagnosisDetail" class="space-y-4">
        <Steps
          :current="currentStep"
          :items="problemPathSteps"
          direction="vertical"
          size="small"
        />

        <!-- 推理过程 -->
        <div v-if="reasoning">
          <div class="mb-2 text-sm font-medium">推理过程</div>
          <div
            class="rounded border p-3 text-sm"
            :style="{ background: 'hsl(var(--muted) / 42%)' }"
          >
            {{ reasoning }}
          </div>
        </div>
        <div
          v-else
          class="py-4 text-center text-sm"
          :style="{ color: themeColors.NEUTRAL }"
        >
          暂无推理过程
        </div>

        <!-- 跳转完整诊断详情 -->
        <div class="flex justify-end">
          <Button type="link" size="small" @click="goDiagnosisDetail">
            查看完整诊断证据 →
          </Button>
        </div>
      </div>
      <Empty
        v-else-if="!diagnosisLoading"
        description="暂无诊断数据"
        class="py-8"
      />
    </ClpmDataCanvas>
  </div>
</template>
