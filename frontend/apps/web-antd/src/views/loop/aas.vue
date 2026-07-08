<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Radio,
  Spin,
  Switch,
  Tag,
  message,
} from 'ant-design-vue';

import {
  getDatasourceConfigApi,
  testHistoryApiApi,
  testSignalrApi,
  updateDatasourceConfigApi,
} from '#/api/datasource';
import type { DataSourceApi } from '#/api/datasource';

defineOptions({ name: 'LoopAas' });

const loading = ref(false);
const savingHistory = ref(false);
const savingSignalr = ref(false);
const testingHistory = ref(false);
const testingSignalr = ref(false);

const config = ref<DataSourceApi.DataSourceConfig | null>(null);

const form = reactive({
  dataSourceType: 'tdengine' as DataSourceApi.DataSourceType,
  historyApiUrl: '',
  historyApiToken: '',
  historyApiTimeout: 30,
  signalrHubUrl: '',
  signalrEnabled: false,
  signalrReconnectInterval: 5,
});

const historyTestResult = ref<DataSourceApi.TestResult | null>(null);
const signalrTestResult = ref<DataSourceApi.TestResult | null>(null);

// 配置与运行态是否不一致（需重启后端才能生效）
const needRestart = computed(() => {
  if (!config.value) return false;
  return (
    form.dataSourceType !== config.value.historyProviderActive ||
    form.signalrEnabled !== config.value.signalrSubscriberRunning
  );
});

async function loadConfig() {
  loading.value = true;
  try {
    const data = await getDatasourceConfigApi();
    config.value = data;
    form.dataSourceType = data.dataSourceType;
    form.historyApiUrl = data.historyApiUrl ?? '';
    form.historyApiToken = data.historyApiToken ?? '';
    form.historyApiTimeout = data.historyApiTimeout;
    form.signalrHubUrl = data.signalrHubUrl ?? '';
    form.signalrEnabled = data.signalrEnabled;
    form.signalrReconnectInterval = data.signalrReconnectInterval;
    historyTestResult.value = null;
    signalrTestResult.value = null;
  } finally {
    loading.value = false;
  }
}

async function saveHistoryConfig() {
  savingHistory.value = true;
  try {
    const data = await updateDatasourceConfigApi({
      dataSourceType: form.dataSourceType,
      historyApiUrl: form.historyApiUrl || undefined,
      historyApiToken: form.historyApiToken || undefined,
      historyApiTimeout: form.historyApiTimeout,
    });
    config.value = data;
    message.success('历史数据源配置已保存');
  } finally {
    savingHistory.value = false;
  }
}

async function saveSignalrConfig() {
  savingSignalr.value = true;
  try {
    const data = await updateDatasourceConfigApi({
      signalrHubUrl: form.signalrHubUrl || undefined,
      signalrEnabled: form.signalrEnabled,
      signalrReconnectInterval: form.signalrReconnectInterval,
    });
    config.value = data;
    message.success('实时数据源配置已保存');
  } finally {
    savingSignalr.value = false;
  }
}

async function testHistory() {
  testingHistory.value = true;
  historyTestResult.value = null;
  try {
    // 先保存当前表单配置，再测试（测试接口使用已保存的配置）
    const data = await updateDatasourceConfigApi({
      dataSourceType: form.dataSourceType,
      historyApiUrl: form.historyApiUrl || undefined,
      historyApiToken: form.historyApiToken || undefined,
      historyApiTimeout: form.historyApiTimeout,
    });
    config.value = data;
    const result = await testHistoryApiApi();
    historyTestResult.value = result;
  } finally {
    testingHistory.value = false;
  }
}

async function testSignalr() {
  testingSignalr.value = true;
  signalrTestResult.value = null;
  try {
    const data = await updateDatasourceConfigApi({
      signalrHubUrl: form.signalrHubUrl || undefined,
      signalrEnabled: form.signalrEnabled,
      signalrReconnectInterval: form.signalrReconnectInterval,
    });
    config.value = data;
    const result = await testSignalrApi();
    signalrTestResult.value = result;
  } finally {
    testingSignalr.value = false;
  }
}

onMounted(loadConfig);
</script>

<template>
  <div class="p-4">
    <Spin :spinning="loading">
      <!-- 顶部状态条 -->
      <Card class="mb-4" :body-style="{ padding: '16px' }" size="small">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-wrap items-center gap-6">
            <div class="flex items-center gap-2">
              <span class="text-gray-500">历史数据源</span>
              <Tag
                :color="
                  config?.historyProviderActive === 'remote_api'
                    ? 'blue'
                    : 'default'
                "
              >
                {{
                  config?.historyProviderActive === 'remote_api'
                    ? '外部 API'
                    : '本地 TDengine'
                }}
              </Tag>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-gray-500">实时订阅</span>
              <Tag :color="config?.signalrSubscriberRunning ? 'green' : 'default'">
                {{ config?.signalrSubscriberRunning ? '运行中' : '未启动' }}
              </Tag>
            </div>
          </div>
          <Button
            v-if="needRestart"
            type="primary"
            ghost
            size="small"
            @click="loadConfig"
          >
            刷新状态
          </Button>
        </div>
      </Card>

      <!-- 重启提示 -->
      <Alert
        v-if="needRestart"
        class="mb-4"
        type="warning"
        show-icon
        message="部分配置需重启后端生效"
        description="数据源类型切换与实时订阅启停在后端启动时初始化，修改后需重启后端服务才能完全生效。API 地址 / Token / 超时 / Hub URL / 重连间隔可即时生效。"
      />

      <!-- 历史数据源配置 -->
      <Card class="mb-4" size="small" title="历史数据源">
        <Form layout="vertical" :model="form">
          <Form.Item label="数据源类型">
            <Radio.Group v-model:value="form.dataSourceType">
              <Radio value="tdengine">本地 TDengine（开发环境）</Radio>
              <Radio value="remote_api">外部 API（生产环境）</Radio>
            </Radio.Group>
          </Form.Item>

          <template v-if="form.dataSourceType === 'remote_api'">
            <Form.Item label="API 地址">
              <Input
                v-model:value="form.historyApiUrl"
                placeholder="http://192.168.1.100:8100/api/services/v1/HistoryData/Get"
              />
            </Form.Item>
            <Form.Item label="鉴权 Token">
              <Input.Password
                v-model:value="form.historyApiToken"
                placeholder="如需鉴权请填写"
              />
            </Form.Item>
            <Form.Item label="请求超时（秒）">
              <InputNumber
                v-model:value="form.historyApiTimeout"
                :max="120"
                :min="5"
              />
            </Form.Item>
          </template>

          <div class="flex items-center gap-3">
            <Button
              type="primary"
              :loading="savingHistory"
              @click="saveHistoryConfig"
            >
              保存配置
            </Button>
            <Button
              v-if="form.dataSourceType === 'remote_api'"
              :loading="testingHistory"
              @click="testHistory"
            >
              测试连接
            </Button>
            <Tag
              v-if="historyTestResult"
              :color="historyTestResult.success ? 'green' : 'red'"
            >
              {{ historyTestResult.message }}<template
                v-if="historyTestResult.latencyMs"
              >
                ({{ historyTestResult.latencyMs }}ms)
              </template>
            </Tag>
          </div>
        </Form>
      </Card>

      <!-- 实时数据源配置 -->
      <Card size="small" title="实时数据源">
        <Form layout="vertical" :model="form">
          <Form.Item label="启用实时数据订阅">
            <div class="flex items-center gap-2">
              <Switch v-model:checked="form.signalrEnabled" />
              <span class="text-gray-400 text-sm">
                关闭时使用本地模拟器（开发环境）
              </span>
            </div>
          </Form.Item>

          <template v-if="form.signalrEnabled">
            <Form.Item label="SignalR Hub URL">
              <Input
                v-model:value="form.signalrHubUrl"
                placeholder="ws://192.168.1.100:8100/signalr/realValueForClpmHub"
              />
            </Form.Item>
            <Form.Item label="断线重连间隔（秒）">
              <InputNumber
                v-model:value="form.signalrReconnectInterval"
                :max="60"
                :min="1"
              />
            </Form.Item>
          </template>

          <div class="flex items-center gap-3">
            <Button
              type="primary"
              :loading="savingSignalr"
              @click="saveSignalrConfig"
            >
              保存配置
            </Button>
            <Button
              v-if="form.signalrEnabled"
              :loading="testingSignalr"
              @click="testSignalr"
            >
              测试连接
            </Button>
            <Tag
              v-if="signalrTestResult"
              :color="signalrTestResult.success ? 'green' : 'red'"
            >
              {{ signalrTestResult.message }}<template
                v-if="signalrTestResult.latencyMs"
              >
                ({{ signalrTestResult.latencyMs }}ms)
              </template>
            </Tag>
          </div>
        </Form>
      </Card>
    </Spin>
  </div>
</template>
