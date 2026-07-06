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

import { ClpmDataCanvas, ClpmPageToolbar } from '#/components/clpm';
import { CLPM_ROLES, ROLE_LABELS } from '#/api/auth';
import {
  createUserApi,
  deleteUserApi,
  getUserListApi,
  resetUserPasswordApi,
  updateUserApi,
} from '#/api/system';

defineOptions({ name: 'SystemUsers' });

const loading = ref(false);
const userList = ref<SystemApi.User[]>([]);
const total = ref(0);

const query = reactive({
  username: '' as string,
  role: undefined as ClpmRole | undefined,
  is_active: undefined as string | undefined,
  page: 1,
  page_size: 20,
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
    dataIndex: 'full_name',
    key: 'full_name',
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
    dataIndex: 'is_active',
    key: 'is_active',
    width: 90,
    align: 'center',
  },
  {
    title: '最后登录',
    dataIndex: 'last_login_at',
    key: 'last_login_at',
    width: 170,
  },
  {
    title: '创建时间',
    dataIndex: 'created_at',
    key: 'created_at',
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
  full_name: '',
  role: 'IC_ENGINEER' as ClpmRole,
  email: '',
  phone: '',
});

// 重置密码 Modal
const resetModalVisible = ref(false);
const resetModalLoading = ref(false);
const resetTarget = ref<null | SystemApi.User>(null);
const resetForm = reactive({
  new_password: '',
});

/** 加载用户列表 */
async function loadList() {
  loading.value = true;
  try {
    let isActiveParam: boolean | undefined;
    if (query.is_active === 'true') {
      isActiveParam = true;
    } else if (query.is_active === 'false') {
      isActiveParam = false;
    }
    const data = await getUserListApi({
      page: query.page,
      page_size: query.page_size,
      username: query.username || undefined,
      role: query.role,
      is_active: isActiveParam,
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
  query.page_size = pagination.pageSize || 20;
  loadList();
}

/** 打开新增弹窗 */
function handleOpenAdd() {
  editingUser.value = null;
  formState.username = '';
  formState.password = '';
  formState.full_name = '';
  formState.role = 'IC_ENGINEER';
  formState.email = '';
  formState.phone = '';
  modalVisible.value = true;
}

/** 打开编辑弹窗 */
function handleOpenEdit(record: SystemApi.User) {
  editingUser.value = record;
  formState.username = record.username;
  formState.password = '';
  formState.full_name = record.full_name;
  formState.role = record.role;
  formState.email = record.email;
  formState.phone = record.phone || '';
  modalVisible.value = true;
}

/** 提交新增/编辑 */
function handleSubmit() {
  formRef.value?.validate().then(async () => {
    modalLoading.value = true;
    try {
      if (editingUser.value) {
        await updateUserApi(editingUser.value.id, {
          full_name: formState.full_name,
          role: formState.role,
          email: formState.email,
          phone: formState.phone || undefined,
        });
        message.success('用户信息更新成功');
      } else {
        await createUserApi({
          username: formState.username,
          password: formState.password,
          full_name: formState.full_name,
          role: formState.role,
          email: formState.email,
          phone: formState.phone || undefined,
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

/** 禁用用户二次确认 */
function handleDisable(record: SystemApi.User) {
  Modal.confirm({
    title: '确认禁用用户',
    content: `即将禁用用户「${record.full_name}（${record.username}）」，禁用后该用户将无法登录系统。是否继续？`,
    okText: '确认禁用',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteUserApi(record.id);
        message.success('用户已禁用');
        await loadList();
      } catch {
        // 错误已由拦截器处理
      }
    },
  });
}

/** 打开重置密码弹窗 */
function handleOpenReset(record: SystemApi.User) {
  resetTarget.value = record;
  resetForm.new_password = '';
  resetModalVisible.value = true;
}

/** 提交重置密码 */
async function handleSubmitReset() {
  if (!resetTarget.value) return;
  if (!resetForm.new_password) {
    message.warning('请输入新密码');
    return;
  }
  resetModalLoading.value = true;
  try {
    await resetUserPasswordApi(resetTarget.value.id, {
      new_password: resetForm.new_password,
    });
    message.success('密码重置成功');
    resetModalVisible.value = false;
  } catch {
    // 错误已由拦截器处理
  } finally {
    resetModalLoading.value = false;
  }
}

function formatTime(t?: string): string {
  if (!t) return '—';
  try {
    // 强制北京时间（UTC+8）
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  } catch {
    return t;
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
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="用户管理"
      subtitle="管理账号、角色、密码重置与启用状态。"
    />
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
          v-model:value="query.is_active"
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
        <Button type="primary" @click="handleOpenAdd">新增用户</Button>
      </div>

      <Table
        :columns="columns"
        :data-source="userList"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.page_size,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: SystemApi.User) => record.id"
        :scroll="{ x: 1300 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'role'">
            <Tag :color="roleColor(record.role as ClpmRole)">
              {{ roleLabel(record.role as ClpmRole) }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'is_active'">
            <Tag :color="record.is_active ? 'green' : 'default'">
              {{ record.is_active ? '启用' : '禁用' }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'last_login_at'">
            {{ formatTime(record.last_login_at) }}
          </template>
          <template v-else-if="column.key === 'created_at'">
            {{ formatTime(record.created_at) }}
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
                v-if="record.is_active"
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
      </Table>
    </ClpmDataCanvas>

    <!-- 新增/编辑 Modal -->
    <Modal
      v-model:open="modalVisible"
      :title="editingUser ? `编辑用户 - ${editingUser.full_name}` : '新增用户'"
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
          name="full_name"
          label="姓名"
          :rules="[{ required: true, message: '请输入姓名' }]"
        >
          <Input v-model:value="formState.full_name" placeholder="用户姓名" />
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

        <FormItem name="phone" label="手机号">
          <Input v-model:value="formState.phone" placeholder="手机号（选填）" />
        </FormItem>
      </Form>
    </Modal>

    <!-- 重置密码 Modal -->
    <Modal
      v-model:open="resetModalVisible"
      :title="`重置密码 - ${resetTarget?.full_name || ''}`"
      :confirm-loading="resetModalLoading"
      width="440px"
      @ok="handleSubmitReset"
    >
      <Form :model="resetForm" layout="vertical" class="pt-4">
        <FormItem label="用户名">
          <span class="font-medium">{{ resetTarget?.username }}</span>
        </FormItem>
        <FormItem
          name="new_password"
          label="新密码"
          :rules="[{ required: true, message: '请输入新密码' }]"
        >
          <Input.Password
            v-model:value="resetForm.new_password"
            placeholder="请输入新密码"
          />
        </FormItem>
      </Form>
    </Modal>
  </Page>
</template>
