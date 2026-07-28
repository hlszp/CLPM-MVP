/**
 * v-permission 按钮级权限指令
 *
 * 用法：
 *   v-permission="'loop:create'"              — 有 loop:create 权限才显示
 *   v-permission="['loop:create','loop:edit']" — 有任一权限即显示
 *   v-permission="['ADMIN','IC_ENGINEER']"     — 有任一角色即显示（P2-1 增强）
 *
 * 匹配规则（并集，命中任一即显示）：
 * - 权限码：useAccessStore().accessCodes（由 /auth/me 返回的 permissions 填充）
 * - 角色名：useUserStore().userInfo.roles（ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT/SPONSOR）
 *
 * 角色名与权限码命名空间不重叠（角色为大写枚举无冒号，权限码含冒号），
 * 因此同一 binding 可混用角色名与权限码，二者取并集判断。
 *
 * 通配规则（仅权限码）：用户拥有 "loop:*" 时，"loop:create" / "loop:edit" 等均通过
 *
 * 与框架内置 v-access:code 的区别：
 * - v-access:code 做精确匹配
 * - v-permission 支持 "*" 通配符 + 角色名，对齐 IDS v3.2 §5.4 权限列表枚举
 *
 * 注意：指令通过 el.remove() 物理移除 DOM，非响应式（角色/权限变化后不会自动恢复）。
 * 需要响应式或条件渲染复杂场景时，优先用 v-if + useUserStore 角色判断。
 */
import type { App, Directive, DirectiveBinding } from 'vue';

import { useAccessStore, useUserStore } from '@vben/stores';

/**
 * 判断用户是否拥有指定权限码（支持通配符）
 * @param userCodes 用户拥有的权限码集合
 * @param required  需要的权限码
 */
function hasPermission(userCodes: Set<string>, required: string): boolean {
  // 精确匹配
  if (userCodes.has(required)) return true;
  // 超级管理员通配
  if (userCodes.has('*')) return true;
  // 模块级通配：loop:* 匹配 loop:create
  const parts = required.split(':');
  if (parts.length > 1) {
    for (let i = parts.length - 1; i > 0; i--) {
      const wildcard = `${parts.slice(0, i).join(':')}:*`;
      if (userCodes.has(wildcard)) return true;
    }
  }
  return false;
}

/**
 * 读取当前用户角色集合（容错：userStore 未初始化时返回空集）
 */
function getUserRolesSet(): Set<string> {
  try {
    const userStore = useUserStore();
    return new Set(userStore.userInfo?.roles);
  } catch {
    return new Set();
  }
}

function isAccessible(
  el: Element,
  binding: DirectiveBinding<string | string[]>,
) {
  const value = binding.value;
  if (!value) return;

  const accessStore = useAccessStore();
  const userCodesSet = new Set(accessStore.accessCodes);
  const userRolesSet = getUserRolesSet();

  const values = Array.isArray(value) ? value : [value];
  // 有任一角色或权限即显示：角色名走精确匹配，权限码走通配匹配
  const hasAny = values.some(
    (v) => userRolesSet.has(v) || hasPermission(userCodesSet, v),
  );

  if (!hasAny) {
    el?.remove();
  }
}

const mounted = (el: Element, binding: DirectiveBinding<string | string[]>) => {
  isAccessible(el, binding);
};

const permissionDirective: Directive = {
  mounted,
};

/**
 * 注册 v-permission 指令
 */
export function registerPermissionDirective(app: App) {
  app.directive('permission', permissionDirective);
}

export { hasPermission, permissionDirective };
