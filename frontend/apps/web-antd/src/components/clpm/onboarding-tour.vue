<script lang="ts" setup>
/**
 * P2-03：首次登录 Onboarding Tour
 *
 * 5 步引导 Tour，使用 Modal + Steps 实现：
 * ①工作台待办 ②回路配置流程 ③性能评估 ④异常处理闭环 ⑤整定安全边界
 *
 * - 首次登录自动触发（localStorage 标记 `clpm-onboarding-completed`）
 * - 可通过 ref.open() 手动触发重播
 * - 遵循 ZL 工业设计规范（Calm UI）
 */
import { computed, ref } from 'vue';

import { IconifyIcon } from '@vben/icons';

import { Button, Modal, Steps } from 'ant-design-vue';

defineOptions({ name: 'ClpmOnboardingTour' });

const STORAGE_KEY = 'clpm-onboarding-completed';

const visible = ref(false);
const current = ref(0);

interface TourStep {
  icon: string;
  title: string;
  description: string;
  highlights: string[];
}

const steps: TourStep[] = [
  {
    icon: 'lucide:layout-dashboard',
    title: '工作台待办',
    description:
      '工作台是每日工作的起点，展示待处理异常回路、数据链路健康状态和关键 KPI 概览。',
    highlights: [
      '待处理异常回路列表',
      '数据链路连通性状态',
      '关键 KPI 趋势概览',
    ],
  },
  {
    icon: 'lucide:git-branch',
    title: '回路配置流程',
    description:
      '在回路管理中创建回路并关联 7 个 OPC 测点（PV/SP/OP/MODE/PID_P/PID_I/PID_D），配置向导会引导你完成全流程。',
    highlights: [
      '4 步向导：基础信息 → Tag 关联 → 投用定义 → 启用评估',
      '7 槽位可视化图解',
      '配置后数据连通性验证',
    ],
  },
  {
    icon: 'lucide:bar-chart-3',
    title: '性能评估',
    description:
      '性能评估模块自动计算 12 个 KPI 指标（准确率/快速率/平稳率等），按控制类型降采样确保计算准确性。',
    highlights: ['8 大 KPI 指标看板', '历史趋势分析', '可信度 A/B/C/D/E 等级'],
  },
  {
    icon: 'lucide:activity',
    title: '异常处理闭环',
    description:
      '诊断中心自动检测异常并生成跟踪记录，从认领到实施到验证形成完整闭环管理。',
    highlights: [
      '8 类诊断标签自动识别',
      '处置时间线全链路追踪',
      'A/B 对比自动验证',
    ],
  },
  {
    icon: 'lucide:sliders-horizontal',
    title: '整定安全边界',
    description:
      '回路整定基于历史数据辨识过程对象（ARX/ARMAX/IV 算法栈），只输出建议参数和风险提示，不直接修改 DCS。',
    highlights: [
      '历史数据自动辨识 G(s)=PV/OP',
      '多 PID 参数响应对比仿真',
      '安全边界：参数由授权人员人工实施',
    ],
  },
];

const currentStep = computed(
  () =>
    steps[current.value] ?? {
      icon: '',
      title: '',
      description: '',
      highlights: [],
    },
);
const isLastStep = computed(() => current.value === steps.length - 1);

/** 打开 Tour（手动触发重播） */
function open() {
  current.value = 0;
  visible.value = true;
}

/** 首次登录自动触发 */
function triggerIfFirstTime() {
  try {
    const completed = localStorage.getItem(STORAGE_KEY);
    if (!completed) {
      current.value = 0;
      visible.value = true;
    }
  } catch {
    // localStorage 不可用时静默跳过
  }
}

function handleNext() {
  if (current.value < steps.length - 1) {
    current.value += 1;
  }
}

function handlePrev() {
  if (current.value > 0) {
    current.value -= 1;
  }
}

function handleFinish() {
  visible.value = false;
  try {
    localStorage.setItem(STORAGE_KEY, new Date().toISOString());
  } catch {
    // localStorage 不可用时静默跳过
  }
}

function handleSkip() {
  visible.value = false;
  try {
    localStorage.setItem(STORAGE_KEY, new Date().toISOString());
  } catch {
    // localStorage 不可用时静默跳过
  }
}

defineExpose({ open, triggerIfFirstTime });
</script>

<template>
  <Modal
    v-model:open="visible"
    :footer="null"
    :width="560"
    :mask-closable="false"
    centered
    title="CLPM 平台快速入门"
  >
    <div class="py-2">
      <!-- Steps 指示器 -->
      <Steps
        :current="current"
        :items="
          steps.map((s) => ({
            title: s.title,
          }))
        "
        size="small"
        class="mb-6"
      />

      <!-- 当前步骤内容 -->
      <div class="flex gap-4">
        <div
          class="flex h-16 w-16 shrink-0 items-center justify-center rounded-xl bg-blue-50"
        >
          <IconifyIcon
            :icon="currentStep.icon"
            :size="32"
            class="text-blue-500"
          />
        </div>
        <div class="flex-1">
          <h3 class="mb-2 text-lg font-semibold">{{ currentStep.title }}</h3>
          <p class="mb-3 text-sm leading-relaxed text-gray-600">
            {{ currentStep.description }}
          </p>
          <ul class="space-y-1">
            <li
              v-for="item in currentStep.highlights"
              :key="item"
              class="flex items-center gap-2 text-sm text-gray-500"
            >
              <IconifyIcon
                icon="lucide:check"
                :size="14"
                class="text-emerald-500"
              />
              {{ item }}
            </li>
          </ul>
        </div>
      </div>
    </div>

    <!-- 底部操作区 -->
    <div class="mt-4 flex items-center justify-between border-t pt-4">
      <Button type="link" size="small" @click="handleSkip"> 跳过引导 </Button>
      <div class="flex gap-2">
        <Button v-if="current > 0" @click="handlePrev"> 上一步 </Button>
        <Button v-if="!isLastStep" type="primary" @click="handleNext">
          下一步
        </Button>
        <Button v-else type="primary" @click="handleFinish"> 开始使用 </Button>
      </div>
    </div>
  </Modal>
</template>
