/**
 * v-permission 按钮级权限指令
 *
 * 用法：
 *   v-permission="'loop:create'"           — 有 loop:create 权限才显示
 *   v-permission="['loop:create','loop:edit']" — 有任一权限即显示
 *
 * 权限码来源：useAccessStore().accessCodes（由 /auth/me 返回的 permissions 填充）
 * 通配规则：用户拥有 "loop:*" 时，"loop:create" / "loop:edit" 等均通过
 *
 * 与框架内置 v-access:code 的区别：
 * - v-access:code 做精确匹配
 * - v-permission 支持 "*" 通配符，对齐 IDS v3.2 §5.4 权限列表枚举
 */
import type { App, Directive, DirectiveBinding } from 'vue';

import { useAccessStore } from '@vben/stores';

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

function isAccessible(
  el: Element,
  binding: DirectiveBinding<string | string[]>,
) {
  const value = binding.value;
  if (!value) return;

  const accessStore = useAccessStore();
  const userCodesSet = new Set(accessStore.accessCodes);

  const values = Array.isArray(value) ? value : [value];
  // 有任一权限即显示
  const hasAny = values.some((v) => hasPermission(userCodesSet, v));

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
