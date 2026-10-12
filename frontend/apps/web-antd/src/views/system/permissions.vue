<script lang="ts" setup>
/**
 * S5-SYS-006 授权配置页（P4-04 重构：静态权限矩阵 → DB 持久策略管理）
 *
 * - 授权矩阵由后端 /system/authorization/*（P4-01）驱动：角色列表 /
 *   页面树（目录 PAGE_GROUP→PAGE）/ 动作清单（moduleKey 分组）/
 *   scope 见用户管理页 / 差异预览 / 审计（策略版本账本）/ 回退；
 * - 保存带 expectedRevision 乐观锁：他人并发变更 409 → 自动刷新最新版本；
 * - 管理员保护：protected 码（授权管理/用户/审计/模块管理）在 ADMIN 角色
 *   禁止取消（后端 403 兜底，前端仅拦截误操作）；
 * - 授权只收紧不放宽：模块开关与 fitness 等业务门禁在授权之外独立执行
 *   （AND 语义），有效授权视图展示模块禁用下不可用的已授权码；
 * - 变更即生效：服务端下一请求取新版本（authz_runtime revision 比对）；
 *   其他在线会话 ≤30s 或下次交互前由 use-authz-live 刷新。
 */
import type { TableColumnsType } from 'ant-design-vue';
import type { TreeProps } from 'ant-design-vue/es/tree';

import type { AuthorizationApi } from '#/api/authorization';

import { computed, onMounted, reactive, ref } from 'vue';

import { Page } from '@vben/common-ui';

import {
  Alert,
  Button,
  Checkbox,
  CheckboxGroup,
  Input,
  message,
  Modal,
  Spin,
  Table,
  TabPane,
  Tabs,
  Tag,
  Tooltip,
  Tree,
} from 'ant-design-vue';

import { CLPM_ROLES, ROLE_LABELS } from '#/api/auth';
import {
  getAuthorizationCatalogApi,
  getAuthorizationRevisionsApi,
  getAuthorizationRoleApi,
  getAuthorizationRolesApi,
  rollbackAuthorizationRevisionApi,
  updateAuthorizationRoleApi,
} from '#/api/authorization';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'SystemPermissions' });

// ===== 状态 =====
const loading = ref(false);
/** 全局策略 revision（保存的乐观锁 expectedRevision） */
const revision = ref(0);
const catalog = ref<AuthorizationApi.CatalogResult | null>(null);
/** 五角色当前矩阵（服务器态，diff 基线） */
const rolesGrants = ref(new Map<string, AuthorizationApi.RoleGrants>());
const selectedRole = ref<string>('ADMIN');
/** 选中角色的有效授权视图（依赖闭包 + 模块禁用标注） */
const effective = ref<AuthorizationApi.EffectiveAccessResult | null>(null);
const effectiveLoading = ref(false);

// 编辑态（与服务器 grants 的差异即待保存变更）
const checkedPages = ref<string[]>([]);
const checkedActions = ref<string[]>([]);

// 保存弹窗
const saveOpen = ref(false);
const saveLoading = ref(false);
const saveForm = reactive({ reason: '' });

// 版本与回退
const revisionsLoading = ref(false);
const revisions = ref<AuthorizationApi.PolicyRevisionInfo[]>([]);
const rollbackOpen = ref(false);
const rollbackLoading = ref(false);
const rollbackTarget = ref<AuthorizationApi.PolicyRevisionInfo | null>(null);
const rollbackForm = reactive({ reason: '' });

const MODULE_LABELS: Record<string, string> = {
  assess: '性能评估',
  config: '参数配置',
  diagnosis: '回路诊断',
  handling: '运维处置',
  monitor: '回路监控',
  reports: '统计分析',
  system: '系统管理',
  tuning: '参数整定',
};

function roleLabel(role: string): string {
  return (ROLE_LABELS as Record<string, string>)[role] || role;
}

function roleColor(role: string): string {
  const map: Record<string, string> = {
    ADMIN: 'red',
    EXPERT: 'purple',
    IC_ENGINEER: 'blue',
    PE_ENGINEER: 'cyan',
    SPONSOR: 'gold',
  };
  return map[role] || 'default';
}

const ROLE_DESCRIPTIONS: Record<string, string> = {
  ADMIN: '系统管理员：全模块管理权限，负责系统配置、用户管理、运维监控',
  EXPERT: '工艺专家：诊断协同 + 整定执行，提供专业判断和整定建议',
  IC_ENGINEER: '仪控工程师：回路管理 + 诊断执行 + 整定执行，日常运维主力',
  PE_ENGINEER: '性能工程师：监控/评估/诊断查看，关注KPI趋势和绩效',
  SPONSOR: '赞助者：监控/评估只读，管理评审和决策视角',
};

function roleDescription(role: string): string {
  return ROLE_DESCRIPTIONS[role] || '';
}

const protectedSet = computed(
  () => new Set(catalog.value?.protectedCodes),
);

const currentGrants = computed(
  () => rolesGrants.value.get(selectedRole.value) ?? null,
);

/** 页面授权树（目录组 → 页面；checkStrictly 父子不联动，只管理叶子码） */
const pageTreeData = computed<TreeProps['treeData']>(() => {
  const pages = catalog.value?.pages ?? [];
  const groups = pages.filter((p) => p.kind === 'PAGE_GROUP');
  const isAdmin = selectedRole.value === 'ADMIN';
  return groups.map((g) => ({
    key: g.code,
    title: g.description || g.code,
    children: pages
      .filter((p) => p.kind === 'PAGE' && p.parentCode === g.code)
      .map((p) => ({
        key: p.code,
        title: p.description || p.code,
        disableCheckbox: isAdmin && protectedSet.value.has(p.code),
      })),
  }));
});

/** 动作清单（按 moduleKey 分组；ADMIN 角色 protected 码禁取消） */
const actionGroups = computed(() => {
  const actions = catalog.value?.actions ?? [];
  const groups = new Map<string, AuthorizationApi.CatalogItem[]>();
  for (const a of actions) {
    const key = a.moduleKey ?? '__common__';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)?.push(a);
  }
  const isAdmin = selectedRole.value === 'ADMIN';
  const unavailable = new Set(effective.value?.unavailableByModule?.actions);
  return [...groups.entries()]
    .toSorted(([a], [b]) => {
      if (a === '__common__') return 1;
      if (b === '__common__') return -1;
      return (MODULE_LABELS[a] ?? a).localeCompare(MODULE_LABELS[b] ?? b);
    })
    .map(([key, items]) => ({
      key,
      label: key === '__common__' ? '通用/横切' : (MODULE_LABELS[key] ?? key),
      items: items.map((a) => ({
        ...a,
        disabled: isAdmin && protectedSet.value.has(a.code),
        moduleDisabled: unavailable.has(a.code),
      })),
    }));
});

/** 待保存差异（编辑态 vs 服务器态） */
const diff = computed(() => {
  const base = currentGrants.value;
  if (!base) {
    return { added: [], removed: [] as string[] };
  }
  const next = new Set([...checkedPages.value, ...checkedActions.value]);
  const prev = new Set([...base.pages, ...base.actions]);
  return {
    added: [...next].filter((c) => !prev.has(c)).toSorted(),
    removed: [...prev].filter((c) => !next.has(c)).toSorted(),
  };
});

const hasChanges = computed(
  () => diff.value.added.length > 0 || diff.value.removed.length > 0,
);

/** 授权概览（动态生成，替换原静态 PERMISSION_MATRIX）：角色 × 页面组 */
const overviewColumns = computed(() => {
  const groups = (catalog.value?.pages ?? []).filter(
    (p) => p.kind === 'PAGE_GROUP',
  );
  return groups.map((g) => ({
    group: g,
    label: g.description || g.code,
  }));
});

const overviewRows = computed(() =>
  CLPM_ROLES.map((role) => {
    const grants = rolesGrants.value.get(role);
    const pages = grants?.pages ?? [];
    const actions = grants?.actions ?? [];
    const catalogPages = catalog.value?.pages ?? [];
    const catalogActions = catalog.value?.actions ?? [];
    const cells: Record<string, string> = {};
    for (const { group } of overviewColumns.value) {
      const pageCount = catalogPages
        .filter((p) => p.kind === 'PAGE' && p.parentCode === group.code)
        .filter((p) => pages.includes(p.code)).length;
      // 动作计数按模块 key 归组（组码 page:xxx → 模块 xxx；驾驶舱/工作台组
      // 无专属模块动作，portal:view 等横切码不单列）
      const moduleKey = group.code.slice('page:'.length);
      const moduleActionTotal = catalogActions.filter(
        (a) => a.moduleKey === moduleKey,
      ).length;
      const actionCount = actions.filter((code) =>
        catalogActions.some((a) => a.code === code && a.moduleKey === moduleKey),
      ).length;
      cells[group.code] =
        pageCount > 0 || actionCount > 0
          ? `页 ${pageCount}${moduleActionTotal > 0 ? ` · 动 ${actionCount}` : ''}`
          : '—';
    }
    return { role, cells };
  }),
);

// ===== 数据加载 =====
async function loadAll(): Promise<void> {
  loading.value = true;
  try {
    const [catalogResult, rolesResult] = await Promise.all([
      getAuthorizationCatalogApi(),
      getAuthorizationRolesApi(),
    ]);
    catalog.value = catalogResult;
    revision.value = rolesResult.revision;
    const map = new Map<string, AuthorizationApi.RoleGrants>();
    for (const r of rolesResult.roles) map.set(r.role, r);
    rolesGrants.value = map;
    // 编辑态对齐最新服务器态（丢弃本地未保存差异）
    syncEditorFromServer();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

function syncEditorFromServer(): void {
  const grants = rolesGrants.value.get(selectedRole.value);
  checkedPages.value = [...(grants?.pages ?? [])];
  checkedActions.value = [...(grants?.actions ?? [])];
}

async function loadEffective(role: string): Promise<void> {
  effectiveLoading.value = true;
  try {
    effective.value = await getAuthorizationRoleApi(role);
  } catch {
    effective.value = null;
  } finally {
    effectiveLoading.value = false;
  }
}

function selectRole(role: string): void {
  if (role === selectedRole.value) return;
  if (hasChanges.value) {
    Modal.confirm({
      title: '放弃未保存的变更？',
      content: '当前角色存在未保存的授权变更，切换角色将丢弃这些调整。',
      okText: '放弃并切换',
      cancelText: '继续编辑',
      onOk: () => applyRoleSelection(role),
    });
    return;
  }
  applyRoleSelection(role);
}

function applyRoleSelection(role: string): void {
  selectedRole.value = role;
  syncEditorFromServer();
  void loadEffective(role);
}

// ===== 保存（expectedRevision 409 处理） =====
function openSave(): void {
  if (!hasChanges.value) {
    message.info('授权无变更');
    return;
  }
  saveForm.reason = '';
  saveOpen.value = true;
}

async function confirmSave(): Promise<void> {
  if (!saveForm.reason.trim()) {
    message.warning('请填写变更原因（审计必填）');
    return;
  }
  saveLoading.value = true;
  try {
    await updateAuthorizationRoleApi(selectedRole.value, {
      expectedRevision: revision.value,
      pageCodes: checkedPages.value,
      actionCodes: checkedActions.value,
      reason: saveForm.reason.trim(),
    });
    message.success(
      '授权已保存并即时生效；其他在线会话将在下次交互或 30 秒内自动刷新',
    );
    saveOpen.value = false;
    await loadAll();
    await loadEffective(selectedRole.value);
  } catch (error: any) {
    const bizCode = error?.response?.data?.code;
    if (bizCode === 'ERR_AUTHZ_REVISION_CONFLICT') {
      // 全局 toast 已提示冲突详情；此处刷新最新版本（编辑态随之重置）
      message.warning('已刷新为最新策略版本，请基于最新授权重新确认变更');
      saveOpen.value = false;
      await loadAll();
      await loadEffective(selectedRole.value);
    }
    // 其他错误（未知码/漏依赖/管理员保护/网络）由拦截器全局提示；
    // 保持弹窗打开以便修正
  } finally {
    saveLoading.value = false;
  }
}

// ===== 版本与回退 =====
const revisionColumns: TableColumnsType = [
  { title: 'Revision', dataIndex: 'revision', key: 'revision', width: 90 },
  { title: '操作', dataIndex: 'operation', key: 'operation', width: 140 },
  { title: '对象', key: 'target', width: 150 },
  { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
  { title: '操作人', dataIndex: 'operator', key: 'operator', width: 110 },
  { title: '时间', dataIndex: 'createdAt', key: 'createdAt', width: 170 },
  { title: '操作列', key: 'action', width: 100, fixed: 'right' },
];

async function loadRevisions(): Promise<void> {
  revisionsLoading.value = true;
  try {
    const result = await getAuthorizationRevisionsApi();
    revision.value = result.revision;
    revisions.value = result.revisions;
  } catch {
    // 错误已由拦截器处理
  } finally {
    revisionsLoading.value = false;
  }
}

/** SEED（种子行）与 ROLLBACK 行不可再回退（后端兜底，前端同步禁用） */
function rollbackDisabled(row: AuthorizationApi.PolicyRevisionInfo): boolean {
  return row.operation === 'SEED' || row.operation === 'ROLLBACK';
}

function openRollback(row: AuthorizationApi.PolicyRevisionInfo): void {
  rollbackTarget.value = row;
  rollbackForm.reason = '';
  rollbackOpen.value = true;
}

async function confirmRollback(): Promise<void> {
  const target = rollbackTarget.value;
  if (!target) return;
  if (!rollbackForm.reason.trim()) {
    message.warning('请填写回退原因（审计必填）');
    return;
  }
  rollbackLoading.value = true;
  try {
    await rollbackAuthorizationRevisionApi(target.revision, {
      expectedRevision: revision.value,
      reason: rollbackForm.reason.trim(),
    });
    message.success('已回退（发布逆操作为新版本，历史保留）');
    rollbackOpen.value = false;
    await Promise.all([loadAll(), loadRevisions()]);
    await loadEffective(selectedRole.value);
  } catch (error: any) {
    const bizCode = error?.response?.data?.code;
    if (bizCode === 'ERR_AUTHZ_REVISION_CONFLICT') {
      message.warning('已刷新为最新策略版本，请重试回退');
      rollbackOpen.value = false;
      await Promise.all([loadAll(), loadRevisions()]);
    }
  } finally {
    rollbackLoading.value = false;
  }
}

function operationTagColor(operation: string): string {
  if (operation === 'SEED') return 'default';
  if (operation === 'ROLLBACK') return 'orange';
  if (operation === 'USER_SCOPE') return 'geekblue';
  return 'blue';
}

function codesSummary(
  value: null | Record<string, unknown> | undefined | unknown[],
): string {
  if (!value) return '—';
  try {
    const pages = (value as any)?.pages;
    const actions = (value as any)?.actions;
    const scopeType = (value as any)?.scopeType;
    if (Array.isArray(pages) || Array.isArray(actions)) {
      return `页 ${pages?.length ?? 0} · 动 ${actions?.length ?? 0}`;
    }
    if (scopeType) {
      return `scope=${scopeType}${(value as any)?.nodeId ? ` (${(value as any).nodeId})` : ''}`;
    }
    return JSON.stringify(value).slice(0, 120);
  } catch {
    return '—';
  }
}

// ===== 页签切换 =====
const activeTab = ref('roles');

function handleTabChange(key: number | string): void {
  if (key === 'revisions') {
    void loadRevisions();
  }
}

// ===== 工具栏 =====
function handleRefresh(): void {
  if (activeTab.value === 'revisions') {
    void Promise.all([loadAll(), loadRevisions()]);
  } else {
    void loadAll().then(() => loadEffective(selectedRole.value));
  }
}

function handleHelp(): void {
  showPageHelp({
    title: '授权配置 帮助',
    content:
      '授权配置页：管理五角色（管理员/工艺专家/仪控工程师/性能工程师/赞助者）的页面授权与动作授权（DB 持久策略驱动）。页面树按目录分组勾选；动作清单按模块分组；保存前预览差异并必填变更原因（审计留痕）。带 🔒 的受保护码（授权管理/用户/审计/模块管理）不可从管理员移除（最后管理员失权保护）。保存即时生效（服务端下一请求取新版本）；其他在线会话 30 秒内或下次交互时自动刷新，无需重新登录。「版本与回退」页签查看策略变更账本并可将目标版本回退（发布逆操作，不删历史）。注意：授权只收紧不放宽——模块开关与整定 fitness 等业务门禁在授权之外独立执行；数据范围（scope）在用户管理页按用户配置。',
  });
}

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

onMounted(() => {
  void loadAll().then(() => loadEffective(selectedRole.value));
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="授权配置"
      :subtitle="`页面授权 · 动作授权 · 差异预览 · 版本回退（当前策略 revision=${revision}）`"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- 授权概览（动态矩阵：替换原静态权限矩阵） -->
    <ClpmDataCanvas class="mt-4" title="授权概览">
      <div class="mb-4">
        <p class="text-sm text-gray-500">
          五角色当前授权统计（DB 策略矩阵动态生成，非静态预设）·
          页=可进页面数 · 动=可用动作数 · 授权只收紧不放宽
        </p>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full border-collapse text-sm">
          <thead>
            <tr class="bg-gray-50">
              <th
                class="border border-gray-200 px-3 py-2 text-center font-medium"
                style="width: 130px"
              >
                角色
              </th>
              <th
                v-for="col in overviewColumns"
                :key="col.group.code"
                class="border border-gray-200 px-3 py-2 text-center font-medium"
              >
                {{ col.label }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in overviewRows" :key="row.role">
              <td class="border border-gray-200 px-3 py-2">
                <Tooltip :title="roleDescription(row.role)" placement="right">
                  <Tag :color="roleColor(row.role)">{{ roleLabel(row.role) }}</Tag>
                </Tooltip>
              </td>
              <td
                v-for="col in overviewColumns"
                :key="col.group.code"
                class="border border-gray-200 px-3 py-2 text-center text-xs"
              >
                {{ row.cells[col.group.code] }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </ClpmDataCanvas>

    <Tabs
      v-model:active-key="activeTab"
      class="mt-4"
      @change="handleTabChange"
    >
      <!-- Tab 1：角色授权编辑 -->
      <TabPane key="roles" tab="角色授权">
        <ClpmDataCanvas :loading="loading">
          <!-- 角色选择 -->
          <div class="mb-4 flex flex-wrap items-center gap-2">
            <span class="text-sm text-gray-500">选择角色：</span>
            <Tooltip
              v-for="r in CLPM_ROLES"
              :key="r"
              :title="roleDescription(r)"
            >
              <Tag
                :color="selectedRole === r ? roleColor(r) : 'default'"
                :style="{ cursor: 'pointer' }"
                :class="{ 'font-medium': selectedRole === r }"
                @click="selectRole(r)"
              >
                {{ roleLabel(r) }}
              </Tag>
            </Tooltip>
            <div class="flex-1"></div>
            <Button
              type="primary"
              :disabled="!hasChanges"
              @click="openSave"
            >
              保存授权{{ hasChanges ? `（${diff.added.length + diff.removed.length} 项变更）` : '' }}
            </Button>
          </div>

          <!-- 未保存变更提示（差异预览条） -->
          <Alert
            v-if="hasChanges"
            type="warning"
            show-icon
            class="mb-4"
            :message="`待保存变更：新增 ${diff.added.length} 项、移除 ${diff.removed.length} 项`"
          >
            <template #description>
              <div class="flex flex-wrap gap-1">
                <Tag v-for="c in diff.added" :key="`add-${c}`" color="green">
                  + {{ c }}
                </Tag>
                <Tag v-for="c in diff.removed" :key="`rm-${c}`" color="red">
                  - {{ c }}
                </Tag>
              </div>
            </template>
          </Alert>

          <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <!-- 页面授权树 -->
            <div class="rounded border border-gray-200 p-3">
              <div class="mb-2 font-medium">
                页面授权（菜单可达页；整组取消后对应菜单与直链同步移除）
              </div>
              <Tree
                :tree-data="pageTreeData"
                :checked-keys="checkedPages"
                checkable
                :check-strictly="true"
                :selectable="false"
                :height="420"
                @check="
                  (_keys: any, info: any) => {
                    // checkStrictly 下 checked 为完整勾选集合；仅接受叶子节点变更
                    if (info?.node?.children?.length) return;
                    const code = String(info?.node?.key ?? '');
                    checkedPages = info.checked
                      ? [...new Set([...checkedPages, code])]
                      : checkedPages.filter((c) => c !== code);
                  }
                "
              />
            </div>

            <!-- 动作授权清单 -->
            <div class="rounded border border-gray-200 p-3">
              <div class="mb-2 font-medium">
                动作授权（写操作自动依赖读取码；SPONSOR 零写为结构性红线）
              </div>
              <div class="max-h-[420px] space-y-4 overflow-y-auto pr-1">
                <div v-for="group in actionGroups" :key="group.key">
                  <div class="mb-1 text-xs font-medium text-gray-500">
                    {{ group.label }}
                  </div>
                  <CheckboxGroup
                    :value="checkedActions"
                    class="!flex !flex-col !items-start gap-1"
                    @change="
                      (vals: any) => {
                        checkedActions = vals as string[];
                      }
                    "
                  >
                    <Checkbox
                      v-for="a in group.items"
                      :key="a.code"
                      :value="a.code"
                      :disabled="a.disabled"
                    >
                      <span class="text-sm">{{ a.description || a.code }}</span>
                      <span class="ml-1 text-xs text-gray-400">{{ a.code }}</span>
                      <Tag v-if="a.isProtected" color="volcano" class="ml-1">
                        🔒保护
                      </Tag>
                      <Tag v-if="a.moduleDisabled" class="ml-1" color="default">
                        模块禁用
                      </Tag>
                    </Checkbox>
                  </CheckboxGroup>
                </div>
              </div>
            </div>
          </div>

          <!-- 有效授权解释（与实际 403 同源） -->
          <div class="mt-4 rounded border border-gray-200 p-3">
            <div class="mb-2 flex items-center gap-2 font-medium">
              有效授权解释
              <span class="text-xs font-normal text-gray-400">
                （与实际 403 判定同源：authz_runtime DB 矩阵 + 依赖闭包）
              </span>
            </div>
            <Spin :spinning="effectiveLoading">
              <template v-if="effective">
                <div class="text-sm">
                  有效动作
                  {{ effective.actions.length }} 个 · 可达页面
                  {{ effective.pages.length }} 个
                  <template v-if="effective.unavailableByModule?.actions?.length">
                    ·
                    <Tooltip
                      :title="`模块开关禁用下不可用：${effective.unavailableByModule.actions.join('、')}`"
                    >
                      <Tag color="default">
                        {{ effective.unavailableByModule.actions.length }} 个因模块禁用不可用
                      </Tag>
                    </Tooltip>
                  </template>
                </div>
                <div class="mt-1 text-xs text-gray-400">
                  {{ effective.note }}——若某操作被拒绝（403），以本视图码集合为解释口径
                </div>
              </template>
              <div v-else class="text-sm text-gray-400">暂无数据</div>
            </Spin>
          </div>
        </ClpmDataCanvas>
      </TabPane>

      <!-- Tab 2：版本与回退（审计） -->
      <TabPane key="revisions" tab="版本与回退">
        <ClpmDataCanvas
          title="策略版本账本"
          :loading="revisionsLoading"
        >
          <template #extra>
            <span class="text-xs text-gray-400">
              追加式账本（不删历史）；回退=发布逆操作为新 revision
            </span>
          </template>
          <Table
            :columns="revisionColumns"
            :data-source="revisions"
            :pagination="{ pageSize: 20, showTotal: (t: number) => `共 ${t} 条` }"
            row-key="revision"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'operation'">
                <Tag
                  :color="
                    operationTagColor(
                      (record as AuthorizationApi.PolicyRevisionInfo).operation,
                    )
                  "
                >
                  {{ (record as AuthorizationApi.PolicyRevisionInfo).operation }}
                </Tag>
                <Tag
                  v-if="(record as AuthorizationApi.PolicyRevisionInfo).rollbackOf"
                  color="orange"
                  class="ml-1"
                >
                  回退自 #{{
                    (record as AuthorizationApi.PolicyRevisionInfo).rollbackOf
                  }}
                </Tag>
              </template>
              <template v-else-if="column.key === 'target'">
                <span v-if="(record as AuthorizationApi.PolicyRevisionInfo).role">
                  {{ roleLabel((record as AuthorizationApi.PolicyRevisionInfo).role!) }}
                </span>
                <span
                  v-else-if="(record as AuthorizationApi.PolicyRevisionInfo).userId"
                >
                  用户
                  {{ (record as AuthorizationApi.PolicyRevisionInfo).userId!.slice(0, 8) }}
                </span>
                <span v-else>—</span>
              </template>
              <template v-else-if="column.key === 'createdAt'">
                {{ formatTime((record as AuthorizationApi.PolicyRevisionInfo).createdAt) }}
              </template>
              <template v-else-if="column.key === 'action'">
                <Tooltip
                  :title="
                    rollbackDisabled(record as AuthorizationApi.PolicyRevisionInfo)
                      ? '种子版本与回退行不可再回退'
                      : `恢复到 revision=${
                          (record as AuthorizationApi.PolicyRevisionInfo).revision
                        } 变更前的授权快照`
                  "
                >
                  <Button
                    type="link"
                    size="small"
                    :disabled="rollbackDisabled(record as AuthorizationApi.PolicyRevisionInfo)"
                    @click="openRollback(record as AuthorizationApi.PolicyRevisionInfo)"
                  >
                    回退
                  </Button>
                </Tooltip>
              </template>
            </template>
            <template #expandedRowRender="{ record }">
              <div class="grid grid-cols-1 gap-2 text-xs md:grid-cols-2">
                <div>
                  <strong>变更前：</strong>
                  {{
                    codesSummary(
                      (record as AuthorizationApi.PolicyRevisionInfo).beforeValue,
                    )
                  }}
                </div>
                <div>
                  <strong>变更后：</strong>
                  {{
                    codesSummary(
                      (record as AuthorizationApi.PolicyRevisionInfo).afterValue,
                    )
                  }}
                </div>
              </div>
            </template>
          </Table>
        </ClpmDataCanvas>
      </TabPane>
    </Tabs>

    <!-- 保存弹窗（差异预览 + 审计原因） -->
    <Modal
      v-model:open="saveOpen"
      :title="`保存授权变更 - ${roleLabel(selectedRole)}`"
      :confirm-loading="saveLoading"
      width="640px"
      @ok="confirmSave"
    >
      <div class="pt-3">
        <div class="mb-2 text-sm">
          <Tag color="green">新增 {{ diff.added.length }}</Tag>
          <Tag color="red">移除 {{ diff.removed.length }}</Tag>
          <span class="text-xs text-gray-400">
            expectedRevision={{ revision }}（并发变更将返回 409 并自动刷新）
          </span>
        </div>
        <div class="mb-3 max-h-48 overflow-y-auto rounded border border-gray-200 p-2">
          <div v-for="c in diff.added" :key="`a-${c}`" class="text-xs text-green-700">
            + {{ c }}
          </div>
          <div v-for="c in diff.removed" :key="`r-${c}`" class="text-xs text-red-600">
            - {{ c }}
          </div>
        </div>
        <div class="mb-1 text-sm">变更原因（必填，写入审计）</div>
        <Input.TextArea
          v-model:value="saveForm.reason"
          :rows="3"
          :maxlength="500"
          placeholder="例如：按 2026-10 用户裁决收紧 IC 工程师诊断操作权限"
          show-count
        />
      </div>
    </Modal>

    <!-- 回退弹窗 -->
    <Modal
      v-model:open="rollbackOpen"
      :title="`回退到 revision=${rollbackTarget?.revision ?? ''} 之前`"
      :confirm-loading="rollbackLoading"
      width="560px"
      @ok="confirmRollback"
    >
      <div class="pt-3">
        <Alert
          type="warning"
          show-icon
          class="mb-3"
          message="回退 = 发布逆操作"
          :description="`将恢复 revision=${rollbackTarget?.revision} 变更前的授权快照并生成新 revision；历史账本保留不删。目标版本：${rollbackTarget ? formatTime(rollbackTarget.createdAt) : ''}（${rollbackTarget?.operator}：${rollbackTarget?.reason ?? '—'}）`"
        />
        <div class="mb-1 text-sm">回退原因（必填，写入审计）</div>
        <Input.TextArea
          v-model:value="rollbackForm.reason"
          :rows="3"
          :maxlength="500"
          placeholder="例如：误收紧导致工艺专家无法访问诊断页，回退 #N"
          show-count
        />
      </div>
    </Modal>
  </Page>
</template>
