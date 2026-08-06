<script lang="ts" setup>
/**
 * S7-TUNE-003 整定算法页
 *
 * 对齐 IDS v3.2 §2.5 + PRD §4.5
 * - 顶部：模型参数输入区（模型类型 + 动态模型参数 + 当前 PID 参数）
 * - 中部：算法选择区（算法下拉 + 动态算法参数 + 算法说明 + 整定按钮）
 * - 底部结果区：推荐 PID 参数（Descriptions）+ 操作按钮（仿真/保存任务）
 * - 支持从 URL query 读取：modelType, modelParams（JSON）, loopId
 */
import type { TuningApi } from '#/api/tuning';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  CollapsePanel,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  InputNumber,
  message,
  Modal,
  Select,
  Spin,
  Tag,
} from 'ant-design-vue';

import {
  createTuningTaskApi,
  getTuningMethodsApi,
  tunePidApi,
} from '#/api/tuning';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStateOverlay,
} from '#/components/clpm';
import { useClpmRoles } from '#/composables/use-clpm-roles';

defineOptions({ name: 'TuningAlgorithm' });

// Phase D: embedded 模式下不渲染 <Page> 外壳，由父级 detail.vue 提供
const props = defineProps<{ embedded?: boolean }>();

const route = useRoute();
const router = useRouter();
const { canEditAdvancedParams } = useClpmRoles();

const loading = ref(false);
const saving = ref(false);

/** P1-023：错误状态（整定失败时持久展示，带重试） */
const errorState = ref<{ detail: string; message: string } | null>(null);
const methods = ref<TuningApi.MethodInfo[]>([]);
const tuneResult = ref<null | TuningApi.TuneResult>(null);

/**
 * 模型来源门禁（P0-04）
 * 服务端 /tune、/simulate、/compare 已强制要求 modelSource + 可验证凭据；
 * 页面控制入口呈现，服务端仍按 sourceRecordId 复核记录。
 */
const modelSource = ref<TuningApi.ModelSource | undefined>(undefined);
const sourceRecordId = ref('');
const riskConfirmed = ref(false);

const sourceOptions: { label: string; value: TuningApi.ModelSource }[] = [
  { label: '历史辨识记录', value: 'IDENTIFICATION_RECORD' },
  { label: '阶跃实验', value: 'STEP_EXPERIMENT' },
  { label: '人工模型（需确认风险）', value: 'MANUAL' },
];

const modelUsageGate = computed<{ blocked: boolean; reason: string | null }>(
  () => {
    if (!modelSource.value) {
      return {
        blocked: true,
        reason: '必须明确模型来源并提供可验证凭据；旧版裸模型请求已停止放行。',
      };
    }
    if (modelSource.value === 'MANUAL') {
      if (!riskConfirmed.value) {
        return {
          blocked: true,
          reason: '人工模型必须显式确认模型与整定风险后方可执行。',
        };
      }
      return { blocked: false, reason: null };
    }
    if (!sourceRecordId.value) {
      return {
        blocked: true,
        reason: '该来源必须提供服务端可验证的 sourceRecordId。',
      };
    }
    return { blocked: false, reason: null };
  },
);

const canTune = computed(() => !modelUsageGate.value.blocked);

/** 模型类型选项 */
const modelTypeOptions: { label: string; value: TuningApi.ModelType }[] = [
  { label: 'FOPDT 一阶加纯滞后', value: 'FOPDT' },
  { label: 'SOPDT 二阶加纯滞后', value: 'SOPDT' },
  { label: 'IPDT 积分加纯滞后', value: 'IPDT' },
];

/** 算法显示名映射 */
const algorithmNameMap: Record<TuningApi.Algorithm, string> = {
  IMC: 'IMC 内模控制',
  LAMBDA: 'Lambda 整定',
  ZN: 'Ziegler-Nichols',
  COHEN_COON: 'Cohen-Coon',
  SIMC: 'SIMC 简化 IMC',
};

/** 表单状态 */
const form = reactive({
  modelType: 'FOPDT' as TuningApi.ModelType,
  // 模型参数
  K: undefined as number | undefined,
  tau: undefined as number | undefined,
  theta: undefined as number | undefined,
  T1: undefined as number | undefined,
  T2: undefined as number | undefined,
  // 当前 PID 参数（可选）
  kp: undefined as number | undefined,
  ti: undefined as number | undefined,
  td: undefined as number | undefined,
  // 算法
  algorithm: 'IMC' as TuningApi.Algorithm,
  // 算法参数（动态）
  algorithmParams: {} as Record<string, number>,
  // 回路 ID（从 model 页跳转来时携带）
  loopId: '' as string,
});

/** 当前选中的算法信息 */
const currentMethod = computed(() => {
  return methods.value.find((m) => m.code === form.algorithm) || null;
});

/** 算法下拉选项 */
const algorithmOptions = computed(() => {
  return methods.value.map((m) => ({
    label: m.name,
    value: m.code,
  }));
});

/** 构建模型参数对象 */
function buildModelParams(): TuningApi.ModelParams {
  const params: TuningApi.ModelParams = { K: form.K ?? null };
  switch (form.modelType) {
    case 'FOPDT': {
      params.tau = form.tau ?? null;
      params.theta = form.theta ?? null;
      break;
    }
    case 'IPDT': {
      params.theta = form.theta ?? null;
      break;
    }
    case 'SOPDT': {
      params.T1 = form.T1 ?? null;
      params.T2 = form.T2 ?? null;
      params.theta = form.theta ?? null;
      break;
    }
  }
  return params;
}

/** 构建当前 PID 参数（若有任一参数则返回） */
function buildCurrentPid(): TuningApi.PidParams | undefined {
  if (form.kp === undefined && form.ti === undefined && form.td === undefined) {
    return undefined;
  }
  return {
    kp: form.kp ?? 0,
    ti: form.ti ?? 0,
    td: form.td ?? 0,
  };
}

/** 加载整定方法信息 */
async function loadMethods() {
  try {
    const data = await getTuningMethodsApi();
    methods.value = data || [];
    // 初始化第一个算法的默认参数
    if (
      data.length > 0 &&
      !methods.value.some((m) => m.code === form.algorithm)
    ) {
      const first = data[0];
      if (first) {
        form.algorithm = first.code;
        initAlgorithmParams(first);
      }
    } else {
      const matched = methods.value.find((m) => m.code === form.algorithm);
      if (matched) {
        initAlgorithmParams(matched);
      }
    }
  } catch {
    // 错误已由拦截器处理
  }
}

/** 初始化算法参数为默认值 */
function initAlgorithmParams(method: TuningApi.MethodInfo) {
  const params: Record<string, number> = {};
  for (const p of method.params) {
    params[p.name] = Number(p.default);
  }
  form.algorithmParams = params;
}

/** 算法变更时重置算法参数 */
function handleAlgorithmChange(value: any) {
  const method = methods.value.find((m) => m.code === value);
  if (method) {
    initAlgorithmParams(method);
  }
}

/** 执行 PID 整定 */
async function handleTune() {
  // 模型来源门禁（P0-04）：服务端不再放行裸模型请求
  if (modelUsageGate.value.blocked) {
    message.warning(modelUsageGate.value.reason || '必须明确模型来源');
    return;
  }
  // 校验模型参数
  if (form.K === undefined || form.K === null) {
    message.warning('请输入过程增益 K');
    return;
  }
  if (
    form.modelType === 'FOPDT' &&
    (form.tau === undefined || form.tau === null)
  ) {
    message.warning('请输入时间常数 τ');
    return;
  }
  if (form.modelType === 'SOPDT') {
    if (form.T1 === undefined || form.T1 === null) {
      message.warning('请输入时间常数 T1');
      return;
    }
    if (form.T2 === undefined || form.T2 === null) {
      message.warning('请输入时间常数 T2');
      return;
    }
  }
  if (form.theta === undefined || form.theta === null) {
    message.warning('请输入纯滞后 θ');
    return;
  }

  loading.value = true;
  errorState.value = null;
  const hide = message.loading(
    `正在使用 ${form.algorithm} 算法进行 PID 整定…`,
    0,
  );
  try {
    const result = await tunePidApi({
      modelType: form.modelType,
      modelParams: buildModelParams(),
      algorithm: form.algorithm,
      algorithmParams: { ...form.algorithmParams },
      currentPid: buildCurrentPid(),
      loopId: form.loopId || undefined,
      modelSource: modelSource.value,
      riskConfirmed: riskConfirmed.value,
      // 人工模型不得绑定 sourceRecordId；记录型来源必须携带可验证凭据
      ...(modelSource.value &&
      modelSource.value !== 'MANUAL' &&
      sourceRecordId.value
        ? { sourceRecordId: sourceRecordId.value }
        : {}),
    });
    tuneResult.value = result;
    hide();
    message.success('PID 整定完成');
  } catch (err) {
    hide();
    errorState.value = {
      message: 'PID 整定失败',
      detail:
        err instanceof Error ? err.message : '请检查模型参数和算法配置后重试',
    };
  } finally {
    loading.value = false;
  }
}

/** 跳转闭环仿真页 */
function handleGoSimulation() {
  if (!tuneResult.value) return;
  if (modelUsageGate.value.blocked) {
    message.warning(modelUsageGate.value.reason || '必须明确模型来源');
    return;
  }
  // Phase D: embedded 模式下用 router.replace 更新 query，非 embedded 跳转
  const simQuery = {
    modelType: form.modelType,
    modelParams: JSON.stringify(buildModelParams()),
    currentPid: tuneResult.value.currentPid
      ? JSON.stringify(tuneResult.value.currentPid)
      : JSON.stringify(buildCurrentPid() || {}),
    recommendedPid: JSON.stringify(tuneResult.value.recommendedPid),
    // 模型来源契约贯穿到仿真页（P0-04）
    ...(modelSource.value ? { modelSource: modelSource.value } : {}),
    ...(modelSource.value &&
    modelSource.value !== 'MANUAL' &&
    sourceRecordId.value
      ? { sourceRecordId: sourceRecordId.value }
      : {}),
    ...(modelSource.value
      ? { riskConfirmed: riskConfirmed.value ? 'true' : 'false' }
      : {}),
  };
  if (props.embedded) {
    router.replace({
      query: { ...route.query, ...simQuery, algorithm: form.algorithm },
    });
  } else {
    router.push({
      path: '/tuning/detail',
      query: { ...simQuery, algorithm: form.algorithm },
    });
  }
}

/** 保存为整定任务 */
function handleSaveTask() {
  if (!tuneResult.value) return;
  if (!form.loopId) {
    message.warning('缺少回路 ID，无法保存整定任务（请从模型辨识页跳转）');
    return;
  }

  const result = tuneResult.value;

  Modal.confirm({
    title: '确认保存整定任务',
    content: `将使用算法「${algorithmNameMap[form.algorithm] || form.algorithm}」的推荐 PID 参数保存为整定任务，是否继续？`,
    okText: '确认保存',
    cancelText: '取消',
    onOk: async () => {
      saving.value = true;
      try {
        await createTuningTaskApi({
          loopId: form.loopId,
          modelType: form.modelType,
          modelParams: buildModelParams(),
          algorithm: form.algorithm,
          recommendedPid: result.recommendedPid,
          currentPid: buildCurrentPid(),
          status: 'SIMULATED',
        });
        message.success('整定任务保存成功');
      } catch {
        // 错误已由拦截器处理
      } finally {
        saving.value = false;
      }
    },
  });
}

/** 从 URL query 初始化参数 */
function initFromQuery() {
  const q = route.query;
  if (q.modelType) {
    form.modelType = q.modelType as TuningApi.ModelType;
  }
  if (q.modelParams) {
    try {
      const params = JSON.parse(
        q.modelParams as string,
      ) as TuningApi.ModelParams;
      if (params.K !== undefined && params.K !== null) {
        form.K = params.K;
      }
      if (params.tau !== undefined && params.tau !== null) {
        form.tau = params.tau;
      }
      if (params.theta !== undefined && params.theta !== null) {
        form.theta = params.theta;
      }
      if (params.T1 !== undefined && params.T1 !== null) {
        form.T1 = params.T1;
      }
      if (params.T2 !== undefined && params.T2 !== null) {
        form.T2 = params.T2;
      }
    } catch {
      // query 参数解析失败，忽略
    }
  }
  if (q.loopId) {
    form.loopId = q.loopId as string;
  }
  // 模型来源门禁（P0-04）：从 model 页/仿真页跳转时携带可验证凭据
  if (q.modelSource) {
    modelSource.value = q.modelSource as TuningApi.ModelSource;
  }
  if (q.sourceRecordId) {
    sourceRecordId.value = q.sourceRecordId as string;
  }
  if (q.riskConfirmed === 'true') {
    riskConfirmed.value = true;
  }
}

/** 模型类型变更时清空不相关的模型参数 */
watch(
  () => form.modelType,
  () => {
    // 不清空已输入的值，避免从 query 初始化后丢失
  },
);

onMounted(() => {
  initFromQuery();
  loadMethods();
});

// Phase D: embedded 模式下 route.query 变化时重新初始化（上步通过 router.replace 传递参数）
watch(
  () => route.query,
  () => {
    if (props.embedded) initFromQuery();
  },
  { deep: true },
);
</script>

<template>
  <component :is="embedded ? 'div' : Page">
    <ClpmPageToolbar
      title="整定算法"
      subtitle="基于辨识模型选择整定算法并生成推荐 PID 参数。"
    />
    <Alert
      type="warning"
      show-icon
      banner
      :closable="false"
      message="只读建议 · 人工实施 · 需留痕"
      description="本平台不直接修改 DCS 的 P/I/D 参数，参数由授权人员人工实施并留痕。"
      style="margin-bottom: 12px"
    />
    <Spin :spinning="loading">
      <ClpmDataCanvas class="mb-4 mt-4" title="模型参数">
        <Alert
          v-if="modelUsageGate.blocked"
          type="warning"
          show-icon
          banner
          :closable="false"
          :message="modelUsageGate.reason || '必须明确模型来源'"
          style="margin-bottom: 12px"
        />
        <Form layout="inline">
          <FormItem label="模型来源">
            <Select
              v-model:value="modelSource"
              style="width: 240px"
              :options="sourceOptions"
              placeholder="必须明确模型来源"
            />
          </FormItem>
          <FormItem v-if="modelSource === 'MANUAL'">
            <Checkbox v-model:checked="riskConfirmed">
              已确认人工模型与整定风险
            </Checkbox>
          </FormItem>
        </Form>
        <Form layout="inline">
          <FormItem label="模型类型">
            <Select
              v-model:value="form.modelType"
              style="width: 200px"
              :options="modelTypeOptions"
            />
          </FormItem>
        </Form>
        <Form class="mt-3" layout="inline">
          <FormItem label="过程增益 K">
            <InputNumber
              v-model:value="form.K"
              :step="0.01"
              placeholder="K"
              style="width: 140px"
            />
          </FormItem>
          <!-- FOPDT: tau / theta -->
          <template v-if="form.modelType === 'FOPDT'">
            <FormItem label="时间常数 τ (秒)">
              <InputNumber
                v-model:value="form.tau"
                :min="0"
                :step="0.1"
                placeholder="tau"
                style="width: 140px"
              />
            </FormItem>
          </template>
          <!-- SOPDT: T1 / T2 / theta -->
          <template v-if="form.modelType === 'SOPDT'">
            <FormItem label="时间常数 T1 (秒)">
              <InputNumber
                v-model:value="form.T1"
                :min="0"
                :step="0.1"
                placeholder="T1"
                style="width: 140px"
              />
            </FormItem>
            <FormItem label="时间常数 T2 (秒)">
              <InputNumber
                v-model:value="form.T2"
                :min="0"
                :step="0.1"
                placeholder="T2"
                style="width: 140px"
              />
            </FormItem>
          </template>
          <!-- 所有模型都有 theta -->
          <FormItem label="纯滞后 θ (秒)">
            <InputNumber
              v-model:value="form.theta"
              :min="0"
              :step="0.1"
              placeholder="theta"
              style="width: 140px"
            />
          </FormItem>
        </Form>
      </ClpmDataCanvas>

      <!-- 当前 PID 参数（可选） -->
      <ClpmDataCanvas title="当前 PID 参数（可选）" class="mb-4">
        <Form layout="inline">
          <FormItem label="比例增益 Kp">
            <InputNumber
              v-model:value="form.kp"
              :step="0.01"
              placeholder="Kp"
              style="width: 140px"
            />
          </FormItem>
          <FormItem label="积分时间 Ti (秒)">
            <InputNumber
              v-model:value="form.ti"
              :min="0"
              :step="0.1"
              placeholder="Ti"
              style="width: 140px"
            />
          </FormItem>
          <FormItem label="微分时间 Td (秒)">
            <InputNumber
              v-model:value="form.td"
              :min="0"
              :step="0.1"
              placeholder="Td"
              style="width: 140px"
            />
          </FormItem>
        </Form>
      </ClpmDataCanvas>

      <!-- 中部：算法选择区 -->
      <ClpmDataCanvas title="整定算法" class="mb-4">
        <Form layout="inline">
          <FormItem label="算法选择">
            <Select
              v-model:value="form.algorithm"
              style="width: 240px"
              :options="algorithmOptions"
              @change="handleAlgorithmChange"
            />
          </FormItem>
        </Form>

        <!-- 算法说明 -->
        <div v-if="currentMethod" class="mt-3 rounded bg-gray-50 p-3">
          <div class="mb-1 text-sm font-medium text-gray-700">
            {{ currentMethod.name }}
            <Tag color="blue" class="ml-2">
              适用模型: {{ currentMethod.applicableModel }}
            </Tag>
          </div>
          <div class="text-xs text-gray-500">
            {{ currentMethod.description }}
          </div>
        </div>

        <!-- P1-022：动态算法参数为高级参数，仅 ADMIN/EXPERT 可见 -->
        <Collapse
          v-if="
            canEditAdvancedParams &&
            currentMethod &&
            currentMethod.params.length > 0
          "
          :bordered="false"
          class="mt-3 advanced-params-collapse"
        >
          <CollapsePanel key="algo-advanced" header="高级参数">
            <Form layout="inline">
              <FormItem
                v-for="param in currentMethod.params"
                :key="param.name"
                :label="param.label"
              >
                <Select
                  v-if="param.options && param.options.length > 0"
                  v-model:value="form.algorithmParams[param.name]"
                  style="width: 140px"
                  :options="param.options.map((o) => ({ label: o, value: o }))"
                />
                <InputNumber
                  v-else
                  v-model:value="form.algorithmParams[param.name]"
                  :min="param.min"
                  :max="param.max"
                  :step="0.01"
                  :placeholder="param.label"
                  style="width: 140px"
                />
              </FormItem>
            </Form>
          </CollapsePanel>
        </Collapse>

        <div class="mt-4">
          <Button
            type="primary"
            size="large"
            :disabled="!canTune"
            :loading="loading"
            @click="handleTune"
          >
            执行整定
          </Button>
        </div>
      </ClpmDataCanvas>

      <!-- P1-023：错误状态覆盖（整定失败时持久展示，带重试） -->
      <ClpmDataCanvas v-if="errorState" title="整定结果" class="mb-4">
        <ClpmStateOverlay
          status="error"
          :error-message="errorState.message"
          :error-detail="errorState.detail"
          @retry="handleTune"
        />
      </ClpmDataCanvas>

      <!-- 底部结果区 -->
      <ClpmDataCanvas v-else-if="tuneResult" title="整定结果">
        <Descriptions :column="{ xs: 1, sm: 2, md: 3 }" bordered size="small">
          <DescriptionsItem label="算法">
            {{ algorithmNameMap[tuneResult.algorithm] || tuneResult.algorithm }}
          </DescriptionsItem>
          <DescriptionsItem label="算法版本">
            {{ tuneResult.algorithmVersion }}
          </DescriptionsItem>
          <DescriptionsItem label="推荐比例增益 Kp">
            <span class="font-mono font-bold text-blue-600">
              {{ Number(tuneResult.recommendedPid.kp).toFixed(4) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="推荐积分时间 Ti (秒)">
            <span class="font-mono font-bold text-blue-600">
              {{ Number(tuneResult.recommendedPid.ti).toFixed(4) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="推荐微分时间 Td (秒)">
            <span class="font-mono font-bold text-blue-600">
              {{ Number(tuneResult.recommendedPid.td).toFixed(4) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem
            v-if="tuneResult.currentPid"
            label="当前 Kp / Ti / Td"
          >
            <span class="font-mono text-gray-600">
              {{ Number(tuneResult.currentPid.kp).toFixed(4) }} /
              {{ Number(tuneResult.currentPid.ti).toFixed(4) }} /
              {{ Number(tuneResult.currentPid.td).toFixed(4) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem v-if="tuneResult.notes" label="备注" :span="3">
            {{ tuneResult.notes }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 操作按钮 -->
        <div class="mt-4 flex gap-2">
          <Button
            type="primary"
            size="large"
            :disabled="!canTune"
            @click="handleGoSimulation"
          >
            进行闭环仿真 →
          </Button>
          <Button size="large" :loading="saving" @click="handleSaveTask">
            保存为整定任务
          </Button>
        </div>
      </ClpmDataCanvas>

      <!-- P1-023：空状态覆盖 -->
      <ClpmDataCanvas v-else title="整定结果">
        <ClpmStateOverlay
          status="empty"
          empty-description="请输入模型参数并选择算法，点击「执行整定」计算推荐 PID 参数"
        />
      </ClpmDataCanvas>
    </Spin>
  </component>
</template>
