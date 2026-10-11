/**
 * CLPM 角色判断 composable（V62-P1-022；P4-02 增权限码判定）
 *
 * 提供可复用的角色判断函数，替代各组件内联的 `userStore.roles.some(...)`
 * 模式，统一管理角色枚举与高级参数可见性策略。
 *
 * 角色枚举（实现契约 v2.3 §5）：
 * - ADMIN：系统管理员，全部可见
 * - IC_ENGINEER：仪表工程师，日常配置与执行，使用默认参数
 * - PE_ENGINEER：工艺工程师，仅汇总视图
 * - EXPERT：控制专家，深度调参与辨识审阅
 * - SPONSOR：发起人，仅汇总视图
 *
 * P4-02 权限码判定（DB 授权矩阵驱动）：
 * - `hasPerm/canAny` 读 `accessStore.accessCodes`（/auth/me 的 permissions，
 *   后端 sys_role_matrix 显式码集合，无通配）——服务端 require_perms 的
 *   前端镜像，按钮显隐与 API 门禁同源；
 * - 角色判断保留作兼容（旧调用方），新代码优先用权限码；
 * - `canEditAdvancedParams` 升级为 `tuning:advanced` 码判定（种子
 *   ADMIN/EXPERT，与原角色数组等价；fitness 门禁仍由服务层执行不放宽）。
 *
 * 选型说明：
 * - 对 Dropdown/Popover 等承载内部状态的交互组件，以及需要响应式切换的场景，
 *   优先使用本 composable + `v-if`（参考 directives/permission.ts 选型建议）；
 * - 纯按钮/链接的静态显隐可继续使用 `v-permission` 指令。
 */
import { computed } from 'vue';

import { useAccessStore, useUserStore } from '@vben/stores';

/** CLPM 角色枚举 */
export type ClpmRole =
  | 'ADMIN'
  | 'EXPERT'
  | 'IC_ENGINEER'
  | 'PE_ENGINEER'
  | 'SPONSOR';

/** 可访问整定模块的角色（实现契约 v2.3 §5） */
const TUNING_ROLES: ClpmRole[] = ['ADMIN', 'IC_ENGINEER', 'EXPERT'];

/** 可编辑高级参数的角色（V62-P1-022） */
const ADVANCED_PARAM_ROLES: ClpmRole[] = ['ADMIN', 'EXPERT'];

/**
 * CLPM 角色判断 composable
 *
 * @example
 * ```ts
 * const { canEditAdvancedParams, hasRole } = useClpmRoles();
 * // v-if="canEditAdvancedParams" 控制高级参数区域可见性
 * ```
 */
export function useClpmRoles() {
  const userStore = useUserStore();
  const accessStore = useAccessStore();

  /** 当前用户角色列表（容错：未登录时为空数组） */
  const roles = computed<string[]>(() => userStore.userInfo?.roles ?? []);

  /** 当前用户动作码集合（P4-02：/auth/me permissions，DB 授权矩阵下发） */
  const accessCodes = computed<string[]>(() => accessStore.accessCodes ?? []);

  /** 是否拥有指定角色 */
  function hasRole(role: string): boolean {
    return roles.value.includes(role);
  }

  /** 是否拥有指定角色中的任意一个（并集判断） */
  function hasAnyRole(required: readonly string[]): boolean {
    return roles.value.some((r) => required.includes(r));
  }

  /**
   * 是否拥有指定权限码（P4-02：全部满足）
   *
   * 与服务端 require_perms 同源（同一 DB 授权矩阵下发），后端放行的操作
   * 前端必可见；后端收紧的入口前端同步隐藏。
   */
  function hasPerm(...codes: string[]): boolean {
    const granted = new Set(accessCodes.value);
    return codes.every((code) => granted.has(code));
  }

  /** 是否拥有任一权限码（并集判断） */
  function canAny(...codes: string[]): boolean {
    const granted = new Set(accessCodes.value);
    return codes.some((code) => granted.has(code));
  }

  /** 是否为系统管理员 */
  const isAdmin = computed(() => hasRole('ADMIN'));

  /** 是否为控制专家 */
  const isExpert = computed(() => hasRole('EXPERT'));

  /** 是否可访问整定模块（ADMIN/IC_ENGINEER/EXPERT） */
  const canAccessTuning = computed(() => hasAnyRole(TUNING_ROLES));

  /**
   * 是否可编辑高级参数（P4-02：tuning:advanced 码判定，种子 ADMIN/EXPERT）
   *
   * IC_ENGINEER 使用默认参数，避免误调 θ 预估、候选阶次或算法微调参数
   * 导致辨识结果不可复现或物理不合理。fitness L0/L1 门禁仍由服务层执行。
   */
  const canEditAdvancedParams = computed(() => hasPerm('tuning:advanced'));

  return {
    accessCodes,
    canAny,
    canAccessTuning,
    canEditAdvancedParams,
    hasAnyRole,
    hasPerm,
    hasRole,
    isAdmin,
    isExpert,
    roles,
  };
}

export { ADVANCED_PARAM_ROLES, TUNING_ROLES };
