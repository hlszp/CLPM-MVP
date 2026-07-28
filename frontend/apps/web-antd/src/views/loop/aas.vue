<script lang="ts" setup>
/**
 * 数据接入页面 — 3 Tab 结构
 *
 * v6.1：DCS 品牌管理与 MODE 映射矩阵
 * - Tab 1: 数据源（历史 TDengine/API + 实时 SignalR）
 * - Tab 2: DCS 系统（品牌/型号 CRUD）
 * - Tab 3: MODE 矩阵（映射矩阵视图 + MODE 定义编辑）
 *
 * 2026-07-28：迁移 CLPM 统一组件体系（ClpmPageToolbar + ClpmDataCanvas +
 * ClpmDangerConfirmModal），删除/切换/测试等确认流全部由危险确认模态承载；
 * 写操作按钮补 v-permission="['ADMIN']"（对齐后端 datasource.py/dcs.py 写端点）。
 */
import type { TableColumnsType, UploadProps } from 'ant-design-vue';

import type { DataSourceApi } from '#/api/datasource';
import type { DcsApi } from '#/api/dcs';

import { computed, onMounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Radio,
  Select,
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
  getPidStructuresApi,
  getVendorsApi,
  importModelsApi,
  importVendorsApi,
  updateModeDefinitionApi,
  updateModelApi,
  updateVendorApi,
  upsertModeMappingApi,
} from '#/api/dcs';
import {
  ClpmDangerConfirmModal,
  ClpmDataCanvas,
  ClpmPageToolbar,
} from '#/components/clpm';

import PidStructureDrawer from './components/pid-structure-drawer.vue';

defineOptions({ name: 'LoopAas' });

const activeTab = ref('datasource');

// =========================================================================
// Tab 1: 数据源配置
// =========================================================================
const loading = ref(false);
/** 数据源配置加载失败态（ClpmDataCanvas error + retry） */
const configError = ref(false);
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

// Token 安全语义（阶段 8）：GET 返回打码值，不回填表单；
// 留空=不变，输入新值=覆盖，勾选清空=传空串显式清除
const savedMaskedToken = ref('');
const clearToken = ref(false);

const historyTestResult = ref<DataSourceApi.TestResult | null>(null);
const signalrTestResult = ref<DataSourceApi.TestResult | null>(null);

const needRestart = computed(() => {
  if (!config.value) return false;
  return form.signalrEnabled !== config.value.signalrSubscriberRunning;
});

// ===== 危险确认弹窗状态（ClpmDangerConfirmModal）=====
// 网络模式切换（瞬断实时链路，属高危操作）
const networkSwitchOpen = ref(false);
const networkSwitchTarget = ref<DataSourceApi.NetworkMode | null>(null);
// 测试连接（先按当前表单隐式保存配置，操作前显式确认）
const testHistoryOpen = ref(false);
const testSignalrOpen = ref(false);

const networkSwitchTargetLabel = computed(() =>
  networkSwitchTarget.value === 'wan'
    ? '公网（Tailscale 子网路由）'
    : '局域网（直连）',
);

/** 打开网络模式切换危险确认弹窗 */
function switchNetworkMode(mode: DataSourceApi.NetworkMode) {
  if (form.networkMode === mode || switchingNetwork.value) return;
  networkSwitchTarget.value = mode;
  networkSwitchOpen.value = true;
}

/** 网络模式切换危险确认回调（ClpmDangerConfirmModal @confirm） */
async function confirmNetworkSwitch() {
  const mode = networkSwitchTarget.value;
  if (!mode) return;
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
    networkSwitchOpen.value = false;
  } catch {
    // 错误提示由请求拦截器统一处理；Radio 绑定的是 form.networkMode，失败时自动停留在原模式
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
  configError.value = false;
  try {
    const data = await getDatasourceConfigApi();
    config.value = data;
    form.networkMode = data.networkMode;
    form.historyApiUrl = data.historyApiUrl ?? '';
    // Token 打码返回，不回填表单；仅在状态中展示打码值
    form.historyApiToken = '';
    savedMaskedToken.value = data.historyApiToken ?? '';
    clearToken.value = false;
    form.historyApiTimeout = data.historyApiTimeout;
    form.signalrHubUrl = data.signalrHubUrl ?? '';
    form.signalrEnabled = data.signalrEnabled;
    form.signalrReconnectInterval = data.signalrReconnectInterval;
    historyTestResult.value = null;
    signalrTestResult.value = null;
    tailscaleSwitchResult.value = null;
  } catch {
    // 错误提示由请求拦截器统一处理，此处仅更新本地错误态供 ClpmDataCanvas 展示
    configError.value = true;
  } finally {
    loading.value = false;
  }
}

/** 保存/测试后重置 Token 输入状态（响应中为打码值，仅用于展示） */
function resetTokenState(data: DataSourceApi.DataSourceConfig) {
  form.historyApiToken = '';
  clearToken.value = false;
  savedMaskedToken.value = data.historyApiToken ?? '';
}

/**
 * 构造历史数据源更新载荷：
 * - URL 传当前表单值（空串 = 显式清空）
 * - Token：勾选清空 = 传空串；留空 = 不传（保持不变）；新值 = 覆盖
 */
function buildHistoryPayload(): DataSourceApi.DataSourceConfigUpdate {
  const payload: DataSourceApi.DataSourceConfigUpdate = {
    historyApiUrl: form.historyApiUrl,
    historyApiTimeout: form.historyApiTimeout,
  };
  if (clearToken.value) {
    payload.historyApiToken = '';
  } else if (form.historyApiToken) {
    payload.historyApiToken = form.historyApiToken;
  }
  return payload;
}

async function saveHistoryConfig() {
  savingHistory.value = true;
  try {
    const data = await updateDatasourceConfigApi(buildHistoryPayload());
    config.value = data;
    resetTokenState(data);
    message.success('历史数据源配置已保存');
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    savingHistory.value = false;
  }
}

async function saveSignalrConfig() {
  savingSignalr.value = true;
  try {
    const data = await updateDatasourceConfigApi({
      signalrHubUrl: form.signalrHubUrl,
      signalrEnabled: form.signalrEnabled,
      signalrReconnectInterval: form.signalrReconnectInterval,
    });
    config.value = data;
    message.success('实时数据源配置已保存');
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    savingSignalr.value = false;
  }
}

/** 测试连接会先按当前表单隐式保存配置，操作前显式提示确认 */
function testHistory() {
  testHistoryOpen.value = true;
}

/** 历史数据源"保存并测试"确认回调（ClpmDangerConfirmModal @confirm） */
async function confirmTestHistory() {
  testingHistory.value = true;
  historyTestResult.value = null;
  try {
    const data = await updateDatasourceConfigApi(buildHistoryPayload());
    config.value = data;
    resetTokenState(data);
    historyTestResult.value = await testHistoryApiApi();
    testHistoryOpen.value = false;
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    testingHistory.value = false;
  }
}

/** 测试连接会先按当前表单隐式保存配置，操作前显式提示确认 */
function testSignalr() {
  testSignalrOpen.value = true;
}

/** 实时数据源"保存并测试"确认回调（ClpmDangerConfirmModal @confirm） */
async function confirmTestSignalr() {
  testingSignalr.value = true;
  signalrTestResult.value = null;
  try {
    const data = await updateDatasourceConfigApi({
      signalrHubUrl: form.signalrHubUrl,
      signalrEnabled: form.signalrEnabled,
      signalrReconnectInterval: form.signalrReconnectInterval,
    });
    config.value = data;
    signalrTestResult.value = await testSignalrApi();
    testSignalrOpen.value = false;
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    testingSignalr.value = false;
  }
}

// =========================================================================
// Tab 2: DCS 系统（品牌/型号管理）
// =========================================================================
const vendorsLoading = ref(false);
/** 品牌列表加载失败态（ClpmDataCanvas error + retry） */
const vendorsError = ref(false);
const vendors = ref<DcsApi.Vendor[]>([]);
const modelsLoading = ref(false);
/** 型号列表加载失败态（ClpmDataCanvas error + retry） */
const modelsError = ref(false);
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

// ===== 删除危险确认弹窗状态（ClpmDangerConfirmModal）=====
const vendorDeleteOpen = ref(false);
const vendorDeleteTarget = ref<DcsApi.Vendor | null>(null);
const vendorDeleteLoading = ref(false);

/** 删除型号目标（Tab 2 表格与 MODE 矩阵共用同一确认弹窗） */
interface ModelDeleteTarget {
  id: string;
  code: string;
  /** 是否从矩阵视图发起（决定删除后刷新范围） */
  fromMatrix: boolean;
}

const modelDeleteOpen = ref(false);
const modelDeleteTarget = ref<ModelDeleteTarget | null>(null);
const modelDeleteLoading = ref(false);

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
  vendorsError.value = false;
  try {
    vendors.value = await getVendorsApi();
  } catch {
    // 错误提示由请求拦截器统一处理，此处仅更新本地错误态供 ClpmDataCanvas 展示
    vendorsError.value = true;
  } finally {
    vendorsLoading.value = false;
  }
}

async function loadModels() {
  modelsLoading.value = true;
  modelsError.value = false;
  try {
    models.value = await getModelsApi();
  } catch {
    // 错误提示由请求拦截器统一处理，此处仅更新本地错误态供 ClpmDataCanvas 展示
    modelsError.value = true;
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
    if (matrix.value) await loadMatrixWithPid();
  } catch {
    // 错误提示由请求拦截器统一处理
  }
}

/** 打开删除品牌危险确认弹窗 */
function openVendorDelete(record: any) {
  vendorDeleteTarget.value = record;
  vendorDeleteOpen.value = true;
}

/** 删除品牌危险确认回调（ClpmDangerConfirmModal @confirm） */
async function confirmVendorDelete() {
  const record = vendorDeleteTarget.value;
  if (!record) return;
  vendorDeleteLoading.value = true;
  try {
    await deleteVendorApi(record.id);
    message.success('品牌已删除');
    vendorDeleteOpen.value = false;
    await loadVendors();
    await loadModels();
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    vendorDeleteLoading.value = false;
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
    if (matrix.value) await loadMatrixWithPid();
  } catch {
    // 错误提示由请求拦截器统一处理
  }
}

/** 打开删除型号危险确认弹窗（Tab 2 表格与 MODE 矩阵共用） */
function openModelDelete(target: ModelDeleteTarget) {
  modelDeleteTarget.value = target;
  modelDeleteOpen.value = true;
}

/** 删除型号危险确认回调（ClpmDangerConfirmModal @confirm） */
async function confirmModelDelete() {
  const target = modelDeleteTarget.value;
  if (!target) return;
  modelDeleteLoading.value = true;
  try {
    await deleteModelApi(target.id);
    message.success('型号已删除');
    modelDeleteOpen.value = false;
    if (target.fromMatrix) {
      await Promise.all([loadMatrixWithPid(), loadModels()]);
    } else {
      await loadModels();
    }
  } catch {
    // 错误提示由请求拦截器统一处理
  } finally {
    modelDeleteLoading.value = false;
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
      if (matrix.value) loadMatrixWithPid();
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
// Tab 3: DCS 型号映射（MODE 矩阵 + PID 结构合并）
// =========================================================================
const matrixLoading = ref(false);
/** MODE 矩阵加载失败态（ClpmDataCanvas error + retry） */
const matrixError = ref(false);
const matrix = ref<DcsApi.ModeMatrixView | null>(null);
const modeDefs = ref<DcsApi.ModeDefinition[]>([]);
const pidStructMap = ref<Map<string, DcsApi.PidStructure>>(new Map());

async function loadMatrixWithPid() {
  matrixLoading.value = true;
  matrixError.value = false;
  try {
    const [matrixData, defsData, pidData] = await Promise.all([
      getModeMatrixApi(),
      getModeDefinitionsApi(),
      getPidStructuresApi(),
    ]);
    matrix.value = matrixData;
    modeDefs.value = defsData;
    pidStructMap.value = new Map((pidData ?? []).map((s) => [s.dcsModelId, s]));
  } catch {
    // 错误提示由请求拦截器统一处理，此处仅更新本地错误态供 ClpmDataCanvas 展示
    matrixError.value = true;
  } finally {
    matrixLoading.value = false;
  }
}

// PID 抽屉状态
interface PidModelRow {
  id: string;
  code: string;
  name: string;
  vendorName?: null | string;
  structure?: DcsApi.PidStructure | null;
}

const pidDrawerOpen = ref(false);
const pidEditingModel = ref<null | PidModelRow>(null);

function openPidDrawer(record: any) {
  if (!record.modelId) return;
  pidEditingModel.value = {
    id: record.modelId,
    code: record.modelCode ?? '',
    name: record.modelName ?? '',
    vendorName: record.vendorName ?? '',
    structure: record.pidStructure ?? null,
  };
  pidDrawerOpen.value = true;
}

function onPidSaved(data: DcsApi.PidStructure) {
  pidStructMap.value.set(data.dcsModelId, data);
  pidStructMap.value = new Map(pidStructMap.value);
}

function onPidDeleted(modelId: string) {
  pidStructMap.value.delete(modelId);
  pidStructMap.value = new Map(pidStructMap.value);
}

/**
 * 转置后的矩阵列定义：品牌 | 型号名称 | MODE 0 | ... | MODE 4 | PID 结构 | 操作
 * - MODE 列标题显示"标准值 - 中文标签"
 * - PID 结构列显示紧凑 Tag（增益·秒/秒），点击打开编辑抽屉
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
  // PID 结构列（紧凑 Tag，点击打开编辑抽屉）
  cols.push(
    {
      title: 'PID 结构',
      key: 'pid',
      width: 110,
      align: 'center',
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      fixed: 'right',
    },
  );
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
      modelCode: col.modelCode ?? '',
      modelName: col.modelName ?? '本系统默认',
      vendorName: col.vendorName ?? '—',
      pidStructure: col.modelId
        ? (pidStructMap.value.get(col.modelId) ?? null)
        : null,
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
    await loadMatrixWithPid();
  } catch {
    // 错误提示由请求拦截器统一处理
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
    await loadMatrixWithPid();
  } catch {
    // 错误提示由请求拦截器统一处理
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
    if (!matrix.value) loadMatrixWithPid();
    if (vendors.value.length === 0) loadVendors();
  }
});

onMounted(loadConfig);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="数据接入"
      subtitle="数据源 · DCS 系统 · MODE 矩阵"
      compact
    />

    <Tabs v-model:active-key="activeTab" class="mt-4">
      <!-- Tab 1: 数据源 -->
      <TabPane key="datasource" tab="数据源">
        <ClpmDataCanvas
          :loading="loading"
          loading-variant="opacity"
          :error="configError"
          error-text="数据源配置加载失败，请重试"
          @retry="loadConfig"
        >
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
                  v-permission="['ADMIN']"
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
                  allow-clear
                />
              </FormItem>
              <FormItem label="鉴权 Token">
                <Input.Password
                  v-model:value="form.historyApiToken"
                  :disabled="clearToken"
                  :placeholder="
                    savedMaskedToken
                      ? `已保存：${savedMaskedToken}（留空保持不变）`
                      : '如需鉴权请填写'
                  "
                />
                <div v-if="savedMaskedToken" class="mt-1">
                  <Checkbox v-model:checked="clearToken">
                    清空已保存 Token
                  </Checkbox>
                </div>
                <div class="text-gray-400 mt-1 text-xs">
                  Token 打码显示（保留前后各 4 位）；留空 = 不变，输入新值 =
                  覆盖，勾选清空 = 清除
                </div>
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
                TDengine，不调用此接口。地址清空后保存即清除配置。
              </div>

              <div class="flex items-center gap-3">
                <Button
                  v-permission="['ADMIN']"
                  type="primary"
                  :loading="savingHistory"
                  @click="saveHistoryConfig"
                >
                  保存配置
                </Button>
                <Button
                  v-permission="['ADMIN']"
                  :loading="testingHistory"
                  @click="testHistory"
                >
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
                    allow-clear
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
                  v-permission="['ADMIN']"
                  type="primary"
                  :loading="savingSignalr"
                  @click="saveSignalrConfig"
                >
                  保存配置
                </Button>
                <Button
                  v-if="form.signalrEnabled"
                  v-permission="['ADMIN']"
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
        </ClpmDataCanvas>
      </TabPane>

      <!-- Tab 2: DCS 系统 -->
      <TabPane key="dcs" tab="DCS 系统">
        <!-- 品牌管理 -->
        <ClpmDataCanvas
          class="mb-4"
          title="DCS 品牌"
          :error="vendorsError"
          error-text="品牌列表加载失败，请重试"
          @retry="loadVendors"
        >
          <template #extra>
            <Button
              v-permission="['ADMIN']"
              type="primary"
              size="small"
              @click="openVendorModal()"
            >
              新增品牌
            </Button>
            <Upload v-bind="vendorUploadProps">
              <Button
                v-permission="['ADMIN']"
                size="small"
                :loading="vendorImporting"
              >
                导入
              </Button>
            </Upload>
            <Button
              size="small"
              :loading="vendorExporting"
              @click="handleExportVendors"
            >
              导出
            </Button>
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
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  @click="openVendorModal(record)"
                  >编辑</Button
                >
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  danger
                  @click="openVendorDelete(record)"
                  >删除</Button
                >
              </template>
            </template>
          </Table>
        </ClpmDataCanvas>

        <!-- 型号管理 -->
        <ClpmDataCanvas
          title="DCS 型号"
          :error="modelsError"
          error-text="型号列表加载失败，请重试"
          @retry="loadModels"
        >
          <template #extra>
            <Button
              v-permission="['ADMIN']"
              type="primary"
              size="small"
              @click="openModelModal()"
              >新增型号</Button
            >
            <Upload v-bind="modelUploadProps">
              <Button
                v-permission="['ADMIN']"
                size="small"
                :loading="modelImporting"
                >导入</Button
              >
            </Upload>
            <Button
              size="small"
              :loading="modelExporting"
              @click="handleExportModels"
            >
              导出
            </Button>
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
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  @click="openModelModal(record)"
                  >编辑</Button
                >
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  danger
                  @click="
                    openModelDelete({
                      id: record.id,
                      code: record.code,
                      fromMatrix: false,
                    })
                  "
                  >删除</Button
                >
              </template>
            </template>
          </Table>
        </ClpmDataCanvas>
      </TabPane>

      <!-- Tab 3: DCS 型号映射（MODE 矩阵 + PID 结构合并） -->
      <TabPane key="matrix" tab="DCS 型号映射">
        <ClpmDataCanvas
          :loading="matrixLoading"
          loading-variant="opacity"
          :error="matrixError"
          error-text="MODE 矩阵加载失败，请重试"
          @retry="loadMatrixWithPid"
        >
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
                    v-permission="['ADMIN']"
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
              <span>DCS 型号映射矩阵</span>
            </template>
            <template #extra>
              <div class="flex items-center gap-2">
                <Button
                  v-permission="['ADMIN']"
                  size="small"
                  @click="openVendorModal()"
                >
                  + 新增品牌
                </Button>
                <Button
                  v-permission="['ADMIN']"
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
              message="第一行为本系统默认 MODE 映射；后续行为各 DCS 型号。点击 MODE 单元格编辑映射值，点击 PID 状态 Tag 编辑 PID 结构。"
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
                <!-- PID 结构列：紧凑 Tag，点击打开编辑抽屉 -->
                <template v-else-if="column.key === 'pid'">
                  <Tag
                    v-if="record.modelId && record.pidStructure"
                    :color="
                      record.pidStructure.dFilterEnabled ? 'blue' : 'green'
                    "
                    class="cursor-pointer"
                    @click="openPidDrawer(record)"
                  >
                    {{
                      `${
                        record.pidStructure.pType === 'PROPORTION'
                          ? '增益'
                          : '比例度'
                      }·${
                        record.pidStructure.iUnit === 'SECONDS' ? '秒' : '分'
                      }/${
                        record.pidStructure.dUnit === 'SECONDS' ? '秒' : '分'
                      }`
                    }}
                  </Tag>
                  <Tag
                    v-else-if="record.modelId"
                    color="default"
                    class="cursor-pointer"
                    @click="openPidDrawer(record)"
                  >
                    默认
                  </Tag>
                  <span v-else class="text-xs text-gray-300">—</span>
                </template>
                <!-- 操作列：删除型号（本系统默认行不显示） -->
                <template v-else-if="column.key === 'action'">
                  <Button
                    v-if="record.modelId"
                    v-permission="['ADMIN']"
                    type="link"
                    size="small"
                    danger
                    @click="
                      openModelDelete({
                        id: record.modelId,
                        code: record.modelCode,
                        fromMatrix: true,
                      })
                    "
                    >删除</Button
                  >
                  <span v-else class="text-xs text-gray-300">默认</span>
                </template>
              </template>
            </Table>
          </Card>
        </ClpmDataCanvas>
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

    <!-- 网络模式切换：危险确认弹窗（瞬断实时链路，轻量确认） -->
    <ClpmDangerConfirmModal
      v-model:open="networkSwitchOpen"
      title="切换网络模式"
      action="切换"
      :target="networkSwitchTargetLabel"
      impact-scope="切换瞬间会中断实时数据链路（SignalR 订阅将自动重连并补数）；仅切换网络链路、不影响数据源选择"
      rollback-tip="可随时切换回原模式"
      confirm-text="确认切换"
      :require-confirm-code="false"
      :require-reason="false"
      :show-audit-note="false"
      :loading="switchingNetwork"
      @confirm="confirmNetworkSwitch"
    />

    <!-- 测试历史数据源连接：先隐式保存配置，操作前显式确认 -->
    <ClpmDangerConfirmModal
      v-model:open="testHistoryOpen"
      title="测试历史数据源连接"
      action="保存并测试"
      impact-scope="将先按当前表单内容保存历史数据源配置，再发起连接测试"
      confirm-text="保存并测试"
      :require-confirm-code="false"
      :require-reason="false"
      :show-audit-note="false"
      :loading="testingHistory"
      @confirm="confirmTestHistory"
    />

    <!-- 测试实时数据源连接：先隐式保存配置，操作前显式确认 -->
    <ClpmDangerConfirmModal
      v-model:open="testSignalrOpen"
      title="测试实时数据源连接"
      action="保存并测试"
      impact-scope="将先按当前表单内容保存实时数据源配置，再发起连接测试"
      confirm-text="保存并测试"
      :require-confirm-code="false"
      :require-reason="false"
      :show-audit-note="false"
      :loading="testingSignalr"
      @confirm="confirmTestSignalr"
    />

    <!-- 删除 DCS 品牌：危险确认弹窗 -->
    <ClpmDangerConfirmModal
      v-model:open="vendorDeleteOpen"
      title="删除 DCS 品牌"
      action="删除"
      :target="vendorDeleteTarget?.code ?? ''"
      impact-scope="将删除该品牌；存在关联型号时后端将拒绝删除"
      rollback-tip="此操作不可逆，删除后无法恢复"
      require-confirm-code
      confirm-code-placeholder="请输入品牌代码以确认"
      :loading="vendorDeleteLoading"
      @confirm="confirmVendorDelete"
    />

    <!-- 删除 DCS 型号：危险确认弹窗（Tab 2 表格与 MODE 矩阵共用） -->
    <ClpmDangerConfirmModal
      v-model:open="modelDeleteOpen"
      title="删除 DCS 型号"
      action="删除"
      :target="modelDeleteTarget?.code ?? ''"
      impact-scope="将删除该型号，关联回路的 dcs_model_id 将置空"
      rollback-tip="此操作不可逆，删除后无法恢复"
      require-confirm-code
      confirm-code-placeholder="请输入型号代码以确认"
      :loading="modelDeleteLoading"
      @confirm="confirmModelDelete"
    />

    <!-- PID 结构编辑抽屉 -->
    <PidStructureDrawer
      v-model:open="pidDrawerOpen"
      :model="pidEditingModel"
      @success="onPidSaved"
      @deleted="onPidDeleted"
    />
  </Page>
</template>
