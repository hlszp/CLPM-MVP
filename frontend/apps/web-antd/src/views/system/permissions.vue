<script lang="ts" setup>
/**
 * S5-SYS-006 权限矩阵页
 *
 * 对齐 IDS v3.2 §5.1 + PRD §3 + UI/UX v4.1 §5.2 + §6.6.2
 * - 矩阵表格展示 5 类角色 × 6 大模块的权限
 * - 权限级别（查看/协同/执行/管理/服务）对齐 D01 §3
 * - 仅查看，无编辑功能
 * - 所有角色可查看
 */
import type { ClpmRole } from '#/api/auth';

import { ref } from 'vue';

import { Page } from '@vben/common-ui';

import { Card, Tag, Tooltip } from 'ant-design-vue';

import { CLPM_ROLES, ROLE_LABELS } from '#/api/auth';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { useModules } from '#/composables/use-modules';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

defineOptions({ name: 'SystemPermissions' });

/**
 * 权限级别（对齐 D01 §3）
 * - VIEW：查看（只读访问）
 * - COLLABORATE：协同（查看 + 评论/标记）
 * - EXECUTE：执行（查看 + 操作执行）
 * - MANAGE：管理（全部操作含配置）
 * - SERVICE：服务（系统级运维，最高权限）
 */
type PermissionLevel =
  | 'COLLABORATE'
  | 'EXECUTE'
  | 'MANAGE'
  | 'SERVICE'
  | 'VIEW';

/** 模块定义 */
interface ModuleDef {
  key: string;
  label: string;
  /** 对应后端热插拔模块 key（diagnosis/tuning/handling 为可选模块） */
  moduleKey?: string;
}

const { moduleEnabled } = useModules();

/** 模块列（label 对齐 IA 重构后顶级菜单标题；moduleKey 标记可选模块） */
const MODULES: ModuleDef[] = [
  { key: 'dashboard', label: '监控', moduleKey: 'monitor' },
  { key: 'loop', label: '回路' },
  { key: 'performance', label: '评估', moduleKey: 'assess' },
  { key: 'diagnosis', label: '诊断', moduleKey: 'diagnosis' },
  { key: 'tuning', label: '整定', moduleKey: 'tuning' },
  { key: 'handling', label: '处置', moduleKey: 'handling' },
  { key: 'reports', label: '报告', moduleKey: 'reports' },
  { key: 'config', label: '配置', moduleKey: 'config' },
  { key: 'system', label: '系统', moduleKey: 'system' },
];

/** 列是否因模块禁用而灰显 */
function isColumnDisabled(m: ModuleDef): boolean {
  return !!m.moduleKey && !moduleEnabled(m.moduleKey);
}

/** 权限级别标签映射 */
const permissionLabelMap: Record<PermissionLevel, string> = {
  COLLABORATE: '协同',
  EXECUTE: '执行',
  MANAGE: '管理',
  SERVICE: '服务',
  VIEW: '查看',
};

/** 权限级别颜色映射 */
const permissionColorMap: Record<PermissionLevel, string> = {
  COLLABORATE: 'cyan',
  EXECUTE: 'blue',
  MANAGE: 'green',
  SERVICE: 'purple',
  VIEW: 'default',
};

/** 矩阵行数据 */
interface MatrixRow {
  role: ClpmRole;
  permissions: Record<string, null | PermissionLevel>;
}

/**
 * 权限矩阵（系统预设，对齐 UI/UX §5.2 + D01 §3）
 * 行：角色，列：模块
 * null 表示无权限
 */
const PERMISSION_MATRIX: Record<
  ClpmRole,
  Record<string, null | PermissionLevel>
> = {
  ADMIN: {
    dashboard: 'MANAGE',
    diagnosis: 'MANAGE',
    loop: 'MANAGE',
    performance: 'MANAGE',
    handling: 'MANAGE',
    reports: 'MANAGE',
    config: 'MANAGE',
    system: 'SERVICE',
    tuning: 'MANAGE',
  },
  EXPERT: {
    dashboard: null,
    diagnosis: 'COLLABORATE',
    loop: null,
    performance: null,
    handling: 'VIEW',
    reports: null,
    config: null,
    system: null,
    tuning: 'EXECUTE',
  },
  IC_ENGINEER: {
    dashboard: 'VIEW',
    diagnosis: 'EXECUTE',
    loop: 'MANAGE',
    performance: 'VIEW',
    handling: 'EXECUTE',
    reports: null,
    config: 'VIEW',
    system: null,
    tuning: 'EXECUTE',
  },
  PE_ENGINEER: {
    dashboard: 'VIEW',
    diagnosis: 'VIEW',
    loop: null,
    performance: 'VIEW',
    handling: 'VIEW',
    reports: 'VIEW',
    config: null,
    system: null,
    tuning: null,
  },
  SPONSOR: {
    dashboard: 'VIEW',
    diagnosis: null,
    loop: null,
    performance: 'VIEW',
    handling: null,
    reports: 'VIEW',
    config: null,
    system: null,
    tuning: null,
  },
};

/** 表格数据源 */
const dataSource: MatrixRow[] = CLPM_ROLES.map((role) => ({
  role,
  permissions: PERMISSION_MATRIX[role],
}));

function roleLabel(role: ClpmRole): string {
  return ROLE_LABELS[role] || role;
}

/** P3-40：角色职责说明（hover Tooltip 展示） */
const ROLE_DESCRIPTIONS: Record<ClpmRole, string> = {
  ADMIN: '系统管理员：全模块管理权限，负责系统配置、用户管理、运维监控',
  EXPERT: '工艺专家：诊断协同 + 整定执行，提供专业判断和整定建议',
  IC_ENGINEER: '仪控工程师：回路管理 + 诊断执行 + 整定执行，日常运维主力',
  PE_ENGINEER: '性能工程师：监控/评估/诊断查看，关注KPI趋势和绩效',
  SPONSOR: '赞助者：监控/评估只读，管理评审和决策视角',
};

function roleDescription(role: ClpmRole): string {
  return ROLE_DESCRIPTIONS[role] || '';
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

/** 获取单元格权限级别 */
function getCellPermission(
  row: MatrixRow,
  moduleKey: string,
): null | PermissionLevel {
  const val = row.permissions[moduleKey];
  if (val === undefined || val === null) return null;
  return val;
}

/** 工具栏刷新态（权限矩阵为系统预设静态数据，刷新仅提供视觉反馈） */
const loading = ref(false);

/** 工具栏刷新：权限矩阵为系统预设，刷新仅闪烁反馈 */
function handleRefresh() {
  loading.value = true;
  setTimeout(() => {
    loading.value = false;
  }, 300);
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '权限矩阵 帮助',
    content:
      '权限矩阵页：查看 5 种角色（管理员 / 工艺专家 / 仪控工程师 / 性能工程师 / 赞助者）在 6 大模块（监控 / 回路 / 评估 / 诊断 / 整定 / 系统）中的系统预设权限级别。权限级别从低到高：查看 → 协同 → 执行 → 管理 → 服务。矩阵为系统预设，不可自定义修改；如需调整请联系产品团队评估。',
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
      title="权限矩阵"
      subtitle="查看 5 种角色在 9 大模块中的系统预设权限级别。"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <ClpmDataCanvas class="mt-4" title="权限矩阵">
      <div class="mb-4">
        <p class="text-sm text-gray-500">
          5 种角色 × 9 大模块访问权限矩阵 · 权限级别：查看 / 协同 / 执行 / 管理
          / 服务 · 系统预设，不可自定义修改
        </p>
      </div>

      <!-- 权限矩阵表格（原生 table，避免 Ant Design Table 的 column.key 类型问题） -->
      <div class="overflow-x-auto">
        <table class="w-full border-collapse text-sm">
          <thead>
            <tr class="bg-gray-50">
              <th
                class="border border-gray-200 px-4 py-3 text-center font-medium"
                style="width: 120px"
              >
                角色
              </th>
              <th
                v-for="m in MODULES"
                :key="m.key"
                class="border px-4 py-3 text-center font-medium"
                :class="
                  isColumnDisabled(m)
                    ? 'border-dashed border-gray-200 text-gray-300'
                    : 'border-gray-200'
                "
                style="width: 100px"
              >
                {{ m.label }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in dataSource" :key="row.role">
              <td class="border border-gray-200 px-4 py-3">
                <Tooltip :title="roleDescription(row.role)" placement="right">
                  <Tag :color="roleColor(row.role)">
                    {{ roleLabel(row.role) }}
                  </Tag>
                </Tooltip>
              </td>
              <td
                v-for="m in MODULES"
                :key="m.key"
                class="border px-4 py-3 text-center"
                :class="
                  isColumnDisabled(m)
                    ? 'border-dashed border-gray-200 bg-gray-50/50'
                    : 'border-gray-200'
                "
              >
                <Tag
                  v-if="getCellPermission(row, m.key) && !isColumnDisabled(m)"
                  :color="
                    permissionColorMap[
                      getCellPermission(row, m.key) as PermissionLevel
                    ]
                  "
                >
                  {{
                    permissionLabelMap[
                      getCellPermission(row, m.key) as PermissionLevel
                    ]
                  }}
                </Tag>
                <span v-else class="text-gray-300">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </ClpmDataCanvas>

    <!-- 底部说明 -->
    <Card class="mt-4">
      <div class="flex items-start gap-3">
        <div class="flex-1">
          <strong>权限矩阵说明</strong>
          <ul class="mt-2 list-disc pl-6 text-sm text-gray-500">
            <li>权限矩阵为系统预设，遵循产品化原则，不可自定义修改。</li>
            <li>
              权限级别从低到高：查看（只读）→ 协同（评论/标记）→ 执行（操作）→
              管理（含配置）→ 服务（系统级运维）。
            </li>
            <li>
              系统管理员拥有全部模块权限（服务级）；外部专家仅可访问诊断与整定模块。
            </li>
            <li>如需调整角色权限，请联系产品团队评估后通过版本升级实现。</li>
          </ul>
        </div>
      </div>
    </Card>
  </Page>
</template>
