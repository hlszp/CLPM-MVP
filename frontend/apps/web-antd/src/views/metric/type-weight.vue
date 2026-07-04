<script lang="ts" setup>
/**
 * 控制类型权重模板配置（P5-T5 重构）
 *
 * 对齐 UI/UX v5.3 §6.1.4 + FDS v5.1 §5.2.2
 * - 4 种控制类型（STABLE/SLOW/FAST/LOGIC）的 6 指标权重模板
 * - 仅 3 项核心指标（accuracyRate + fastRate + steadyRate）参与权重和校验（须=100）
 * - 国标默认值对比展示
 * - 实时归一校验：accuracy + fast + steady = 100
 * - "保存为新版本"按钮 + 二次确认弹窗（需填写变更说明 remark）
 * - R 折扣因子说明区（只读）
 * - 适用场景说明（只读文本）
 */
import type { ControlType, MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import {
  Button,
  Input,
  InputNumber,
  message,
  Modal,
  RadioGroup,
  Tag,
} from 'ant-design-vue';

import { ClpmToolbarButton } from '#/components/clpm';
import {
  getWeightTemplatesApi,
  saveWeightTemplatesApi,
} from '#/api/metric';

defineOptions({ name: 'MetricTypeWeightContent' });

const loading = ref(false);
const saving = ref(false);
const schema = ref<MetricApi.WeightTemplateSchema | null>(null);
const activeControlType = ref<ControlType>('STABLE');

/** 控制类型元数据 */
const CONTROL_TYPE_MAP: Record<
  ControlType,
  { color: string; desc: string; label: string; scene: string }
> = {
  STABLE: {
    label: '稳定型',
    color: 'blue',
    desc: '温度、液位等慢过程回路',
    scene: '适用于温度、液位等响应较慢、对稳定性要求高的回路。',
  },
  SLOW: {
    label: '慢速型',
    color: 'cyan',
    desc: '缓慢响应的回路',
    scene: '适用于缓慢响应的流量回路，对准确度要求较高。',
  },
  FAST: {
    label: '快速型',
    color: 'orange',
    desc: '流量、压力等快过程回路',
    scene: '适用于流量、压力等响应迅速的回路，侧重快速跟踪能力。',
  },
  LOGIC: {
    label: '逻辑型',
    color: 'purple',
    desc: '开关/逻辑控制回路',
    scene: '适用于开关量、逻辑控制回路，无准确度指标（accuracy=0）。',
  },
};

/** 国标默认权重模板（FDS v5.1 §5.2.2） */
const DEFAULT_WEIGHTS: Record<
  ControlType,
  { accuracyRate: number; fastRate: number; steadyRate: number }
> = {
  STABLE: { steadyRate: 50, accuracyRate: 20, fastRate: 30 },
  SLOW: { steadyRate: 60, accuracyRate: 30, fastRate: 10 },
  FAST: { steadyRate: 30, accuracyRate: 20, fastRate: 50 },
  LOGIC: { steadyRate: 60, accuracyRate: 0, fastRate: 40 },
};

/** 编辑态：以 controlType 为 key 存储 6 指标权重 */
const editState = reactive<
  Record<
    ControlType,
    {
      accuracyRate: number;
      autoModeRate: number;
      fastRate: number;
      oscillationRate: number;
      saturationRate: number;
      steadyRate: number;
    }
  >
>({
  STABLE: {
    autoModeRate: 0,
    steadyRate: 50,
    accuracyRate: 20,
    fastRate: 30,
    oscillationRate: 0,
    saturationRate: 0,
  },
  SLOW: {
    autoModeRate: 0,
    steadyRate: 60,
    accuracyRate: 30,
    fastRate: 10,
    oscillationRate: 0,
    saturationRate: 0,
  },
  FAST: {
    autoModeRate: 0,
    steadyRate: 30,
    accuracyRate: 20,
    fastRate: 50,
    oscillationRate: 0,
    saturationRate: 0,
  },
  LOGIC: {
    autoModeRate: 0,
    steadyRate: 60,
    accuracyRate: 0,
    fastRate: 40,
    oscillationRate: 0,
    saturationRate: 0,
  },
});

/** 当前激活类型的编辑态（用于模板绑定） */
const currentEdit = computed(() => editState[activeControlType.value]);

/** 3 项核心指标权重和 */
const coreWeightTotal = computed(() => {
  const s = currentEdit.value;
  return s.steadyRate + s.accuracyRate + s.fastRate;
});

const coreWeightValid = computed(() => coreWeightTotal.value === 100);

/** 加载权重模板 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getWeightTemplatesApi();
    schema.value = data;
    // 同步编辑态
    for (const item of data.templates ?? []) {
      editState[item.controlType] = {
        autoModeRate: item.autoModeRate ?? 0,
        steadyRate: item.steadyRate ?? 0,
        accuracyRate: item.accuracyRate ?? 0,
        fastRate: item.fastRate ?? 0,
        oscillationRate: item.oscillationRate ?? 0,
        saturationRate: item.saturationRate ?? 0,
      };
    }
    // 补全 4 种类型（后端可能未返回全部）— 使用国标默认值
    const types: ControlType[] = ['STABLE', 'SLOW', 'FAST', 'LOGIC'];
    for (const t of types) {
      if (!data.templates?.some((it) => it.controlType === t)) {
        const d = DEFAULT_WEIGHTS[t];
        editState[t] = {
          autoModeRate: 0,
          steadyRate: d.steadyRate,
          accuracyRate: d.accuracyRate,
          fastRate: d.fastRate,
          oscillationRate: 0,
          saturationRate: 0,
        };
      }
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 保存确认弹窗 */
const confirmVisible = ref(false);
const confirmLoading = ref(false);
const changeRemark = ref('');

/** 全部 4 类是否均通过校验 */
const allValid = computed(() => {
  const types: ControlType[] = ['STABLE', 'SLOW', 'FAST', 'LOGIC'];
  return types.every((t) => {
    const s = editState[t];
    return s.steadyRate + s.accuracyRate + s.fastRate === 100;
  });
});

function handleSave() {
  if (!allValid.value) {
    message.warning(
      '存在控制类型核心指标权重和不为 100，请检查 STABLE/SLOW/FAST/LOGIC 各项',
    );
    return;
  }
  if (!changeRemark.value.trim()) {
    message.warning('请填写变更说明');
    return;
  }
  confirmVisible.value = true;
}

async function confirmSave() {
  confirmLoading.value = true;
  saving.value = true;
  try {
    const templates: MetricApi.WeightTemplateItem[] = (
      ['STABLE', 'SLOW', 'FAST', 'LOGIC'] as ControlType[]
    ).map((t) => ({
      controlType: t,
      autoModeRate: editState[t].autoModeRate,
      steadyRate: editState[t].steadyRate,
      accuracyRate: editState[t].accuracyRate,
      fastRate: editState[t].fastRate,
      oscillationRate: editState[t].oscillationRate,
      saturationRate: editState[t].saturationRate,
    }));
    await saveWeightTemplatesApi({
      templates,
      remark: changeRemark.value.trim(),
    });
    message.success('权重模板保存成功（已生成新版本）');
    confirmVisible.value = false;
    changeRemark.value = '';
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    confirmLoading.value = false;
    saving.value = false;
  }
}

/** 重置当前类型为国标默认值 */
function resetCurrentToDefault() {
  const d = DEFAULT_WEIGHTS[activeControlType.value];
  editState[activeControlType.value] = {
    autoModeRate: 0,
    steadyRate: d.steadyRate,
    accuracyRate: d.accuracyRate,
    fastRate: d.fastRate,
    oscillationRate: 0,
    saturationRate: 0,
  };
  message.info(`已重置 ${CONTROL_TYPE_MAP[activeControlType.value].label} 为国标默认值`);
}

onMounted(() => {
  loadList();
});
</script>

<template>
  <div class="metric-type-weight-content">
    <div class="mb-3 flex items-center justify-between">
      <p class="text-sm text-gray-500">
        配置 4 种控制类型（STABLE/SLOW/FAST/LOGIC）的 6 指标权重模板。
        仅 3 项核心指标（稳定率 + 准确度 + 快速率）参与权重和校验，须=100。
      </p>
      <div class="flex gap-2">
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadList"
        />
        <Button
          v-permission="['ADMIN']"
          :disabled="!allValid"
          @click="resetCurrentToDefault"
        >
          重置当前为默认
        </Button>
        <Button
          v-permission="['ADMIN']"
          type="primary"
          :loading="saving"
          :disabled="!allValid"
          @click="handleSave"
        >
          保存为新版本
        </Button>
      </div>
    </div>

    <!-- 控制类型切换器 -->
    <div class="mb-4 flex items-center gap-3">
      <RadioGroup
        v-model:value="activeControlType"
        :options="
          (['STABLE', 'SLOW', 'FAST', 'LOGIC'] as ControlType[]).map((t) => ({
            label: `${CONTROL_TYPE_MAP[t].label}（${t}）`,
            value: t,
          }))
        "
        option-type="button"
        button-style="solid"
      />
      <Tag :color="CONTROL_TYPE_MAP[activeControlType].color">
        {{ CONTROL_TYPE_MAP[activeControlType].desc }}
      </Tag>
    </div>

    <!-- 权重编辑表单 -->
    <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
      <!-- 3 项核心指标 -->
      <div class="rounded border border-blue-200 bg-blue-50/30 p-4">
        <div class="mb-3 font-medium text-blue-700">
          核心指标（参与权重和校验，须=100）
        </div>
        <div class="space-y-3">
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-sm">稳定率 (steadyRate)</span>
              <span class="text-xs text-gray-400">
                国标默认: {{ DEFAULT_WEIGHTS[activeControlType].steadyRate }}
              </span>
            </div>
            <InputNumber
              v-model:value="currentEdit.steadyRate"
              :min="0"
              :max="100"
              addon-after="%"
              style="width: 100%"
            />
          </div>
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-sm">准确度 (accuracyRate)</span>
              <span class="text-xs text-gray-400">
                国标默认: {{ DEFAULT_WEIGHTS[activeControlType].accuracyRate }}
              </span>
            </div>
            <InputNumber
              v-model:value="currentEdit.accuracyRate"
              :min="0"
              :max="100"
              addon-after="%"
              style="width: 100%"
            />
          </div>
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-sm">快速率 (fastRate)</span>
              <span class="text-xs text-gray-400">
                国标默认: {{ DEFAULT_WEIGHTS[activeControlType].fastRate }}
              </span>
            </div>
            <InputNumber
              v-model:value="currentEdit.fastRate"
              :min="0"
              :max="100"
              addon-after="%"
              style="width: 100%"
            />
          </div>
        </div>
        <div
          class="mt-3 rounded p-2 text-center text-sm"
          :class="
            coreWeightValid
              ? 'bg-green-100 text-green-700'
              : 'bg-red-100 text-red-700'
          "
        >
          权重总和: {{ coreWeightTotal }}
          <span v-if="coreWeightValid"> ✓</span>
          <span v-else> ✗ 须为 100</span>
        </div>
      </div>

      <!-- 3 项非核心指标（只读，固定为 0） -->
      <div class="rounded border border-gray-200 bg-gray-50 p-4">
        <div class="mb-3 font-medium text-gray-600">
          非核心指标（固定为 0，不参与综合评分权重）
        </div>
        <div class="space-y-3">
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-sm text-gray-500">
                自动模式率 (autoModeRate)
              </span>
              <span class="text-xs text-gray-400">固定: 0</span>
            </div>
            <InputNumber
              v-model:value="currentEdit.autoModeRate"
              :min="0"
              :max="100"
              addon-after="%"
              disabled
              style="width: 100%"
            />
          </div>
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-sm text-gray-500">
                振荡率 (oscillationRate)
              </span>
              <span class="text-xs text-gray-400">固定: 0</span>
            </div>
            <InputNumber
              v-model:value="currentEdit.oscillationRate"
              :min="0"
              :max="100"
              addon-after="%"
              disabled
              style="width: 100%"
            />
          </div>
          <div>
            <div class="mb-1 flex items-center justify-between">
              <span class="text-sm text-gray-500">
                饱和率 (saturationRate)
              </span>
              <span class="text-xs text-gray-400">固定: 0</span>
            </div>
            <InputNumber
              v-model:value="currentEdit.saturationRate"
              :min="0"
              :max="100"
              addon-after="%"
              disabled
              style="width: 100%"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- R 折扣因子说明 -->
    <div class="mt-4 rounded border border-amber-200 bg-amber-50 p-4">
      <div class="mb-2 font-medium text-amber-800">R 折扣因子说明</div>
      <div class="text-sm text-amber-700">
        <p>
          有效自控率 R（effectiveAutoRate）作为乘法折扣因子，
          不参与上述权重和校验，独立作用于综合评分。
        </p>
        <p class="mt-2 font-mono bg-white/60 inline-block px-2 py-1 rounded">
          P_loop = (A·a + F·f + S·s) / (a + f + s) × (R / 100)
        </p>
        <p class="mt-2">
          其中 A/F/S 为指标值，a/f/s 为对应权重（即上方 3 项核心指标权重）。
          R 取值 0~100，反映自动模式有效时长占比。
        </p>
      </div>
    </div>

    <!-- 适用场景说明 -->
    <div class="mt-4 rounded border border-gray-200 bg-gray-50 p-4">
      <div class="mb-2 font-medium text-gray-700">适用场景</div>
      <p class="text-sm text-gray-600">
        {{ CONTROL_TYPE_MAP[activeControlType].scene }}
      </p>
      <div class="mt-2 text-xs text-gray-500">
        <strong>国标默认值：</strong>
        稳定率={{ DEFAULT_WEIGHTS[activeControlType].steadyRate }}%，
        准确度={{ DEFAULT_WEIGHTS[activeControlType].accuracyRate }}%，
        快速率={{ DEFAULT_WEIGHTS[activeControlType].fastRate }}%
      </div>
    </div>

    <!-- 保存确认弹窗 -->
    <Modal
      v-model:open="confirmVisible"
      title="确认保存权重模板（新版本）"
      :confirm-loading="confirmLoading"
      ok-text="确认保存"
      cancel-text="取消"
      width="560px"
      @ok="confirmSave"
    >
      <div class="space-y-3 py-2">
        <div class="text-sm">
          <div class="mb-2 font-medium">变更摘要（4 类控制类型）</div>
          <div class="rounded border border-gray-200 bg-gray-50 p-3">
            <div
              v-for="t in (['STABLE', 'SLOW', 'FAST', 'LOGIC'] as ControlType[])"
              :key="t"
              class="mb-1 flex justify-between text-xs"
            >
              <span class="text-gray-600">
                {{ CONTROL_TYPE_MAP[t].label }}（{{ t }}）
              </span>
              <span class="font-mono">
                S={{ editState[t].steadyRate }} /
                A={{ editState[t].accuracyRate }} /
                F={{ editState[t].fastRate }}
                <span
                  :class="
                    editState[t].steadyRate +
                      editState[t].accuracyRate +
                      editState[t].fastRate ===
                    100
                      ? 'text-green-600'
                      : 'text-red-600'
                  "
                >
                  ({{
                    editState[t].steadyRate +
                      editState[t].accuracyRate +
                      editState[t].fastRate
                  }})
                </span>
              </span>
            </div>
          </div>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">影响范围</div>
          <p class="rounded bg-orange-50 p-2 text-xs text-orange-700">
            保存后将以新版本生效，所有回路的综合性能评分将在下次评估时使用新权重。
            可在「版本历史」Tab 查看历史版本并回滚。
          </p>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">变更说明（必填）</div>
          <Input.TextArea
            v-model:value="changeRemark"
            placeholder="请简要说明本次变更原因，便于追溯"
            :rows="2"
          />
        </div>
      </div>
    </Modal>
  </div>
</template>
