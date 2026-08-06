<script lang="ts" setup>
/**
 * S5-SYS-LLM LLM 配置页（P3-04 自然语言诊断解读配套）
 *
 * 让管理员自助配置 LLM 服务的 BaseURL / API Key / 模型 / 超时，而非代码写死。
 * 遵循 OpenAI 兼容接口协议，任何兼容服务均可接入（OpenAI/DeepSeek/通义/Moonshot/Ollama 等）。
 *
 * - GET 返回的 API Key 脱敏（sk-***xxxx），保存时空值=保留原值
 * - 连接测试：发一条 ping 请求，返回成功/失败 + 延迟
 * - 未启用或未配置时，诊断解读 auto 模式自动 fallback 规则模板，功能不阻断
 *
 * 权限：仅 ADMIN（路由 authority + 后端 POST /test 均 ADMIN）
 */
import type { LlmApi } from '#/api/llm';

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Select,
  Switch,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import {
  getLlmConfigApi,
  saveLlmConfigApi,
  testLlmConnectionApi,
} from '#/api/llm';
import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'SystemLlmConfig' });

const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
/** 是否已配置 API Key（GET 返回，决定保存时是否强制要求重填） */
const apiKeyConfigured = ref(false);
const lastUpdated = ref<{
  by: null | string;
  at: null | string;
} | null>(null);

/** 表单数据 */
const form = reactive({
  enabled: false,
  endpoint: '',
  apiKey: '',
  model: '',
  timeout: 30,
  maxTokens: 4096,
});

/** 连接测试结果 */
const testResult = ref<LlmApi.LlmTestResult | null>(null);

/** 常见模型预设（OpenAI 兼容） */
const MODEL_OPTIONS = [
  { label: 'gpt-4o（OpenAI）', value: 'gpt-4o' },
  { label: 'gpt-4o-mini（OpenAI，经济）', value: 'gpt-4o-mini' },
  { label: 'deepseek-chat（DeepSeek）', value: 'deepseek-chat' },
  { label: 'qwen-plus（通义千问）', value: 'qwen-plus' },
  { label: 'moonshot-v1-8k（Moonshot）', value: 'moonshot-v1-8k' },
  { label: '自定义模型…', value: '__custom__' },
];

/** 是否自定义模型（不在预设列表） */
const isCustomModel = ref(false);

/** 当前 Select 显示值 */
const modelSelectValue = ref<string | undefined>(undefined);

/** 加载配置 */
async function loadConfig() {
  loading.value = true;
  try {
    const data = await getLlmConfigApi();
    form.enabled = data.enabled;
    form.endpoint = data.endpoint ?? '';
    form.apiKey = ''; // 始终置空，保存时空=保留原值
    form.model = data.model ?? '';
    form.timeout = data.timeout;
    form.maxTokens = data.maxTokens ?? 4096;
    apiKeyConfigured.value = data.apiKeyConfigured;
    lastUpdated.value = {
      by: data.updatedBy ?? null,
      at: data.updatedAt ?? null,
    };
    isCustomModel.value =
      !!data.model && !MODEL_OPTIONS.some((o) => o.value === data.model);
    modelSelectValue.value = isCustomModel.value
      ? '__custom__'
      : (data.model ?? undefined);
    testResult.value = null;
  } catch {
    message.error('加载 LLM 配置失败');
  } finally {
    loading.value = false;
  }
}

/** 保存配置 */
async function handleSave() {
  if (form.enabled) {
    if (!form.endpoint.trim()) {
      message.warning('启用 LLM 时需填写 BaseURL');
      return;
    }
    if (!form.model.trim()) {
      message.warning('启用 LLM 时需选择/填写模型');
      return;
    }
    if (!apiKeyConfigured.value && !form.apiKey) {
      message.warning('启用 LLM 时需填写 API Key');
      return;
    }
  }
  saving.value = true;
  try {
    const data = await saveLlmConfigApi({
      enabled: form.enabled,
      endpoint: form.endpoint.trim() || undefined,
      apiKey: form.apiKey || undefined, // 空=保留原值
      model: form.model.trim() || undefined,
      timeout: form.timeout,
      maxTokens: form.maxTokens,
    });
    apiKeyConfigured.value = data.apiKeyConfigured;
    form.apiKey = ''; // 清空，保存后不再显示明文
    lastUpdated.value = {
      by: data.updatedBy ?? null,
      at: data.updatedAt ?? null,
    };
    message.success('LLM 配置已保存');
  } catch {
    message.error('保存失败，请重试');
  } finally {
    saving.value = false;
  }
}

/** 连接测试 */
async function handleTest() {
  if (!form.enabled) {
    message.warning('请先启用并保存 LLM 配置再测试');
    return;
  }
  testing.value = true;
  testResult.value = null;
  try {
    testResult.value = await testLlmConnectionApi();
  } catch {
    testResult.value = {
      success: false,
      message: '请求失败，请检查后端服务',
    };
  } finally {
    testing.value = false;
  }
}

/** 模型选择变更：选"自定义"切到输入框，选预设填入 value */
function handleModelChange(value: unknown) {
  const v = typeof value === 'string' ? value : '';
  if (v === '__custom__') {
    isCustomModel.value = true;
    form.model = '';
  } else {
    isCustomModel.value = false;
    form.model = v;
  }
}

onMounted(loadConfig);
</script>

<template>
  <Page :hide-footer="true">
    <ClpmPageToolbar title="LLM 配置" sub-title="自然语言诊断解读服务配置">
      <Button type="primary" :loading="saving" @click="handleSave">
        保存配置
      </Button>
    </ClpmPageToolbar>

    <ClpmDataCanvas :loading="loading">
      <div class="mx-auto max-w-2xl">
        <!-- 说明 -->
        <Alert
          type="info"
          show-icon
          class="mb-4"
          message="LLM 用于诊断详情页的「大白话解读」功能"
        >
          <template #description>
            遵循 OpenAI 兼容接口协议，支持 OpenAI / DeepSeek / 通义千问 /
            Moonshot / 本地 Ollama 等任何兼容服务。
            未启用或调用失败时，解读功能自动回退规则模板，<b>不会阻断使用</b>。
          </template>
        </Alert>

        <Form layout="vertical" :model="form">
          <!-- 启用开关 -->
          <FormItem label="启用 LLM 解读">
            <div class="flex items-center gap-2">
              <Switch v-model:checked="form.enabled" />
              <span class="text-sm opacity-70">
                {{
                  form.enabled
                    ? '已启用（优先 LLM，失败回退模板）'
                    : '未启用（仅规则模板）'
                }}
              </span>
            </div>
          </FormItem>

          <!-- BaseURL -->
          <FormItem label="BaseURL（API 根地址）">
            <Input
              v-model:value="form.endpoint"
              placeholder="https://api.openai.com"
              allow-clear
            />
            <template #extra>
              <span class="text-xs opacity-60">
                不含 <code>/v1</code> 后缀，系统自动拼接
                <code>{BaseURL}/v1/chat/completions</code>
              </span>
            </template>
          </FormItem>

          <!-- API Key -->
          <FormItem label="API Key">
            <Input.Password
              v-model:value="form.apiKey"
              :placeholder="
                apiKeyConfigured
                  ? '已配置（保留原值，如需修改请重新填写）'
                  : '请填写 API Key'
              "
              allow-clear
            />
            <template #extra>
              <span v-if="apiKeyConfigured" class="text-xs opacity-60">
                <Tag color="green" class="m-0" style="font-size: 11px">
                  已配置
                </Tag>
                留空保存即保留原值，不会清空
              </span>
              <span v-else class="text-xs opacity-60">未配置</span>
            </template>
          </FormItem>

          <!-- 模型 -->
          <FormItem label="模型">
            <Select
              v-if="!isCustomModel"
              v-model:value="modelSelectValue"
              placeholder="选择常用模型或切换自定义"
              :options="MODEL_OPTIONS"
              allow-clear
              @change="handleModelChange"
            />
            <Input
              v-else
              v-model:value="form.model"
              placeholder="输入模型名，如 qwen2.5-7b-instruct"
              allow-clear
            >
              <template #addonAfter>
                <Tooltip title="切回预设列表">
                  <span
                    class="cursor-pointer"
                    @click="
                      isCustomModel = false;
                      modelSelectValue = undefined;
                    "
                  >
                    预设
                  </span>
                </Tooltip>
              </template>
            </Input>
          </FormItem>

          <!-- 超时 -->
          <FormItem label="请求超时（秒）">
            <InputNumber
              v-model:value="form.timeout"
              :min="5"
              :max="300"
              :step="5"
              style="width: 100%"
            />
            <template #extra>
              <span class="text-xs opacity-60">
                建议保持默认 30 秒，网络较慢时可适当调大
              </span>
            </template>
          </FormItem>

          <!-- max_tokens -->
          <FormItem label="最大输出 Token 数">
            <InputNumber
              v-model:value="form.maxTokens"
              :min="256"
              :max="32768"
              :step="512"
              style="width: 100%"
            />
            <template #extra>
              <span class="text-xs opacity-60">
                默认 4096。推理模型（如 deepseek-r1）建议
                ≥4096，否则思考链可能耗尽 token 导致输出为空
              </span>
            </template>
          </FormItem>

          <!-- 连接测试 -->
          <FormItem label="连接测试">
            <div class="flex items-center gap-3">
              <Button :loading="testing" @click="handleTest"> 测试连接 </Button>
              <span v-if="testResult" class="flex items-center gap-2">
                <Tag :color="testResult.success ? 'green' : 'red'" class="m-0">
                  {{ testResult.success ? '成功' : '失败' }}
                </Tag>
                <span class="text-sm">
                  {{ testResult.message }}
                </span>
              </span>
            </div>
          </FormItem>
        </Form>

        <!-- 最近更新 -->
        <div
          v-if="lastUpdated?.at"
          class="mt-4 border-t pt-3 text-xs opacity-50"
        >
          最近更新：{{ lastUpdated.by ?? '—' }} ·
          {{ formatTime(lastUpdated.at) }}
        </div>
      </div>
    </ClpmDataCanvas>
  </Page>
</template>
