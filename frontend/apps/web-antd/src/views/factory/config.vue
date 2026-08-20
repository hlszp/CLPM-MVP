<script lang="ts" setup>
/**
 * 工厂配置页（列表 + 树形结构）
 *
 * - 左侧：工厂模型树（工厂 → 装置 → 单元，选择联动右侧列表筛选）
 * - 右侧：节点分页列表（层级路径/父节点/来源标记/参评/更新时间 + CRUD）
 * - 导入/导出（Excel）：导出全部层级（父先子后）；导入逐行 upsert，
 *   完成后反馈新增/更新/失败明细（导入仅 ADMIN）
 * - AAS 工厂模型同步（独立同步配置区）：同步设置（连接配置/启停）+
 *   连接测试 + 全量同步 + 同步日志；source_node_id 标记 AAS 同步节点
 *   （本地改名会被下次同步覆盖，主数据语义）
 */
import type {
  TableColumnsType,
  TablePaginationConfig,
  UploadProps,
} from 'ant-design-vue';

import type { PlantNodeApi } from '#/api/plant-node';
import type {
  FactorySyncLog,
  FactorySyncSetting,
  PlantNodeListItem,
} from '#/api/plant-node';

import { computed, h, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Form,
  FormItem,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Select,
  Switch,
  Table,
  Tag,
  Tooltip,
  Upload,
} from 'ant-design-vue';

import {
  createPlantNodeApi,
  deletePlantNodeApi,
  exportPlantNodesApi,
  getFactorySyncLogsApi,
  getFactorySyncSettingApi,
  getPlantNodeListApi,
  getPlantNodeTreeApi,
  importPlantNodesApi,
  saveFactorySyncSettingApi,
  syncFactoryModelApi,
  testFactorySyncApi,
  updatePlantNodeApi,
} from '#/api/plant-node';
import {
  ClpmHelpIcon,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'FactoryConfig' });

const { themeColors } = useClpmTheme();

// ===== 常量 =====

const TYPE_TEXT: Record<string, string> = {
  FACTORY: '工厂',
  AREA: '装置',
  UNIT: '单元',
};

function typeText(t: string): string {
  return TYPE_TEXT[t] ?? t;
}

const HELP_CONTENT = [
  '工厂配置页：工厂-装置-单元三层结构的定义与管理（左侧树 + 右侧列表），回路挂在单元节点下。',
  '· 层级约束：工厂为根节点，装置挂在工厂下，单元挂在装置下；存在子节点或关联回路的节点不可删除。',
  '· 导入/导出（Excel）：导出全部层级（列：节点名称/节点类型/父节点名称/层级路径，父先子后）；导入为逐行 upsert（名称+父节点已存在则更新，否则新建），可先导出作为模板修改后回灌；导入仅 ADMIN。',
  '· AAS 工厂模型同步（独立同步配置区）：从 AAS 高级过程报警系统拉取区域节点（AreaNode）全量数据，按来源标记（source_node_id）upsert——AAS 同步节点的名称/层级以 AAS 为准（本地改名会被下次同步覆盖）；本地维护节点不受同步影响。',
  '· 连接协议：ABP 动态 API（登录 TokenAuth + 区域节点分页接口），默认地址可在同步设置中调整。',
  '· 同步需先在「同步设置」中启用并配置账号；「立即同步」为手动全量触发，同步结果可在「同步日志」中查看。',
].join('\n');

// ===== 左侧树 =====

const treeLoading = ref(false);
const treeData = ref<PlantNodeApi.PlantNode[]>([]);
const selectedNode = ref<null | PlantNodeApi.PlantNode>(null);

async function loadTree() {
  treeLoading.value = true;
  try {
    treeData.value = await getPlantNodeTreeApi();
  } catch {
    // 错误已由拦截器处理
  } finally {
    treeLoading.value = false;
  }
}

function handleTreeSelect(node: null | PlantNodeApi.PlantNode) {
  selectedNode.value = node;
  // 联动右侧列表：选中节点 → 按名称过滤（同词根）
  query.keyword = node ? node.name : undefined;
  query.page = 1;
  loadList();
}

// ===== 右侧列表 =====

const loading = ref(false);
const list = ref<PlantNodeListItem[]>([]);
const total = ref(0);
const query = reactive({
  keyword: undefined as string | undefined,
  nodeType: undefined as PlantNodeApi.NodeType | undefined,
  source: undefined as 'aas' | 'local' | undefined,
  page: 1,
  pageSize: 20,
});

const columns: TableColumnsType = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 160 },
  {
    title: '类型',
    dataIndex: 'type',
    key: 'type',
    width: 90,
    customRender: ({ value }) => typeText(value),
  },
  {
    title: '层级路径',
    dataIndex: 'path',
    key: 'path',
    ellipsis: true,
  },
  {
    title: '来源',
    key: 'source',
    width: 100,
  },
  {
    title: '参评',
    dataIndex: 'isKpiEnabled',
    key: 'isKpiEnabled',
    width: 80,
  },
  {
    title: '更新时间',
    dataIndex: 'updatedAt',
    key: 'updatedAt',
    width: 150,
    customRender: ({ value }) => (value ? formatTime(value) : '—'),
  },
  { title: '操作', key: 'action', width: 130, fixed: 'right' },
];

async function loadList() {
  loading.value = true;
  try {
    const res = await getPlantNodeListApi({
      keyword: query.keyword,
      nodeType: query.nodeType,
      source: query.source,
      page: query.page,
      pageSize: query.pageSize,
    });
    list.value = res.items ?? [];
    total.value = res.total ?? 0;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handlePageChange(pag: TablePaginationConfig) {
  query.page = pag.current ?? 1;
  query.pageSize = pag.pageSize ?? 20;
  loadList();
}

// ===== 新增 / 编辑节点 =====

const modalVisible = ref(false);
const modalMode = ref<'create' | 'edit'>('create');
const saving = ref(false);
const form = reactive({
  id: '',
  name: '',
  type: 'FACTORY' as PlantNodeApi.NodeType,
  parentId: '' as string,
});

/** 树扁平化为父节点选项（显示完整路径） */
const parentOptions = computed(() => {
  const nodeMap = new Map<string, PlantNodeApi.PlantNode>();
  for (const n of treeData.value) nodeMap.set(n.id, n);
  const options: Array<{ label: string; value: string }> = [];
  const walk = (nodes: PlantNodeApi.PlantNode[], depth: number) => {
    for (const n of nodes) {
      const suffix = `（${typeText(n.type)}）`;
      const indent = '　'.repeat(depth);
      options.push({ label: `${indent}${n.name}${suffix}`, value: n.id });
      if (n.children?.length) walk(n.children, depth + 1);
    }
  };
  walk(treeData.value, 0);
  return options;
});

function openCreateModal() {
  modalMode.value = 'create';
  form.id = '';
  form.name = '';
  form.type = 'FACTORY';
  form.parentId = selectedNode.value?.id ?? '';
  modalVisible.value = true;
}

function openEditModal(record: PlantNodeListItem) {
  modalMode.value = 'edit';
  form.id = record.id;
  form.name = record.name;
  form.type = record.type;
  form.parentId = record.parentId ?? '';
  modalVisible.value = true;
}

async function handleSave() {
  if (!form.name.trim()) {
    message.warning('节点名称不可为空');
    return;
  }
  saving.value = true;
  try {
    if (modalMode.value === 'create') {
      await createPlantNodeApi({
        name: form.name.trim(),
        type: form.type,
        parentId: form.parentId || null,
      });
      message.success('节点已创建');
    } else {
      await updatePlantNodeApi(form.id, { name: form.name.trim() });
      message.success('节点已更新');
    }
    modalVisible.value = false;
    await Promise.all([loadTree(), loadList()]);
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

async function handleDelete(record: PlantNodeListItem) {
  try {
    await deletePlantNodeApi(record.id);
    message.success('节点已删除');
    await Promise.all([loadTree(), loadList()]);
  } catch {
    // 错误已由拦截器处理（含子节点/回路保护提示）
  }
}

// ===== 导入 / 导出（Excel） =====

const exporting = ref(false);

/** 导出工厂层级 Excel（列：节点名称/节点类型/父节点名称/层级路径，父先子后） */
async function handleExport() {
  exporting.value = true;
  try {
    const blob = await exportPlantNodesApi();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const filename = `工厂模型_${new Date().toISOString().slice(0, 10)}.xlsx`;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success(`已导出 ${filename}`);
  } catch {
    // 错误已由拦截器处理
  } finally {
    exporting.value = false;
  }
}

const importing = ref(false);

/**
 * 导入工厂层级 Excel（Upload beforeUpload 钩子，仅 ADMIN）
 *
 * 逐行 upsert：节点名称 + 父节点已存在则更新，否则新建；
 * 导入完成反馈明细（upsert 语义下列表可能无可见变化，必须反馈新增/更新/失败计数）。
 */
function handleImportBeforeUpload(file: File): boolean {
  importing.value = true;
  importPlantNodesApi(file)
    .then((result) => {
      const summary = `共 ${result.total} 行：新增 ${result.inserted} 个，更新 ${result.updated} 个，失败 ${result.failed} 个`;
      if (result.failed > 0 && result.errors.length > 0) {
        Modal.error({
          title: '导入完成（部分行失败）',
          width: 520,
          content: h('div', null, [
            h('p', null, summary),
            h(
              'ul',
              {
                style: {
                  'max-height': '220px',
                  overflow: 'auto',
                  'padding-left': '20px',
                },
              },
              result.errors.slice(0, 20).map((e) =>
                h(
                  'li',
                  null,
                  `第 ${e.row} 行${e.name ? `（${e.name}）` : ''}：${e.message}`,
                ),
              ),
            ),
            result.errors.length > 20
              ? h(
                  'p',
                  { style: { color: '#888' } },
                  `… 其余 ${result.errors.length - 20} 条错误省略`,
                )
              : null,
          ]),
        });
      } else {
        message.success(`导入完成：${summary}`);
      }
      // 刷新树 + 列表（导入可能新建/更新节点）
      void Promise.all([loadTree(), loadList()]);
    })
    .catch(() => {
      // 错误已由拦截器处理
    })
    .finally(() => {
      importing.value = false;
    });
  // 返回 false 阻止 Upload 组件默认上传行为
  return false;
}

const uploadAccept = '.xlsx,.xls';

const uploadProps: UploadProps = {
  accept: uploadAccept,
  showUploadList: false,
  beforeUpload: handleImportBeforeUpload as UploadProps['beforeUpload'],
};

// ===== AAS 同步 =====

const setting = ref<FactorySyncSetting | null>(null);

async function loadSetting() {
  try {
    setting.value = await getFactorySyncSettingApi();
  } catch {
    // 非管理员静默
  }
}

/** 同步设置弹窗 */
const settingOpen = ref(false);
const settingSaving = ref(false);
const testing = ref(false);
const settingForm = reactive({
  baseUrl: '',
  authApiPath: '/api/TokenAuth/Authenticate',
  nodesApiPath: '/api/services/v1/AreaNode/GetAllPagedAndSorted',
  userName: '',
  password: '',
  isEnabled: false,
  pageBatchSize: 500,
});

function openSettingModal() {
  const s = setting.value;
  settingForm.baseUrl = s?.baseUrl ?? '';
  settingForm.authApiPath = s?.authApiPath ?? '/api/TokenAuth/Authenticate';
  settingForm.nodesApiPath =
    s?.nodesApiPath ?? '/api/services/v1/AreaNode/GetAllPagedAndSorted';
  settingForm.userName = s?.userName ?? '';
  settingForm.password = '';
  settingForm.isEnabled = s?.isEnabled ?? false;
  settingForm.pageBatchSize = s?.pageBatchSize ?? 500;
  settingOpen.value = true;
}

async function saveSetting() {
  if (!settingForm.baseUrl.trim() || !settingForm.userName.trim()) {
    message.warning('AAS 地址与账号不可为空');
    return;
  }
  settingSaving.value = true;
  try {
    setting.value = await saveFactorySyncSettingApi({
      baseUrl: settingForm.baseUrl.trim(),
      authApiPath: settingForm.authApiPath.trim(),
      nodesApiPath: settingForm.nodesApiPath.trim(),
      userName: settingForm.userName.trim(),
      password: settingForm.password || undefined,
      isEnabled: settingForm.isEnabled,
      pageBatchSize: settingForm.pageBatchSize,
    });
    message.success('同步配置已保存（运行时生效）');
    settingOpen.value = false;
  } catch {
    // 错误已由拦截器处理
  } finally {
    settingSaving.value = false;
  }
}

async function testConnection() {
  // 先保存当前表单（保证测试用最新配置），再测试
  if (!settingForm.baseUrl.trim() || !settingForm.userName.trim()) {
    message.warning('请先填写 AAS 地址与账号');
    return;
  }
  testing.value = true;
  try {
    await saveFactorySyncSettingApi({
      baseUrl: settingForm.baseUrl.trim(),
      authApiPath: settingForm.authApiPath.trim(),
      nodesApiPath: settingForm.nodesApiPath.trim(),
      userName: settingForm.userName.trim(),
      password: settingForm.password || undefined,
      isEnabled: settingForm.isEnabled,
      pageBatchSize: settingForm.pageBatchSize,
    });
    const result = await testFactorySyncApi();
    if (result.success) {
      message.success(`连接成功（${result.latencyMs}ms）`);
    } else {
      message.error(`连接失败：${result.message}`);
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    testing.value = false;
  }
}

/** 立即同步 */
const syncing = ref(false);

async function handleSync() {
  if (setting.value && !setting.value.isEnabled) {
    message.warning('同步未启用，请先在「同步设置」中开启');
    openSettingModal();
    return;
  }
  syncing.value = true;
  try {
    const result = await syncFactoryModelApi();
    if (result.status === 'success') {
      message.success(
        `同步完成：共 ${result.nodesTotal} 节点，新增 ${result.created}，更新 ${result.updated}`,
      );
      await Promise.all([loadTree(), loadList(), loadSetting()]);
    } else {
      message.error(`同步失败：${result.message}`);
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    syncing.value = false;
  }
}

/** 同步日志弹窗 */
const logsOpen = ref(false);
const logsLoading = ref(false);
const logs = ref<FactorySyncLog[]>([]);

async function openLogs() {
  logsOpen.value = true;
  logsLoading.value = true;
  try {
    logs.value = await getFactorySyncLogsApi(20);
  } catch {
    logs.value = [];
  } finally {
    logsLoading.value = false;
  }
}

// ===== 工具栏 =====

async function handleRefresh() {
  await Promise.all([loadTree(), loadList(), loadSetting()]);
}

function handleHelp() {
  showPageHelp({ title: '工厂配置 帮助', content: HELP_CONTENT });
}

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value || treeLoading.value },
  help: { onClick: handleHelp },
}));

/** 同步日志表列 */
const logColumns: TableColumnsType = [
  { title: '时间', key: 'startTime', width: 150 },
  { title: '状态', key: 'status', width: 80 },
  { title: '结果摘要', key: 'summary', ellipsis: true },
  { title: '操作人', dataIndex: 'operatorName', key: 'operatorName', width: 90 },
];

onMounted(() => {
  void loadTree();
  void loadList();
  void loadSetting();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="工厂配置"
      subtitle="工厂-装置-单元定义（列表 + 树形结构）"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <div class="mt-4 flex gap-4">
      <!-- 左侧：工厂模型树 -->
      <div class="w-72 shrink-0">
        <PlantNodeTree
          :loading="treeLoading"
          card-title="工厂模型"
          max-height="calc(100vh - 320px)"
          @select="handleTreeSelect"
          @load-complete="(data: PlantNodeApi.PlantNode[]) => (treeData = data)"
        />
      </div>

      <!-- 右侧：节点列表 + 同步区 -->
      <div class="min-w-0 flex-1">
        <!-- 同步配置区（独立同步配置） -->
        <div
          class="mb-3 flex flex-wrap items-center justify-between gap-2 rounded p-3"
          :style="{
            border: '1px solid hsl(var(--border))',
            background: 'hsl(var(--muted) / 42%)',
          }"
        >
          <div class="flex items-center text-sm" :style="{ color: themeColors.NEUTRAL }">
            <span>
              AAS 工厂模型同步（独立配置）：
              <template v-if="setting">
                {{ setting.isEnabled ? '已启用' : '未启用' }}
                <template v-if="setting.lastSyncAt">
                  · 最近同步 {{ formatTime(setting.lastSyncAt) }}
                  <template v-if="setting.lastSyncSummary">
                    （{{ setting.lastSyncSummary }}）
                  </template>
                </template>
              </template>
              <template v-else>未配置</template>
            </span>
            <ClpmHelpIcon title="工厂配置 帮助" :content="HELP_CONTENT" />
          </div>
          <div class="flex items-center gap-2">
            <Button size="small" @click="openSettingModal"> 同步设置 </Button>
            <Button size="small" @click="openLogs"> 同步日志 </Button>
            <Button
              v-permission="['ADMIN']"
              size="small"
              type="primary"
              :loading="syncing"
              @click="handleSync"
            >
              立即同步
            </Button>
          </div>
        </div>

        <!-- 筛选 + 新增 -->
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <Input
            v-model:value="query.keyword"
            placeholder="按名称搜索"
            style="width: 180px"
            allow-clear
            @press-enter="handleSearch"
          />
          <Select
            v-model:value="query.nodeType"
            allow-clear
            placeholder="类型"
            style="width: 110px"
            :options="[
              { value: 'FACTORY', label: '工厂' },
              { value: 'AREA', label: '装置' },
              { value: 'UNIT', label: '单元' },
            ]"
            @change="handleSearch"
          />
          <Select
            v-model:value="query.source"
            allow-clear
            placeholder="来源"
            style="width: 120px"
            :options="[
              { value: 'aas', label: 'AAS 同步' },
              { value: 'local', label: '本地维护' },
            ]"
            @change="handleSearch"
          />
          <Button @click="handleSearch">查询</Button>
          <div class="!ml-auto flex items-center gap-2">
            <Upload v-bind="uploadProps">
              <Button
                v-permission="['ADMIN']"
                :loading="importing"
              >
                导入
              </Button>
            </Upload>
            <Button :loading="exporting" @click="handleExport"> 导出 </Button>
            <Button
              v-permission="['ADMIN']"
              type="primary"
              @click="openCreateModal"
            >
              新增节点
            </Button>
          </div>
        </div>

        <!-- 节点列表 -->
        <Table
          :columns="columns"
          :data-source="list"
          :loading="loading"
          :pagination="{
            current: query.page,
            pageSize: query.pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t: number) => `共 ${t} 个节点`,
          }"
          :scroll="{ x: 1000 }"
          row-key="id"
          size="middle"
          @change="handlePageChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'source'">
              <Tag v-if="record.sourceNodeId" color="blue"> AAS 同步 </Tag>
              <Tag v-else> 本地 </Tag>
            </template>
            <template v-else-if="column.key === 'isKpiEnabled'">
              <Tag :color="record.isKpiEnabled ? 'green' : 'default'">
                {{ record.isKpiEnabled ? '参评' : '—' }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'action'">
              <div class="flex items-center gap-1">
                <Button
                  v-permission="['ADMIN']"
                  type="link"
                  size="small"
                  @click="openEditModal(record as PlantNodeListItem)"
                >
                  编辑
                </Button>
                <Tooltip
                  :title="
                    record.sourceNodeId
                      ? 'AAS 同步节点：删除后会被下次同步恢复，建议在 AAS 侧调整'
                      : ''
                  "
                >
                  <Popconfirm
                    :title="`确认删除节点「${record.name}」？存在子节点或关联回路时将拒绝删除。`"
                    @confirm="handleDelete(record as PlantNodeListItem)"
                  >
                    <Button
                      v-permission="['ADMIN']"
                      type="link"
                      size="small"
                      danger
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </Tooltip>
              </div>
            </template>
          </template>
        </Table>
      </div>
    </div>

    <!-- 新增 / 编辑节点 -->
    <Modal
      v-model:open="modalVisible"
      :title="modalMode === 'create' ? '新增工厂节点' : '编辑工厂节点'"
      :confirm-loading="saving"
      width="520px"
      @ok="handleSave"
    >
      <Form layout="vertical" class="pt-4">
        <FormItem label="节点名称" required>
          <Input
            v-model:value="form.name"
            placeholder="节点名称"
            :maxlength="100"
          />
        </FormItem>
        <FormItem label="节点类型">
          <Select
            v-model:value="form.type"
            :disabled="modalMode === 'edit'"
            :options="[
              { value: 'FACTORY', label: '工厂（顶层）' },
              { value: 'AREA', label: '装置' },
              { value: 'UNIT', label: '单元（挂回路）' },
            ]"
            placeholder="选择节点类型"
          />
        </FormItem>
        <FormItem label="父节点">
          <Select
            v-model:value="form.parentId"
            :disabled="modalMode === 'edit'"
            allow-clear
            show-search
            :options="parentOptions"
            placeholder="不选则为顶层节点（工厂）"
          />
        </FormItem>
        <div v-if="modalMode === 'edit'" class="text-xs" :style="{ color: themeColors.NEUTRAL }">
          编辑仅支持修改名称；类型与父级调整请删除后重建（存在子节点/回路时不可删除）。
        </div>
      </Form>
    </Modal>

    <!-- 同步设置 -->
    <Modal
      v-model:open="settingOpen"
      title="AAS 工厂模型同步设置"
      :confirm-loading="settingSaving"
      width="560px"
      ok-text="保存"
      cancel-text="取消"
      @ok="saveSetting"
    >
      <Form layout="vertical" class="pt-4">
        <FormItem label="AAS 地址（BaseUrl）" required>
          <Input
            v-model:value="settingForm.baseUrl"
            placeholder="如 http://192.168.100.2:81"
          />
        </FormItem>
        <FormItem label="登录接口路径">
          <Input v-model:value="settingForm.authApiPath" />
        </FormItem>
        <FormItem label="区域节点接口路径">
          <Input v-model:value="settingForm.nodesApiPath" />
        </FormItem>
        <FormItem label="账号" required>
          <Input v-model:value="settingForm.userName" />
        </FormItem>
        <FormItem :label="`密码${setting?.hasPassword ? '（已配置，留空=保留原密码）' : ''}`">
          <Input.Password
            v-model:value="settingForm.password"
            placeholder="AAS 账号密码"
          />
        </FormItem>
        <FormItem label="启用同步">
          <Switch v-model:checked="settingForm.isEnabled" />
          <span class="ml-2 text-xs" :style="{ color: themeColors.NEUTRAL }">
            关闭后「立即同步」将被拒绝
          </span>
        </FormItem>
        <FormItem label="分页批量大小">
          <InputNumber
            v-model:value="settingForm.pageBatchSize"
            :min="1"
            :max="2000"
            style="width: 160px"
          />
        </FormItem>
        <div class="flex justify-end">
          <Button :loading="testing" @click="testConnection">
            测试连接（先保存当前配置）
          </Button>
        </div>
      </Form>
    </Modal>

    <!-- 同步日志 -->
    <Modal
      v-model:open="logsOpen"
      title="工厂模型同步日志"
      :footer="null"
      width="720px"
    >
      <Table
        :columns="logColumns"
        :data-source="logs"
        :loading="logsLoading"
        :pagination="false"
        row-key="id"
        size="small"
        :scroll="{ y: 360 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'startTime'">
            <span class="text-xs">
              {{ formatTime(record.startTime) }}
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag :color="record.status === 'success' ? 'green' : 'red'">
              {{ record.status === 'success' ? '成功' : '失败' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'summary'">
            <span class="text-xs">
              共 {{ record.nodesTotal }}，新增 {{ record.nodesCreated }}，更新
              {{ record.nodesUpdated }}（{{ record.durationMs }}ms）
              <template v-if="record.errorMessage">
                · {{ record.errorMessage }}
              </template>
            </span>
          </template>
        </template>
      </Table>
    </Modal>
  </Page>
</template>
