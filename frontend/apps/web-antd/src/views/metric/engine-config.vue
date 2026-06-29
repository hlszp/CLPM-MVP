<script lang="ts" setup>
/**
 * S3-METRIC-008 引擎规则配置页
 *
 * 对齐 IDS v3.2 §2.3 + PRD §4.3
 * - 表单展示引擎规则（计算周期/数据拉取窗口/调度并发数/启用状态）
 * - 最近执行状态信息（lastExecutedAt/lastExecutionStatus/processedLoopCount）
 * - 保存后即时生效
 * - 配置变更二次确认弹窗
 * - 仅 ADMIN 可编辑
 */
import type { MetricApi } from '#/api/metric';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Card,
  Descriptions,
  DescriptionsItem,
  Form,
  FormItem,
  InputNumber,
  message,
  Modal,
  Select,
  Switch,
  Tag,
} from 'ant-design-vue';

import { ClpmPageToolbar } from '#/components/clpm';
import ConfigTabs from '#/components/metric/config-tabs.vue';
import { getRulesApi, updateRuleApi } from '#/api/metric';

defineOptions({ name: 'MetricEngineConfig' });

const loading = ref(false);
const saving = ref(false);
const rule = ref<MetricApi.RuleItem | null>(null);

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

const formRef = ref();
const formState = reactive({
  calcPeriod: '1h',
  dataFetchWindow: '1h',
  scheduleConcurrency: 10,
  isEnabled: true,
});

/** 加载引擎规则 */
async function loadRule() {
  loading.value = true;
  try {
    const data = await getRulesApi();
    if (data.items && data.items.length > 0) {
      const firstRule = data.items[0];
      if (firstRule) {
        rule.value = firstRule;
        formState.calcPeriod = firstRule.calcPeriod;
        formState.dataFetchWindow = firstRule.dataFetchWindow;
        formState.scheduleConcurrency = firstRule.scheduleConcurrency;
        formState.isEnabled = firstRule.isEnabled;
      }
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 保存（含二次确认） */
function handleSave() {
  formRef.value?.validate().then(() => {
    Modal.confirm({
      title: '确认变更引擎规则',
      content: '引擎规则变更后立即生效，可能影响下一次评估调度。是否继续？',
      okText: '确认保存',
      cancelText: '取消',
      onOk: doSave,
    });
  });
}

/** 实际保存 */
async function doSave() {
  if (!rule.value) return;
  saving.value = true;
  try {
    await updateRuleApi(rule.value.ruleId, {
      calcPeriod: formState.calcPeriod,
      dataFetchWindow: formState.dataFetchWindow,
      scheduleConcurrency: formState.scheduleConcurrency,
      isEnabled: formState.isEnabled,
    });
    message.success('引擎规则更新成功，已即时生效');
    await loadRule();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

function formatTime(t?: string): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

function executionStatusColor(status?: string): string {
  if (!status) return 'default';
  if (status === 'SUCCESS') return 'green';
  if (status === 'FAILED') return 'red';
  if (status === 'RUNNING') return 'blue';
  return 'default';
}

function executionStatusLabel(status?: string): string {
  if (!status) return '—';
  if (status === 'SUCCESS') return '成功';
  if (status === 'FAILED') return '失败';
  if (status === 'RUNNING') return '运行中';
  return status;
}

onMounted(() => {
  loadRule();
});
</script>

<template>
  <Page>
    <ConfigTabs />
    <ClpmPageToolbar
      title="引擎规则"
      subtitle="管理计算周期、数据拉取窗口、并发度和执行状态。"
    />
    <div class="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
      <!-- 规则配置表单 -->
      <Card title="规则配置" :loading="loading">
        <Form
          ref="formRef"
          :model="formState"
          layout="vertical"
          class="pt-2"
          :disabled="loading"
        >
          <FormItem
            name="calcPeriod"
            label="计算周期"
            :rules="[{ required: true, message: '请选择计算周期' }]"
          >
            <Select
              v-model:value="formState.calcPeriod"
              :options="calcPeriodOptions"
              placeholder="请选择计算周期"
            />
          </FormItem>

          <FormItem
            name="dataFetchWindow"
            label="数据拉取窗口"
            :rules="[{ required: true, message: '请选择数据拉取窗口' }]"
          >
            <Select
              v-model:value="formState.dataFetchWindow"
              :options="dataFetchWindowOptions"
              placeholder="请选择数据拉取窗口"
            />
          </FormItem>

          <FormItem
            name="scheduleConcurrency"
            label="调度并发数"
            :rules="[{ required: true, message: '请输入调度并发数' }]"
          >
            <InputNumber
              v-model:value="formState.scheduleConcurrency"
              :min="1"
              :max="100"
              class="w-full"
            />
          </FormItem>

          <FormItem name="isEnabled" label="启用状态">
            <Switch v-model:checked="formState.isEnabled" />
          </FormItem>

          <div class="mt-4">
            <Button
              v-permission="['ADMIN']"
              type="primary"
              :loading="saving"
              @click="handleSave"
            >
              保存配置
            </Button>
          </div>
        </Form>
      </Card>

      <!-- 最近执行状态 -->
      <Card title="最近执行状态">
        <Descriptions
          v-if="rule"
          :column="1"
          bordered
          size="small"
          class="pt-2"
        >
          <DescriptionsItem label="规则名称">
            {{ rule.ruleName }}
          </DescriptionsItem>
          <DescriptionsItem label="启用状态">
            <Tag :color="rule.isEnabled ? 'green' : 'default'">
              {{ rule.isEnabled ? '启用' : '禁用' }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="最近执行时间">
            {{ formatTime(rule.lastExecutedAt) }}
          </DescriptionsItem>
          <DescriptionsItem label="执行状态">
            <Tag :color="executionStatusColor(rule.lastExecutionStatus)">
              {{ executionStatusLabel(rule.lastExecutionStatus) }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="执行耗时">
            {{
              rule.lastExecutionDuration != null
                ? `${rule.lastExecutionDuration} ms`
                : '—'
            }}
          </DescriptionsItem>
          <DescriptionsItem label="处理回路数">
            {{ rule.processedLoopCount ?? '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="更新时间">
            {{ formatTime(rule.updatedAt) }}
          </DescriptionsItem>
          <DescriptionsItem label="更新人">
            {{ rule.updatedBy || '—' }}
          </DescriptionsItem>
        </Descriptions>
        <div v-else class="py-8 text-center text-gray-400">暂无执行记录</div>
      </Card>
    </div>
  </Page>
</template>
