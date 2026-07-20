<script lang="ts" setup>
/**
 * 数据接入页面 — 3 Tab 结构
 *
 * v6.1：DCS 品牌管理与 MODE 映射矩阵
 * - Tab 1: 数据源（历史 TDengine/API + 实时 SignalR）
 * - Tab 2: DCS 系统（品牌/型号 CRUD）
 * - Tab 3: MODE 矩阵（映射矩阵视图 + MODE 定义编辑）
 */
import type { TableColumnsType, UploadProps } from 'ant-design-vue';

import type { DataSourceApi } from '#/api/datasource';
import type { DcsApi } from '#/api/dcs';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import {
  Alert,
  Button,
  Card,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Spin,
  Switch,
  Table,
  TabPane,
  Tabs,
  Tag,
  Upload,
} from 'ant-design-vue';

import {
  getDatasourceConfigApi,
  testHistoryApiApi,
  testSignalrApi,
  updateDatasourceConfigApi,
} from '#/api/datasource';
import {
  createModelApi,
  createVendorApi,
  deleteModelApi,
  deleteVendorApi,
  exportModelsApi,
  exportVendorsApi,
  getModeDefinitionsApi,
  getModelsApi,
  getModeMatrixApi,
  getVendorsApi,
  importModelsApi,
  importVendorsApi,
  updateModeDefinitionApi,
  updateModelApi,
  updateVendorApi,
  upsertModeMappingApi,
} from '#/api/dcs';

defineOptions({ name: 'LoopAas' });

const activeTab = ref('datasource');

// =========================================================================
// Tab 1: 数据源配置
// =========================================================================
const loading = ref(false);
const savingHistory = ref(false);
const savingSignalr = ref(false);
const testingHistory = ref(false);
const testingSignalr = ref(false);

const config = ref<DataSourceApi.DataSourceConfig | null>(null);

const form = reactive({
  networkMode: 'lan' as DataSourceApi.NetworkMode,
  historyApiUrl: '',
  historyApiToken: '',
  historyApiTimeout: 30,
  signalrHubUrl: '',
  signalrEnabled: false,
  signalrReconnectInterval: 5,
});

// 默认局域网地址（公网切换由 Tailscale 子网路由透明转发，URL 不变）
const DEFAULT_HISTORY_API_URL =
  'http://192.168.100.2:81/api/services/v1/HistoryData/Get';
const DEFAULT_SIGNALR_HUB_URL =
  'ws://192.168.100.2:81/signalr/realValueForClpmHub';

// 网络模式切换状态
const switchingNetwork = ref(false);
const tailscaleSwitchResult = ref<DataSourceApi.TailscaleSwitchResult | null>(
  null,
);

const historyTestResult = ref<DataSourceApi.TestResult | null>(null);
const signalrTestResult = ref<DataSourceApi.TestResult | null>(null);

const needRestart = computed(() => {
  if (!config.value) return false;
  return form.signalrEnabled !== config.value.signalrSubscriberRunning;
});

async function switchNetworkMode(mode: DataSourceApi.NetworkMode) {
  if (form.networkMode === mode || switchingNetwork.value) return;
  switchingNetwork.value = true;
  tailscaleSwitchResult.value = null;
  try {
    const data = await updateDatasourceConfigApi({ networkMode: mode });
    config.value = data;
    form.networkMode = data.networkMode;
    tailscaleSwitchResult.value = data.tailscaleSwitch;
    if (data.tailscaleSwitch) {
      const { status, message: msg } = data.tailscaleSwitch;
      if (status === 'success') {
        message.success(msg);
      } else if (status === 'skipped') {
        message.info(msg);
      } else {
        message.warning(msg);
      }
    }
  } finally {
    switchingNetwork.value = false;
  }
}

// Radio.Group change 事件适配 — ant-design-vue 的 RadioChangeEvent.target.value 为 optional
function handleNetworkModeChange(e: { target: { value?: unknown } }) {
  const value = e?.target?.value as DataSourceApi.NetworkMode | undefined;
  if (value) switchNetworkMode(value);
}

async function loadConfig() {
  loading.value = true;
  try {
    const data = await getDatasourceConfigApi();
    config.value = data;
    form.networkMode = data.networkMode;
    form.historyApiUrl = data.historyApiUrl ?? '';
    form.historyApiToken = data.historyApiToken ?? '';
    form.historyApiTimeout = data.historyApiTimeout;
    form.signalrHubUrl = data.signalrHubUrl ?? '';
    form.signalrEnabled = data.signalrEnabled;
    form.signalrReconnectInterval = data.signalrReconnectInterval;
    historyTestResult.value = null;
    signalrTestResult.value = null;
    tailscaleSwitchResult.value = null;
  } finally {
    loading.value = false;
  }
}

async function saveHistoryConfig() {
  savingHistory.value = true;
  try {
    const data = await updateDatasourceConfigApi({
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
    const data = await updateDatasourceConfigApi({
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

// =========================================================================
// Tab 2: DCS 系统（品牌/型号管理）
// =========================================================================
const vendorsLoading = ref(false);
const vendors = ref<DcsApi.Vendor[]>([]);
const modelsLoading = ref(false);
const models = ref<DcsApi.Model[]>([]);

const vendorModalVisible = ref(false);
const vendorModalMode = ref<'create' | 'edit'>('create');
const vendorForm = reactive({
  id: '',
  code: '',
  name: '',
  nameEn: '',
  description: '',
  sortOrder: 0,
  isActive: true,
});

const modelModalVisible = ref(false);
const modelModalMode = ref<'create' | 'edit'>('create');
const modelForm = reactive({
  id: '',
  vendorId: '' as string,
  code: '',
  name: '',
  description: '',
  sortOrder: 0,
  isActive: true,
});

const vendorColumns: TableColumnsType = [
  { title: '品牌代码', dataIndex: 'code', key: 'code', width: 140 },
  { title: '中文名', dataIndex: 'name', key: 'name', width: 140 },
  { title: '英文名', dataIndex: 'nameEn', key: 'nameEn', width: 140 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '排序', dataIndex: 'sortOrder', key: 'sortOrder', width: 80 },
  { title: '状态', dataIndex: 'isActive', key: 'isActive', width: 80 },
  { title: '操作', key: 'action', width: 120, fixed: 'right' },
];

const modelColumns: TableColumnsType = [
  { title: '型号代码', dataIndex: 'code', key: 'code', width: 180 },
  { title: '型号名称', dataIndex: 'name', key: 'name', width: 200 },
  { title: '品牌', dataIndex: 'vendorName', key: 'vendorName', width: 120 },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '排序', dataIndex: 'sortOrder', key: 'sortOrder', width: 80 },
  { title: '状态', dataIndex: 'isActive', key: 'isActive', width: 80 },
  { title: '操作', key: 'action', width: 120, fixed: 'right' },
];

async function loadVendors() {
  vendorsLoading.value = true;
  try {
    vendors.value = await getVendorsApi();
  } finally {
    vendorsLoading.value = false;
  }
}

async function loadModels() {
  modelsLoading.value = true;
  try {
    models.value = await getModelsApi();
  } finally {
    modelsLoading.value = false;
  }
}

function openVendorModal(record?: any) {
  if (record) {
    vendorModalMode.value = 'edit';
    Object.assign(vendorForm, {
      id: record.id,
      code: record.code,
      name: record.name,
      nameEn: record.nameEn ?? '',
      description: record.description ?? '',
      sortOrder: record.sortOrder,
      isActive: record.isActive,
    });
  } else {
    vendorModalMode.value = 'create';
    Object.assign(vendorForm, {
      id: '',
      code: '',
      name: '',
      nameEn: '',
      description: '',
      sortOrder: 0,
      isActive: true,
    });
  }
  vendorModalVisible.value = true;
}

async function saveVendor() {
  try {
    if (vendorModalMode.value === 'create') {
      await createVendorApi({
        code: vendorForm.code,
        name: vendorForm.name,
        nameEn: vendorForm.nameEn || undefined,
        description: vendorForm.description || undefined,
        sortOrder: vendorForm.sortOrder,
      });
      message.success('品牌创建成功');
    } else {
      await updateVendorApi(vendorForm.id, {
        name: vendorForm.name,
        nameEn: vendorForm.nameEn || undefined,
        description: vendorForm.description || undefined,
        sortOrder: vendorForm.sortOrder,
        isActive: vendorForm.isActive,
      });
      message.success('品牌更新成功');
    }
    vendorModalVisible.value = false;
    await loadVendors();
    // 如果当前在矩阵 Tab，同步刷新矩阵（品牌名可能变化）
    if (matrix.value) await loadMatrix();
  } catch {
    // error handled by request interceptor
  }
}

async function removeVendor(record: any) {
  try {
    await deleteVendorApi(record.id);
    message.success('品牌已删除');
    await loadVendors();
    await loadModels();
  } catch {
    // error handled by request interceptor
  }
}

function openModelModal(record?: any) {
  if (record) {
    modelModalMode.value = 'edit';
    Object.assign(modelForm, {
      id: record.id,
      vendorId: record.vendorId,
      code: record.code,
      name: record.name,
      description: record.description ?? '',
      sortOrder: record.sortOrder,
      isActive: record.isActive,
    });
  } else {
    modelModalMode.value = 'create';
    Object.assign(modelForm, {
      id: '',
      vendorId: vendors.value[0]?.id ?? '',
      code: '',
      name: '',
      description: '',
      sortOrder: 0,
      isActive: true,
    });
  }
  modelModalVisible.value = true;
}

async function saveModel() {
  try {
    if (modelModalMode.value === 'create') {
      await createModelApi({
        vendorId: modelForm.vendorId,
        code: modelForm.code,
        name: modelForm.name,
        description: modelForm.description || undefined,
        sortOrder: modelForm.sortOrder,
      });
      message.success('型号创建成功');
    } else {
      await updateModelApi(modelForm.id, {
        name: modelForm.name,
        description: modelForm.description || undefined,
        sortOrder: modelForm.sortOrder,
        isActive: modelForm.isActive,
      });
      message.success('型号更新成功');
    }
    modelModalVisible.value = false;
    await loadModels();
    // 如果当前在矩阵 Tab，同步刷新矩阵（新增型号会作为新行出现）
    if (matrix.value) await loadMatrix();
  } catch {
    // error handled by request interceptor
  }
}

async function removeModel(record: any) {
  try {
    await deleteModelApi(record.id);
    message.success('型号已删除');
    await loadModels();
  } catch {
    // error handled by request interceptor
  }
}

// ===== 品牌导入导出（v6.1） =====
const vendorExporting = ref(false);
const vendorImporting = ref(false);

async function handleExportVendors() {
  vendorExporting.value = true;
  const hide = message.loading('正在生成品牌导出文件…', 0);
  try {
    const blob = await exportVendorsApi();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `DCS品牌_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    hide();
    message.success('导出成功');
  } catch (error) {
    hide();
    console.error('品牌导出失败:', error);
  } finally {
    vendorExporting.value = false;
  }
}

function handleImportVendorBeforeUpload(file: File): boolean {
  vendorImporting.value = true;
  const hide = message.loading(`正在导入品牌文件「${file.name}」…`, 0);
  importVendorsApi(file)
    .then((result) => {
      hide();
      message.success(
        `导入完成：共 ${result.total} 条，新增 ${result.inserted}，更新 ${result.updated}，失败 ${result.failed}`,
      );
      loadVendors();
    })
    .catch((error) => {
      hide();
      console.error('品牌导入失败:', error);
    })
    .finally(() => {
      vendorImporting.value = false;
    });
  return false;
}

const vendorUploadProps: UploadProps = {
  accept: '.xlsx,.xls',
  showUploadList: false,
  beforeUpload: handleImportVendorBeforeUpload as UploadProps['beforeUpload'],
};

// ===== 型号导入导出（v6.1） =====
const modelExporting = ref(false);
const modelImporting = ref(false);

async function handleExportModels() {
  modelExporting.value = true;
  const hide = message.loading('正在生成型号导出文件…', 0);
  try {
    const blob = await exportModelsApi();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `DCS型号_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    hide();
    message.success('导出成功');
  } catch (error) {
    hide();
    console.error('型号导出失败:', error);
  } finally {
    modelExporting.value = false;
  }
}

function handleImportModelBeforeUpload(file: File): boolean {
  modelImporting.value = true;
  const hide = message.loading(`正在导入型号文件「${file.name}」…`, 0);
  importModelsApi(file)
    .then((result) => {
      hide();
      message.success(
        `导入完成：共 ${result.total} 条，新增 ${result.inserted}，更新 ${result.updated}，失败 ${result.failed}`,
      );
      loadModels();
      // 矩阵视图也需刷新（新增型号会作为新列）
      if (matrix.value) loadMatrix();
    })
    .catch((error) => {
      hide();
      console.error('型号导入失败:', error);
    })
    .finally(() => {
      modelImporting.value = false;
    });
  return false;
}

const modelUploadProps: UploadProps = {
  accept: '.xlsx,.xls',
  showUploadList: false,
  beforeUpload: handleImportModelBeforeUpload as UploadProps['beforeUpload'],
};

// =========================================================================
// Tab 3: MODE 矩阵（转置：行=DCS 型号，列=标准 MODE）
// =========================================================================
const matrixLoading = ref(false);
const matrix = ref<DcsApi.ModeMatrixView | null>(null);
const modeDefs = ref<DcsApi.ModeDefinition[]>([]);

async function loadMatrix() {
  matrixLoading.value = true;
  try {
    const [matrixData, defsData] = await Promise.all([
      getModeMatrixApi(),
      getModeDefinitionsApi(),
    ]);
    matrix.value = matrixData;
    modeDefs.value = defsData;
  } finally {
    matrixLoading.value = false;
  }
}

/**
 * 转置后的矩阵列定义：品牌 | 型号名称 | MODE 0 | MODE 1 | ... | MODE 4 | 操作
 * - MODE 列标题显示"标准值 - 中文标签"，计入自控率的列加蓝色 Tag
 */
const matrixColumns = computed<TableColumnsType>(() => {
  if (!matrix.value) return [];
  const cols: TableColumnsType = [
    {
      title: '品牌',
      dataIndex: 'vendorName',
      key: 'vendorName',
      width: 120,
      fixed: 'left',
    },
    {
      title: 'DCS 型号',
      dataIndex: 'modelName',
      key: 'modelName',
      width: 160,
      fixed: 'left',
    },
  ];
  // 动态列：各标准 MODE（从 modeDefs 获取标签信息）
  for (const d of modeDefs.value) {
    cols.push({
      title: `${d.standardMode} - ${d.labelZh}`,
      dataIndex: `mode_${d.standardMode}`,
      key: `mode_${d.standardMode}`,
      width: 130,
      align: 'center',
    });
  }
  // 操作列
  cols.push({
    title: '操作',
    key: 'action',
    width: 80,
    fixed: 'right',
  });
  return cols;
});

/**
 * 转置后的矩阵行数据：第一行为本系统默认，后续行为各 DCS 型号
 * 每行包含 modelId/modelName/vendorName + 各标准 MODE 的映射值
 */
const matrixData = computed(() => {
  const currentMatrix = matrix.value;
  if (!currentMatrix) return [];
  return currentMatrix.columns.map((col) => {
    const modelKey = col.modelId ?? 'default';
    const row: Record<string, any> = {
      key: modelKey,
      modelId: col.modelId,
      modelName: col.modelName ?? '本系统默认',
      vendorName: col.vendorName ?? '—',
    };
    // 填充各标准 MODE 的映射值
    for (const modeRow of currentMatrix.rows) {
      const cell = modeRow.columns.find(
        (c: DcsApi.MatrixColumn) => (c.modelId ?? 'default') === modelKey,
      );
      row[`mode_${modeRow.standardMode}`] = cell?.rawModeValue ?? null;
    }
    return row;
  });
});

/** 更新 MODE 定义的 is_auto 字段 */
async function toggleModeAuto(record: any, checked: any) {
  try {
    await updateModeDefinitionApi(record.standardMode, { isAuto: checked });
    message.success(
      `${record.labelZh}（MODE=${record.standardMode}）已${checked ? '计入' : '移出'}自控率`,
    );
    await loadMatrix();
  } catch {
    // error handled by request interceptor
  }
}

/** 从矩阵页面删除型号 */
async function removeModelFromMatrix(record: any) {
  if (!record.modelId) return;
  try {
    await deleteModelApi(record.modelId);
    message.success('型号已删除');
    await Promise.all([loadMatrix(), loadModels()]);
  } catch {
    // error handled by request interceptor
  }
}

// 矩阵单元格编辑弹窗
const cellEditVisible = ref(false);
const cellEditForm = reactive({
  modelId: null as null | string,
  modelName: '',
  standardMode: 0,
  standardLabel: '',
  rawModeValue: 0,
});

/** 打开单元格编辑弹窗（转置后：从行=型号、列=标准MODE 提取信息） */
function openCellEdit(record: any, column: any) {
  const colKey = column.key as string;
  const standardMode = Number.parseInt(colKey.replace('mode_', ''), 10);
  const modeRow = matrix.value?.rows.find(
    (r: DcsApi.MatrixRow) => r.standardMode === standardMode,
  );
  cellEditForm.modelId = record.modelId;
  cellEditForm.modelName = record.modelName ?? '本系统默认';
  cellEditForm.standardMode = standardMode;
  cellEditForm.standardLabel = modeRow
    ? `${standardMode} - ${modeRow.labelZh}`
    : `MODE ${standardMode}`;
  const currentValue = record[colKey];
  cellEditForm.rawModeValue = currentValue ?? standardMode;
  cellEditVisible.value = true;
}

async function saveCellEdit() {
  try {
    await upsertModeMappingApi({
      dcsModelId: cellEditForm.modelId,
      standardMode: cellEditForm.standardMode,
      rawModeValue: cellEditForm.rawModeValue,
    });
    message.success('映射已保存');
    cellEditVisible.value = false;
    await loadMatrix();
  } catch {
    // error handled by request interceptor
  }
}

// =========================================================================
// Tab 切换时加载数据
// =========================================================================
watch(activeTab, (tab: string) => {
  if (tab === 'dcs' && vendors.value.length === 0) {
    loadVendors();
    loadModels();
  } else if (tab === 'matrix') {
    if (!matrix.value) loadMatrix();
    if (vendors.value.length === 0) loadVendors();
  }
});

onMounted(loadConfig);
</script>

<template>
  <div class="p-4">
    <Tabs v-model:active-key="activeTab">
      <!-- Tab 1: 数据源 -->
      <TabPane key="datasource" tab="数据源">
        <Spin :spinning="loading">
          <!-- 顶部状态条 -->
          <Card class="mb-4" :body-style="{ padding: '16px' }" size="small">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="flex flex-wrap items-center gap-6">
                <div class="flex items-center gap-2">
                  <span class="text-gray-500">网络模式</span>
                  <Tag :color="form.networkMode === 'wan' ? 'purple' : 'green'">
                    {{
                      form.networkMode === 'wan'
                        ? '公网（Tailscale）'
                        : '局域网直连'
                    }}
                  </Tag>
                  <Tag
                    v-if="config && !config.tailscaleAvailable"
                    color="default"
                  >
                    Tailscale 未安装
                  </Tag>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-gray-500">实时订阅</span>
                  <Tag
                    :color="
                      config?.signalrSubscriberRunning ? 'green' : 'default'
                    "
                  >
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
            message="实时订阅启停需重启后端生效"
            description="实时订阅器在后端启动时初始化，修改后需重启后端服务才能完全生效。API 地址 / Token / 超时 / Hub URL / 重连间隔 / 网络模式可即时生效。"
          />

          <!-- 网络模式切换 -->
          <Card class="mb-4" size="small" title="网络模式">
            <Form layout="vertical" :model="form">
              <FormItem label="链路路径">
                <Radio.Group
                  :value="form.networkMode"
                  :disabled="switchingNetwork"
                  @change="handleNetworkModeChange"
                >
                  <Radio value="lan">局域网（直连，默认）</Radio>
                  <Radio value="wan">公网（走 Tailscale 子网路由）</Radio>
                </Radio.Group>
              </FormItem>

              <Alert
                v-if="config && !config.tailscaleAvailable"
                class="mb-2"
                type="info"
                show-icon
                message="当前环境未检测到 tailscale 客户端（容器/未安装），切换将被静默跳过"
              />

              <Alert
                v-if="tailscaleSwitchResult"
                class="mb-2"
                :type="
                  tailscaleSwitchResult.status === 'success'
                    ? 'success'
                    : tailscaleSwitchResult.status === 'skipped'
                      ? 'info'
                      : 'error'
                "
                show-icon
                :message="tailscaleSwitchResult.message"
                :description="
                  tailscaleSwitchResult.latencyMs !== null
                    ? `耗时 ${tailscaleSwitchResult.latencyMs}ms`
                    : undefined
                "
              />

              <div class="text-gray-400 text-xs">
                局域网模式移除 192.168.100.0/24 子网路由，直连
                AAS；公网模式安装子网路由，通过 zpdev Tailscale 转发。两模式 URL
                相同，由 Tailscale 透明路由。
              </div>
            </Form>
          </Card>

          <!-- 历史数据源配置（仅数据导入时调用） -->
          <Card
            class="mb-4"
            size="small"
            title="历史数据导入接口（仅「数据管理 → 历史数据导入」时调用）"
          >
            <Form layout="vertical" :model="form">
              <FormItem label="API 地址">
                <Input
                  v-model:value="form.historyApiUrl"
                  :placeholder="DEFAULT_HISTORY_API_URL"
                />
              </FormItem>
              <FormItem label="鉴权 Token">
                <Input.Password
                  v-model:value="form.historyApiToken"
                  placeholder="如需鉴权请填写"
                />
              </FormItem>
              <FormItem label="请求超时（秒）">
                <InputNumber
                  v-model:value="form.historyApiTimeout"
                  :max="120"
                  :min="5"
                />
              </FormItem>

              <div class="text-gray-400 mb-3 text-xs">
                性能评估、回路诊断等计算任务一律读取本地
                TDengine，不调用此接口。
              </div>

              <div class="flex items-center gap-3">
                <Button
                  type="primary"
                  :loading="savingHistory"
                  @click="saveHistoryConfig"
                >
                  保存配置
                </Button>
                <Button :loading="testingHistory" @click="testHistory">
                  测试连接
                </Button>
                <Tag
                  v-if="historyTestResult"
                  :color="historyTestResult.success ? 'green' : 'red'"
                >
                  {{ historyTestResult.message
                  }}<template v-if="historyTestResult.latencyMs">
                    ({{ historyTestResult.latencyMs }}ms)
                  </template>
                </Tag>
              </div>
            </Form>
          </Card>

          <!-- 实时数据源配置 -->
          <Card size="small" title="实时数据源">
            <Form layout="vertical" :model="form">
              <FormItem label="启用实时数据订阅">
                <div class="flex items-center gap-2">
                  <Switch v-model:checked="form.signalrEnabled" />
                  <span class="text-gray-400 text-sm">
                    关闭时使用本地模拟器（开发环境）
                  </span>
                </div>
              </FormItem>

              <template v-if="form.signalrEnabled">
                <FormItem label="SignalR Hub URL">
                  <Input
                    v-model:value="form.signalrHubUrl"
                    :placeholder="DEFAULT_SIGNALR_HUB_URL"
                  />
                </FormItem>
                <FormItem label="断线重连间隔（秒）">
                  <InputNumber
                    v-model:value="form.signalrReconnectInterval"
                    :max="60"
                    :min="1"
                  />
                </FormItem>
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
                  {{ signalrTestResult.message
                  }}<template v-if="signalrTestResult.latencyMs">
                    ({{ signalrTestResult.latencyMs }}ms)
                  </template>
                </Tag>
              </div>
            </Form>
          </Card>
        </Spin>
      </TabPane>

      <!-- Tab 2: DCS 系统 -->
      <TabPane key="dcs" tab="DCS 系统">
        <!-- 品牌管理 -->
        <Card class="mb-4" size="small" title="DCS 品牌">
          <template #extra>
            <div class="flex items-center gap-2">
              <Button type="primary" size="small" @click="openVendorModal()">
                新增品牌
              </Button>
              <Upload v-bind="vendorUploadProps">
                <Button size="small" :loading="vendorImporting">导入</Button>
              </Upload>
              <Button
                size="small"
                :loading="vendorExporting"
                @click="handleExportVendors"
              >
                导出
              </Button>
            </div>
          </template>
          <Table
            :columns="vendorColumns"
            :data-source="vendors"
            :loading="vendorsLoading"
            :pagination="false"
            row-key="id"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'action'">
                <Button
                  type="link"
                  size="small"
                  @click="openVendorModal(record)"
                  >编辑</Button
                >
                <Popconfirm
                  title="删除品牌？有关联型号时禁止删除"
                  @confirm="removeVendor(record)"
                >
                  <Button type="link" size="small" danger>删除</Button>
                </Popconfirm>
              </template>
            </template>
          </Table>
        </Card>

        <!-- 型号管理 -->
        <Card size="small" title="DCS 型号">
          <template #extra>
            <div class="flex items-center gap-2">
              <Button type="primary" size="small" @click="openModelModal()"
                >新增型号</Button
              >
              <Upload v-bind="modelUploadProps">
                <Button size="small" :loading="modelImporting">导入</Button>
              </Upload>
              <Button
                size="small"
                :loading="modelExporting"
                @click="handleExportModels"
              >
                导出
              </Button>
            </div>
          </template>
          <Table
            :columns="modelColumns"
            :data-source="models"
            :loading="modelsLoading"
            :pagination="false"
            row-key="id"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'action'">
                <Button type="link" size="small" @click="openModelModal(record)"
                  >编辑</Button
                >
                <Popconfirm
                  title="删除型号？关联回路的 dcs_model_id 将置空"
                  @confirm="removeModel(record)"
                >
                  <Button type="link" size="small" danger>删除</Button>
                </Popconfirm>
              </template>
            </template>
          </Table>
        </Card>
      </TabPane>

      <!-- Tab 3: MODE 矩阵 -->
      <TabPane key="matrix" tab="MODE 矩阵">
        <Spin :spinning="matrixLoading">
          <!-- 标准 MODE 定义（精简表，可折叠 isAuto 开关） -->
          <Card class="mb-4" size="small" title="标准 MODE 定义">
            <Table
              :columns="[
                {
                  title: 'MODE 值',
                  dataIndex: 'standardMode',
                  key: 'standardMode',
                  width: 100,
                },
                {
                  title: '中文标签',
                  dataIndex: 'labelZh',
                  key: 'labelZh',
                  width: 120,
                },
                {
                  title: '英文标签',
                  dataIndex: 'labelEn',
                  key: 'labelEn',
                  width: 120,
                },
                { title: '颜色', dataIndex: 'color', key: 'color', width: 100 },
                {
                  title: '计入自控率',
                  dataIndex: 'isAuto',
                  key: 'isAuto',
                  width: 120,
                },
                {
                  title: '描述',
                  dataIndex: 'description',
                  key: 'description',
                  ellipsis: true,
                },
              ]"
              :data-source="modeDefs"
              :pagination="false"
              row-key="id"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'color'">
                  <div class="flex items-center gap-2">
                    <span
                      class="inline-block h-4 w-4 rounded"
                      :style="{ backgroundColor: record.color }"
                    ></span>
                    <span class="text-xs text-gray-400">{{
                      record.color
                    }}</span>
                  </div>
                </template>
                <template v-if="column.key === 'isAuto'">
                  <Switch
                    :checked="record.isAuto"
                    checked-children="是"
                    un-checked-children="否"
                    @change="(checked: any) => toggleModeAuto(record, checked)"
                  />
                </template>
              </template>
            </Table>
          </Card>

          <!-- MODE 映射矩阵（转置：行=DCS 型号，列=标准 MODE） -->
          <Card size="small">
            <template #title>
              <span>MODE 映射矩阵</span>
            </template>
            <template #extra>
              <div class="flex items-center gap-2">
                <Button size="small" @click="openVendorModal()">
                  + 新增品牌
                </Button>
                <Button
                  size="small"
                  type="primary"
                  :disabled="vendors.length === 0"
                  @click="openModelModal()"
                >
                  + 新增型号
                </Button>
              </div>
            </template>
            <Alert
              class="mb-3"
              type="info"
              show-icon
              message="第一行为本系统默认 MODE 映射；后续行为各 DCS 品牌型号的实际 MODE 值。点击单元格可编辑映射值。"
            />
            <Table
              :columns="matrixColumns"
              :data-source="matrixData"
              :pagination="false"
              :scroll="{ x: 'max-content' }"
              row-key="key"
              size="small"
              bordered
            >
              <template #bodyCell="{ column, record }">
                <!-- MODE 值单元格（可点击编辑） -->
                <template v-if="String(column.key ?? '').startsWith('mode_')">
                  <span
                    class="cursor-pointer font-mono"
                    :class="
                      record[String(column.dataIndex ?? '')] != null
                        ? 'text-blue-600 hover:text-blue-800'
                        : 'text-gray-300 hover:text-gray-500'
                    "
                    @click="openCellEdit(record, column)"
                  >
                    {{ record[String(column.dataIndex ?? '')] ?? '—' }}
                  </span>
                </template>
                <!-- 操作列：删除型号（本系统默认行不显示） -->
                <template v-else-if="column.key === 'action'">
                  <Popconfirm
                    v-if="record.modelId"
                    title="确认删除该型号？"
                    ok-text="删除"
                    cancel-text="取消"
                    @confirm="removeModelFromMatrix(record)"
                  >
                    <a class="text-sm text-red-500">删除</a>
                  </Popconfirm>
                  <span v-else class="text-xs text-gray-300">默认</span>
                </template>
              </template>
            </Table>
          </Card>
        </Spin>
      </TabPane>
    </Tabs>

    <!-- 品牌编辑弹窗 -->
    <Modal
      v-model:open="vendorModalVisible"
      :title="vendorModalMode === 'create' ? '新增品牌' : '编辑品牌'"
      @ok="saveVendor"
    >
      <Form layout="vertical">
        <FormItem label="品牌代码（唯一）">
          <Input
            v-model:value="vendorForm.code"
            :disabled="vendorModalMode === 'edit'"
            placeholder="如 hollysys"
          />
        </FormItem>
        <FormItem label="中文名">
          <Input v-model:value="vendorForm.name" placeholder="如 和利时" />
        </FormItem>
        <FormItem label="英文名">
          <Input v-model:value="vendorForm.nameEn" placeholder="Hollysys" />
        </FormItem>
        <FormItem label="描述">
          <Input.TextArea v-model:value="vendorForm.description" :rows="2" />
        </FormItem>
        <FormItem label="排序">
          <InputNumber v-model:value="vendorForm.sortOrder" :min="0" />
        </FormItem>
        <FormItem v-if="vendorModalMode === 'edit'" label="启用状态">
          <Switch v-model:checked="vendorForm.isActive" />
        </FormItem>
      </Form>
    </Modal>

    <!-- 型号编辑弹窗 -->
    <Modal
      v-model:open="modelModalVisible"
      :title="modelModalMode === 'create' ? '新增型号' : '编辑型号'"
      @ok="saveModel"
    >
      <Form layout="vertical">
        <FormItem label="所属品牌">
          <Select
            v-model:value="modelForm.vendorId"
            :disabled="modelModalMode === 'edit'"
            :options="vendors.map((v) => ({ label: v.name, value: v.id }))"
            placeholder="选择品牌"
          />
        </FormItem>
        <FormItem label="型号代码（全局唯一）">
          <Input
            v-model:value="modelForm.code"
            :disabled="modelModalMode === 'edit'"
            placeholder="如 hollysys-macs"
          />
        </FormItem>
        <FormItem label="型号名称">
          <Input v-model:value="modelForm.name" placeholder="如 MACS V" />
        </FormItem>
        <FormItem label="描述">
          <Input.TextArea v-model:value="modelForm.description" :rows="2" />
        </FormItem>
        <FormItem label="排序">
          <InputNumber v-model:value="modelForm.sortOrder" :min="0" />
        </FormItem>
        <FormItem v-if="modelModalMode === 'edit'" label="启用状态">
          <Switch v-model:checked="modelForm.isActive" />
        </FormItem>
      </Form>
    </Modal>

    <!-- 矩阵单元格编辑弹窗 -->
    <Modal
      v-model:open="cellEditVisible"
      title="编辑 MODE 映射"
      :width="420"
      @ok="saveCellEdit"
    >
      <Form layout="vertical">
        <FormItem label="型号">
          <Input :value="cellEditForm.modelName" disabled />
        </FormItem>
        <FormItem label="标准 MODE">
          <Input :value="cellEditForm.standardLabel" disabled />
        </FormItem>
        <FormItem label="该型号实际 MODE 值">
          <InputNumber
            v-model:value="cellEditForm.rawModeValue"
            :min="0"
            :max="999"
          />
          <p class="mt-1 text-xs text-gray-400">
            填写该 DCS 型号在此控制模式下实际推送的 MODE 值
          </p>
        </FormItem>
      </Form>
    </Modal>
  </div>
</template>
