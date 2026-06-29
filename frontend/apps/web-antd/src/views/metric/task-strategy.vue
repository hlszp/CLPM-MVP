<script lang="ts" setup>
/**
 * 任务策略配置（B2.5 新增）
 *
 * 对齐 UI/UX 改造方案 §6.1.4 + PRD §4.3
 * - 标准评估任务策略：计算周期 / 数据窗口 / 默认时间窗 / 是否启用整点触发
 * - 自动触发策略：低效回路自动重评阈值 / 新回路自动首评开关
 * - 重试策略：失败重试次数 / 重试间隔
 * - 调度策略：并发数 / 优先级（级别 1 优先）/ 排队超时
 * - 仅 ADMIN 可编辑
 *
 * 注：本页为前端占位 + 表单结构，后端"配置变更预览/任务策略"接口属 P1 待补。
 */
import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Card,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Switch,
  Tag,
} from 'ant-design-vue';

import { ClpmPageToolbar, ClpmToolbarButton } from '#/components/clpm';
import ConfigTabs from '#/components/metric/config-tabs.vue';

defineOptions({ name: 'MetricTaskStrategy' });

const loading = ref(false);
const saving = ref(false);

const formRef = ref();

const formState = reactive({
  // 标准评估任务
  calcPeriod: '1h',
  dataFetchWindow: '1h',
  defaultTimeWindow: 'today',
  hourlyTrigger: true,
  // 自动触发
  autoRerevaluateThreshold: 60,
  autoFirstEvaluation: true,
  // 重试策略
  retryMaxAttempts: 3,
  retryInterval: 30,
  // 调度策略
  scheduleConcurrency: 10,
  priorityByLevel: true,
  queueTimeout: 300,
});

const calcPeriodOptions = [
  { label: '5 分钟', value: '5m' },
  { label: '15 分钟', value: '15m' },
  { label: '30 分钟', value: '30m' },
  { label: '1 小时', value: '1h' },
  { label: '6 小时', value: '6h' },
  { label: '1 天', value: '1d' },
];

const dataFetchWindowOptions = [
  { label: '15 分钟', value: '15m' },
  { label: '30 分钟', value: '30m' },
  { label: '1 小时', value: '1h' },
  { label: '6 小时', value: '6h' },
  { label: '1 天', value: '1d' },
];

const defaultTimeWindowOptions = [
  { label: '今天', value: 'today' },
  { label: '昨天', value: 'yesterday' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

/** 变更确认弹窗 */
const confirmVisible = ref(false);
const changeRemark = ref('');

/** 模拟加载（P1 接口待补） */
async function loadStrategy() {
  loading.value = true;
  try {
    // TODO: 调用 GET /api/v1/config/task-strategy（P1 接口）
    await new Promise((resolve) => setTimeout(resolve, 200));
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSave() {
  formRef.value?.validate().then(() => {
    confirmVisible.value = true;
    changeRemark.value = '';
  });
}

async function confirmSave() {
  saving.value = true;
  try {
    // TODO: 调用 PUT /api/v1/config/task-strategy（P1 接口）
    await new Promise((resolve) => setTimeout(resolve, 300));
    message.success('任务策略已保存（占位提示，P1 接口接入后生效）');
    confirmVisible.value = false;
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  loadStrategy();
});
</script>

<template>
  <Page>
    <ConfigTabs />
    <ClpmPageToolbar
      title="任务策略"
      subtitle="管理标准评估任务、自动触发、重试与调度策略"
    />
    <div class="mt-4 space-y-4">
      <!-- 标准评估任务 -->
      <Card title="标准评估任务" :loading="loading">
        <Form ref="formRef" :model="formState" layout="vertical" class="pt-2">
          <div class="grid grid-cols-2 gap-4">
            <FormItem label="计算周期" name="calcPeriod">
              <Select
                v-model:value="formState.calcPeriod"
                :options="calcPeriodOptions"
              />
            </FormItem>
            <FormItem label="数据拉取窗口" name="dataFetchWindow">
              <Select
                v-model:value="formState.dataFetchWindow"
                :options="dataFetchWindowOptions"
              />
            </FormItem>
            <FormItem label="默认时间窗" name="defaultTimeWindow">
              <Select
                v-model:value="formState.defaultTimeWindow"
                :options="defaultTimeWindowOptions"
              />
            </FormItem>
            <FormItem label="整点自动触发" name="hourlyTrigger">
              <Switch v-model:checked="formState.hourlyTrigger" />
              <span class="ml-2 text-xs text-gray-500">
                开启后每整点自动触发标准评估任务
              </span>
            </FormItem>
          </div>
        </Form>
      </Card>

      <!-- 自动触发策略 -->
      <Card title="自动触发策略">
        <Form :model="formState" layout="vertical">
          <div class="grid grid-cols-2 gap-4">
            <FormItem
              label="低效回路自动重评阈值（综合评分）"
              name="autoRerevaluateThreshold"
            >
              <InputNumber
                v-model:value="formState.autoRerevaluateThreshold"
                :min="0"
                :max="100"
                class="w-full"
                addon-after="分"
              />
              <span class="mt-1 block text-xs text-gray-500">
                综合评分低于此阈值的回路将自动触发重评
              </span>
            </FormItem>
            <FormItem label="新回路自动首评" name="autoFirstEvaluation">
              <Switch v-model:checked="formState.autoFirstEvaluation" />
              <span class="ml-2 text-xs text-gray-500">
                新建回路后自动触发首次评估
              </span>
            </FormItem>
          </div>
        </Form>
      </Card>

      <!-- 重试策略 -->
      <Card title="重试策略">
        <Form :model="formState" layout="vertical">
          <div class="grid grid-cols-2 gap-4">
            <FormItem label="失败重试次数" name="retryMaxAttempts">
              <InputNumber
                v-model:value="formState.retryMaxAttempts"
                :min="0"
                :max="10"
                class="w-full"
              />
            </FormItem>
            <FormItem label="重试间隔（秒）" name="retryInterval">
              <InputNumber
                v-model:value="formState.retryInterval"
                :min="5"
                :max="3600"
                class="w-full"
              />
            </FormItem>
          </div>
        </Form>
      </Card>

      <!-- 调度策略 -->
      <Card title="调度策略">
        <Form :model="formState" layout="vertical">
          <div class="grid grid-cols-2 gap-4">
            <FormItem label="调度并发数" name="scheduleConcurrency">
              <InputNumber
                v-model:value="formState.scheduleConcurrency"
                :min="1"
                :max="100"
                class="w-full"
              />
            </FormItem>
            <FormItem label="排队超时（秒）" name="queueTimeout">
              <InputNumber
                v-model:value="formState.queueTimeout"
                :min="60"
                :max="3600"
                class="w-full"
              />
            </FormItem>
            <FormItem label="按级别优先" name="priorityByLevel">
              <Switch v-model:checked="formState.priorityByLevel" />
              <span class="ml-2 text-xs text-gray-500">
                <Tag color="red">1 级</Tag>
                <Tag color="orange">2 级</Tag>
                <Tag color="blue">3 级</Tag>
                关键回路优先调度
              </span>
            </FormItem>
          </div>
        </Form>
      </Card>

      <!-- 保存按钮 -->
      <div class="flex justify-end gap-2">
        <ClpmToolbarButton
          icon="ant-design:reload-outlined"
          :loading="loading"
          label="刷新"
          @click="loadStrategy"
        />
        <ClpmToolbarButton
          v-permission="['ADMIN']"
          icon="ant-design:save-outlined"
          variant="primary"
          :loading="saving"
          label="保存配置"
          @click="handleSave"
        />
      </div>
    </div>

    <!-- 变更确认弹窗 -->
    <Modal
      v-model:open="confirmVisible"
      title="确认变更任务策略"
      :confirm-loading="saving"
      ok-text="确认保存"
      cancel-text="取消"
      width="520px"
      @ok="confirmSave"
    >
      <div class="space-y-3 py-2">
        <div class="text-sm">
          <div class="mb-1 font-medium">影响范围</div>
          <p class="rounded bg-orange-50 p-2 text-xs text-orange-700">
            任务策略变更后将影响下一次评估调度的执行方式，包括触发时机、并发数与重试策略。已运行中的任务不受影响。
          </p>
        </div>
        <div class="text-sm">
          <div class="mb-1 font-medium">变更说明（可选）</div>
          <Input.TextArea
            v-model:value="changeRemark"
            placeholder="请简要说明本次变更原因，便于追溯"
            :rows="2"
          />
        </div>
      </div>
    </Modal>
  </Page>
</template>
