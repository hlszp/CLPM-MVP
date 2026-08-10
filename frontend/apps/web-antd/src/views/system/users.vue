<script lang="ts" setup>
/**
 * S5-SYS-004 用户管理页
 *
 * 对齐 IDS v3.2 §2.6 + PRD §4.6 + UI/UX v4.1 §6.6.1
 * - 表格展示用户列表（用户名/姓名/角色/邮箱/状态/操作）
 * - 新增/编辑用户弹窗表单
 * - 重置密码操作
 * - 禁用用户二次确认
 * - 仅 ADMIN 可见
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { ClpmRole } from '#/api/auth';
import type { SystemApi } from '#/api/system';

import { computed, h, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Badge,
  Button,
  Dropdown,
  Form,
  FormItem,
  Input,
  Menu,
  message,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';

import { CLPM_ROLES, ROLE_LABELS } from '#/api/auth';
import {
  createUserApi,
  deleteUserApi,
  getUserListApi,
  resetUserPasswordApi,
  updateUserApi,
} from '#/api/system';
import {
  ClpmDangerConfirmModal,
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { ClpmEmptyState } from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { statusTokenToAntdColor } from '#/constants/clpm-ui';
import { exportData } from '#/utils/export';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'SystemUsers' });

// ===== A-07：表格密度三档（紧凑/标准/宽松，持久化）=====
const { tableSize, densityLabel, cycleDensity } =
  useTableDensity('system-users');

const loading = ref(false);
const userList = ref<SystemApi.User[]>([]);
const total = ref(0);

const query = reactive({
  username: '' as string,
  role: undefined as ClpmRole | undefined,
  isActive: undefined as string | undefined,
  page: 1,
  pageSize: 20,
});

// ===== P2-07：批量操作（行多选 + 批量禁用/启用）=====
const selectedRowKeys = ref<string[]>([]);
const batchLoading = ref(false);

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys: (number | string)[]) => {
    selectedRowKeys.value = keys as string[];
  },
}));

/** 已选用户中可禁用的（当前启用）数量 */
const selectedDisableCount = computed(
  () =>
    userList.value.filter(
      (u) => selectedRowKeys.value.includes(u.id) && u.isActive,
    ).length,
);
/** 已选用户中可启用的（当前禁用）数量 */
const selectedEnableCount = computed(
  () =>
    userList.value.filter(
      (u) => selectedRowKeys.value.includes(u.id) && !u.isActive,
    ).length,
);

/** 角色选项 */
const roleOptions = CLPM_ROLES.map((r) => ({
  label: ROLE_LABELS[r],
  value: r,
}));

/** 状态选项（使用字符串值避免 Select 类型问题） */
const statusOptions = [
  { label: '启用', value: 'true' },
  { label: '禁用', value: 'false' },
];

const columns: TableColumnsType = [
  {
    title: '用户名',
    dataIndex: 'username',
    key: 'username',
    width: 130,
  },
  {
    title: '姓名',
    dataIndex: 'displayName',
    key: 'displayName',
    width: 130,
  },
  {
    title: '角色',
    dataIndex: 'role',
    key: 'role',
    width: 140,
  },
  {
    title: '邮箱',
    dataIndex: 'email',
    key: 'email',
    width: 200,
    ellipsis: true,
  },
  {
    title: '状态',
    dataIndex: 'isActive',
    key: 'isActive',
    width: 90,
    align: 'center',
    customRender: ({ record }: { record: SystemApi.User }) =>
      h(
        Tag,
        { color: statusTokenToAntdColor(record.isActive ? 'ok' : 'error') },
        () => (record.isActive ? '启用' : '禁用'),
      ),
  },
  {
    title: '最后登录',
    dataIndex: 'lastLoginAt',
    key: 'lastLoginAt',
    width: 170,
  },
  {
    title: '创建时间',
    dataIndex: 'createdAt',
    key: 'createdAt',
    width: 170,
  },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
];

// 新增/编辑 Modal
const modalVisible = ref(false);
const modalLoading = ref(false);
const editingUser = ref<null | SystemApi.User>(null);
const formRef = ref();
const formState = reactive({
  username: '',
  password: '',
  displayName: '',
  role: 'IC_ENGINEER' as ClpmRole,
  email: '',
});

// 重置密码 Modal
const resetModalVisible = ref(false);
const resetModalLoading = ref(false);
const resetTarget = ref<null | SystemApi.User>(null);
const resetForm = reactive({
  newPassword: '',
});

/** 加载用户列表 */
async function loadList() {
  loading.value = true;
  try {
    let isActiveParam: boolean | undefined;
    if (query.isActive === 'true') {
      isActiveParam = true;
    } else if (query.isActive === 'false') {
      isActiveParam = false;
    }
    const data = await getUserListApi({
      page: query.page,
      pageSize: query.pageSize,
      keyword: query.username || undefined,
      role: query.role,
      isActive: isActiveParam,
    });
    userList.value = data.items || [];
    total.value = data.total || 0;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  selectedRowKeys.value = []; // P2-07：筛选时清空选择，避免跨页残留
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

/** 打开新增弹窗 */
function handleOpenAdd() {
  editingUser.value = null;
  formState.username = '';
  formState.password = '';
  formState.displayName = '';
  formState.role = 'IC_ENGINEER';
  formState.email = '';
  modalVisible.value = true;
}

/** 打开编辑弹窗 */
function handleOpenEdit(record: SystemApi.User) {
  editingUser.value = record;
  formState.username = record.username;
  formState.password = '';
  formState.displayName = record.displayName;
  formState.role = record.role;
  formState.email = record.email ?? '';
  modalVisible.value = true;
}

/** 提交新增/编辑 */
function handleSubmit() {
  formRef.value?.validate().then(async () => {
    modalLoading.value = true;
    try {
      if (editingUser.value) {
        await updateUserApi(editingUser.value.id, {
          displayName: formState.displayName,
          role: formState.role,
          email: formState.email || undefined,
        });
        message.success('用户信息更新成功');
      } else {
        await createUserApi({
          username: formState.username,
          password: formState.password,
          displayName: formState.displayName,
          role: formState.role,
          email: formState.email || undefined,
        });
        message.success('用户创建成功');
      }
      modalVisible.value = false;
      await loadList();
    } catch {
      // 错误已由拦截器处理
    } finally {
      modalLoading.value = false;
    }
  });
}

/** 禁用用户二次确认 - 使用 ClpmDangerConfirmModal 替代 Modal.confirm */
const disableOpen = ref(false);
const disableTarget = ref('');
const disableLoading = ref(false);
/** 暂存待禁用用户（点击确认后用于 API 调用） */
const pendingDisable = ref<null | SystemApi.User>(null);

function handleDisable(record: SystemApi.User) {
  pendingDisable.value = record;
  disableTarget.value = `${record.displayName}（${record.username}）`;
  disableOpen.value = true;
}

async function handleDisableConfirm() {
  if (!pendingDisable.value) return;
  disableLoading.value = true;
  try {
    await deleteUserApi(pendingDisable.value.id);
    message.success('用户已禁用');
    disableOpen.value = false;
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    disableLoading.value = false;
  }
}

// ===== P2-07：批量禁用/启用 =====
const batchDisableOpen = ref(false);
const batchDisableLoading = ref(false);

/** 批量禁用：打开确认弹窗 */
function handleBatchDisable() {
  if (selectedDisableCount.value === 0) {
    message.warning('所选用户中没有可禁用的启用状态用户');
    return;
  }
  batchDisableOpen.value = true;
}

/** 批量禁用：确认执行（Promise.all 并行调用 deleteUserApi） */
async function handleBatchDisableConfirm() {
  const targets = userList.value.filter(
    (u) => selectedRowKeys.value.includes(u.id) && u.isActive,
  );
  if (targets.length === 0) return;
  batchDisableLoading.value = true;
  try {
    const results = await Promise.allSettled(
      targets.map((u) => deleteUserApi(u.id)),
    );
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;
    const failed = results.length - succeeded;
    if (succeeded > 0) {
      message.success(`已禁用 ${succeeded} 个用户`);
    }
    if (failed > 0) {
      message.warning(`${failed} 个用户禁用失败`);
    }
    batchDisableOpen.value = false;
    selectedRowKeys.value = [];
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchDisableLoading.value = false;
  }
}

/** 批量启用：直接执行（启用为安全操作，无需危险确认） */
async function handleBatchEnable() {
  if (selectedEnableCount.value === 0) {
    message.warning('所选用户中没有可启用的禁用状态用户');
    return;
  }
  const targets = userList.value.filter(
    (u) => selectedRowKeys.value.includes(u.id) && !u.isActive,
  );
  batchLoading.value = true;
  try {
    const results = await Promise.allSettled(
      targets.map((u) => updateUserApi(u.id, { isActive: true })),
    );
    const succeeded = results.filter((r) => r.status === 'fulfilled').length;
    const failed = results.length - succeeded;
    if (succeeded > 0) {
      message.success(`已启用 ${succeeded} 个用户`);
    }
    if (failed > 0) {
      message.warning(`${failed} 个用户启用失败`);
    }
    selectedRowKeys.value = [];
    await loadList();
  } catch {
    // 错误已由拦截器处理
  } finally {
    batchLoading.value = false;
  }
}

/** 打开重置密码弹窗 */
function handleOpenReset(record: SystemApi.User) {
  resetTarget.value = record;
  resetForm.newPassword = '';
  resetModalVisible.value = true;
}

/** 提交重置密码 */
async function handleSubmitReset() {
  if (!resetTarget.value) return;
  if (!resetForm.newPassword) {
    message.warning('请输入新密码');
    return;
  }
  resetModalLoading.value = true;
  try {
    await resetUserPasswordApi(resetTarget.value.id, {
      newPassword: resetForm.newPassword,
    });
    message.success('密码重置成功');
    resetModalVisible.value = false;
  } catch {
    // 错误已由拦截器处理
  } finally {
    resetModalLoading.value = false;
  }
}

function roleLabel(role: ClpmRole): string {
  return ROLE_LABELS[role] || role;
}

function roleColor(role: ClpmRole): string {
  const map: Record<ClpmRole, string> = {
    ADMIN: 'red',
    EXPERT: 'purple',
    IC_ENGINEER: 'blue',
    PE_ENGINEER: 'cyan',
    SPONSOR: 'gold',
  };
  return map[role] || 'default';
}

// ===== P3-38：密码复制 + 随机生成 =====

/** 复制密码到剪贴板 */
async function handleCopyPassword() {
  if (!resetForm.newPassword) {
    message.warning('请先输入新密码');
    return;
  }
  try {
    await navigator.clipboard.writeText(resetForm.newPassword);
    message.success('密码已复制到剪贴板');
  } catch {
    message.error('复制失败，请手动选择复制');
  }
}

/** 生成随机密码（大小写+数字，12位） */
function handleGeneratePassword() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789';
  let pwd = '';
  for (let i = 0; i < 12; i++) {
    pwd += chars[Math.floor(Math.random() * chars.length)];
  }
  resetForm.newPassword = pwd;
}

onMounted(() => {
  loadList();
});

/** 工具栏刷新：重新加载用户列表 */
function handleRefresh() {
  loadList();
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '用户管理 帮助',
    content:
      '用户管理页：管理系统账号、角色、密码与启用状态。支持按用户名、角色、状态筛选；新增/编辑用户（弹窗表单）、重置密码、禁用用户（二次确认）。5 种角色：管理员 / 工艺专家 / 仪控工程师 / 性能工程师 / 赞助者。仅 ADMIN 可访问。刷新按钮重新拉取用户列表，点击「导出」可将当前筛选结果保存为 CSV 或 Excel 文件。',
  });
}

/** P3-05：导出当前筛选结果为 CSV 或 Excel */
function handleExport(format: 'csv' | 'excel') {
  if (userList.value.length === 0) {
    message.warning('当前无可导出的数据');
    return;
  }
  const headers = [
    '用户名',
    '姓名',
    '角色',
    '邮箱',
    '状态',
    '创建时间',
    '最后登录',
  ];
  const rows = userList.value.map((u) => [
    u.username,
    u.displayName ?? '',
    ROLE_LABELS[u.role as ClpmRole] ?? u.role,
    u.email ?? '',
    u.isActive ? '启用' : '禁用',
    formatTime(u.createdAt),
    formatTime(u.lastLoginAt),
  ]);
  exportData({
    filename: `users-${new Date().toISOString().slice(0, 10)}`,
    format,
    headers,
    rows,
    sheetName: '用户列表',
  });
  message.success(`已导出 ${userList.value.length} 条记录`);
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助；导出独立 Dropdown） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="用户管理"
      subtitle="管理账号、角色、密码重置与启用状态。"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
        <!-- P3-05：导出 CSV/Excel 双格式（Dropdown 选择） -->
        <Dropdown>
          <ClpmToolbarButton
            icon="export"
            label="导出"
            tooltip="导出当前筛选结果为 CSV 或 Excel"
          />
          <template #overlay>
            <Menu @click="(e: any) => handleExport(e.key as 'csv' | 'excel')">
              <Menu.Item key="csv">导出 CSV</Menu.Item>
              <Menu.Item key="excel">导出 Excel</Menu.Item>
            </Menu>
          </template>
        </Dropdown>
        <!-- A-07：密度三档切换（紧凑/标准/宽松，点击循环） -->
        <ClpmToolbarButton
          icon="ant-design:column-height-outlined"
          :label="`密度：${densityLabel}`"
          :tooltip="`密度：${densityLabel}（点击切换）`"
          @click="cycleDensity"
        />
      </template>
    </ClpmPageToolbar>
    <ClpmDataCanvas class="mt-4" title="用户列表" :loading="loading">
      <!-- 筛选栏 -->
      <div class="mb-4 flex flex-wrap items-center gap-3">
        <Input
          v-model:value="query.username"
          placeholder="搜索用户名"
          style="width: 180px"
          allow-clear
          @press-enter="handleSearch"
        />
        <Select
          v-model:value="query.role"
          placeholder="角色筛选"
          style="width: 160px"
          allow-clear
          :options="roleOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="query.isActive"
          placeholder="状态筛选"
          style="width: 120px"
          allow-clear
          :options="statusOptions"
          @change="handleSearch"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
        <div class="flex-1"></div>
        <Button type="primary" @click="handleOpenAdd">新建用户</Button>
      </div>

      <!-- P2-07：批量操作工具栏（选中行时显示） -->
      <div
        v-if="selectedRowKeys.length > 0"
        class="mb-3 flex items-center gap-3 rounded border border-blue-200 bg-blue-50 px-4 py-2"
      >
        <Badge :count="selectedRowKeys.length" :offset="[6, 0]" />
        <span class="text-sm text-blue-700">
          已选 {{ selectedRowKeys.length }} 个用户
          <template v-if="selectedDisableCount > 0">
            （{{ selectedDisableCount }} 个可禁用）
          </template>
          <template v-if="selectedEnableCount > 0">
            （{{ selectedEnableCount }} 个可启用）
          </template>
        </span>
        <div class="flex-1"></div>
        <Tooltip
          :title="
            selectedDisableCount === 0
              ? '所选用户中没有可禁用的启用状态用户'
              : ''
          "
        >
          <Button
            size="small"
            danger
            :disabled="selectedDisableCount === 0"
            :loading="batchDisableLoading"
            @click="handleBatchDisable"
          >
            批量禁用
          </Button>
        </Tooltip>
        <Tooltip
          :title="
            selectedEnableCount === 0
              ? '所选用户中没有可启用的禁用状态用户'
              : ''
          "
        >
          <Button
            size="small"
            type="primary"
            ghost
            :disabled="selectedEnableCount === 0"
            :loading="batchLoading"
            @click="handleBatchEnable"
          >
            批量启用
          </Button>
        </Tooltip>
        <Button size="small" type="text" @click="selectedRowKeys = []">
          取消选择
        </Button>
      </div>

      <Table
        :columns="columns"
        :data-source="userList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: SystemApi.User) => record.id"
        :row-selection="rowSelection"
        :scroll="{ x: 1300 }"
        :size="tableSize"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'role'">
            <Tag :color="roleColor(record.role as ClpmRole)">
              {{ roleLabel(record.role as ClpmRole) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'isActive'">
            <Tag :color="record.isActive ? 'green' : 'default'">
              {{ record.isActive ? '启用' : '禁用' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'lastLoginAt'">
            {{ formatTime(record.lastLoginAt) }}
          </template>
          <template v-else-if="column.key === 'createdAt'">
            {{ formatTime(record.createdAt) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex gap-1">
              <Button
                type="link"
                size="small"
                @click="handleOpenEdit(record as SystemApi.User)"
              >
                编辑
              </Button>
              <Button
                type="link"
                size="small"
                @click="handleOpenReset(record as SystemApi.User)"
              >
                重置密码
              </Button>
              <Button
                v-if="record.isActive"
                type="link"
                size="small"
                danger
                @click="handleDisable(record as SystemApi.User)"
              >
                禁用
              </Button>
            </div>
          </template>
        </template>
        <template #emptyText>
          <ClpmEmptyState
            title="无匹配用户"
            description="可调整筛选条件，或点击「新建用户」创建账号。"
            :actions="[
              { label: '新建用户', primary: true, onClick: handleOpenAdd },
            ]"
          />
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 新增/编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="
        editingUser ? `编辑用户 - ${editingUser.displayName}` : '新建用户'
      "
      :confirm-loading="modalLoading"
      width="560px"
      @ok="handleSubmit"
    >
      <Form ref="formRef" :model="formState" layout="vertical" class="pt-4">
        <FormItem
          name="username"
          label="用户名"
          :rules="[{ required: true, message: '请输入用户名' }]"
        >
          <Input
            v-model:value="formState.username"
            placeholder="登录用户名"
            :disabled="!!editingUser"
          />
          <!-- P3-07：编辑模式下用户名不可修改的说明 -->
          <template v-if="editingUser" #extra>
            <span class="text-xs opacity-60">用户名创建后不可修改</span>
          </template>
        </FormItem>

        <FormItem
          v-if="!editingUser"
          name="password"
          label="初始密码"
          :rules="[{ required: true, message: '请输入初始密码' }]"
        >
          <Input.Password
            v-model:value="formState.password"
            placeholder="初始密码"
          />
        </FormItem>

        <FormItem
          name="displayName"
          label="姓名"
          :rules="[{ required: true, message: '请输入姓名' }]"
        >
          <Input v-model:value="formState.displayName" placeholder="用户姓名" />
        </FormItem>

        <FormItem
          name="role"
          label="角色"
          :rules="[{ required: true, message: '请选择角色' }]"
        >
          <Select
            v-model:value="formState.role"
            :options="roleOptions"
            placeholder="选择角色"
          />
        </FormItem>

        <FormItem name="email" label="邮箱">
          <Input v-model:value="formState.email" placeholder="user@plant.com" />
        </FormItem>
      </Form>
    </Modal>

    <!-- 重置密码 Modal -->
    <Modal
      v-model:open="resetModalVisible"
      :title="`重置密码 - ${resetTarget?.displayName || ''}`"
      :confirm-loading="resetModalLoading"
      width="440px"
      @ok="handleSubmitReset"
    >
      <Form :model="resetForm" layout="vertical" class="pt-4">
        <FormItem label="用户名">
          <span class="font-medium">{{ resetTarget?.username }}</span>
        </FormItem>
        <FormItem
          name="newPassword"
          label="新密码"
          :rules="[{ required: true, message: '请输入新密码' }]"
        >
          <Space direction="vertical" class="w-full" :size="8">
            <Input.Password
              v-model:value="resetForm.newPassword"
              placeholder="请输入新密码"
            />
            <Space :size="8">
              <Button
                size="small"
                type="primary"
                ghost
                @click="handleGeneratePassword"
              >
                随机生成
              </Button>
              <!-- P3-07：无密码时 disabled 增加 Tooltip -->
              <Tooltip
                :title="!resetForm.newPassword ? '请先生成或输入密码' : ''"
              >
                <Button
                  size="small"
                  :disabled="!resetForm.newPassword"
                  @click="handleCopyPassword"
                >
                  复制密码
                </Button>
              </Tooltip>
            </Space>
          </Space>
        </FormItem>
      </Form>
    </Modal>

    <!-- 禁用用户二次确认 - ClpmDangerConfirmModal -->
    <ClpmDangerConfirmModal
      v-model:open="disableOpen"
      title="禁用用户"
      action="禁用"
      :target="disableTarget"
      impact-scope="该用户将无法登录系统、活跃会话将被终止"
      rollback-tip="可随时重新启用"
      :require-confirm-code="true"
      :loading="disableLoading"
      @confirm="handleDisableConfirm"
    />

    <!-- P2-07：批量禁用确认 - ClpmDangerConfirmModal -->
    <ClpmDangerConfirmModal
      v-model:open="batchDisableOpen"
      title="批量禁用用户"
      action="批量禁用"
      :target="`${selectedDisableCount} 个启用状态用户`"
      impact-scope="所选用户将无法登录系统、活跃会话将被终止"
      rollback-tip="可随时在用户管理页批量重新启用"
      :require-confirm-code="true"
      :loading="batchDisableLoading"
      @confirm="handleBatchDisableConfirm"
    />
  </Page>
</template>
