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

import { onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Button,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Select,
  Table,
  Tag,
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
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useTableDensity } from '#/composables/use-table-density';
import { formatTime } from '#/utils/format';
import { ClpmEmptyState } from '#/components/clpm';

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
      '用户管理页：管理系统账号、角色、密码与启用状态。支持按用户名、角色、状态筛选；新增/编辑用户（弹窗表单）、重置密码、禁用用户（二次确认）。5 种角色：管理员 / 工艺专家 / 仪控工程师 / 性能工程师 / 赞助者。仅 ADMIN 可访问。刷新按钮重新拉取用户列表。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
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
      :title="editingUser ? `编辑用户 - ${editingUser.displayName}` : '新建用户'"
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
          <Input.Password
            v-model:value="resetForm.newPassword"
            placeholder="请输入新密码"
          />
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
  </Page>
</template>
