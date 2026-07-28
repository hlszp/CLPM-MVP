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
 * 隐藏机制（2026-07-28 修复）：
 * - 无权限时用 **Comment 节点占位替换**原元素（保留原元素引用，权限恢复后
 *   在 updated 钩子中重新挂载），替代旧的 `el.remove()` 物理移除；
 * - 旧实现教训（diagnosis/tracker.vue:99-103）：`el.remove()` 会破坏
 *   Dropdown 等依赖子元素引用的组件内部状态，导致菜单无法展开；
 * - **选型建议**：对 Dropdown/Popover 等承载内部状态的交互组件，以及需要
 *   响应式切换的场景，优先使用 `v-if` + useUserStore 角色判断（参考
 *   tracker.vue 的 canEditStatus 模式）；v-permission 适用于纯按钮/链接的
 *   静态显隐。
 */
import type { App, Directive, DirectiveBinding } from 'vue';

import { useAccessStore, useUserStore } from '@vben/stores';

type PermissionValue = string | string[];

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

/**
 * 当前用户是否满足 binding 要求的角色/权限（并集，命中任一即可）
 */
function checkAccessible(binding: DirectiveBinding<PermissionValue>): boolean {
  const value = binding.value;
  if (!value) return true;

  const accessStore = useAccessStore();
  const userCodesSet = new Set(accessStore.accessCodes);
  const userRolesSet = getUserRolesSet();

  const values = Array.isArray(value) ? value : [value];
  // 有任一角色或权限即显示：角色名走精确匹配，权限码走通配匹配
  return values.some(
    (v) => userRolesSet.has(v) || hasPermission(userCodesSet, v),
  );
}

/**
 * 被 Comment 占位替换的元素 → 占位 Comment 节点（保留重新挂载能力）
 */
const placeholderMap = new WeakMap<Element, Comment>();

/**
 * 用 Comment 节点占位替换元素（幂等：已替换时不重复操作）
 */
function detachWithPlaceholder(el: Element, binding: DirectiveBinding) {
  if (placeholderMap.has(el)) return;
  const raw = Array.isArray(binding.value)
    ? binding.value.join(',')
    : binding.value;
  const comment = document.createComment(` v-permission: ${String(raw)} `);
  el.parentNode?.replaceChild(comment, el);
  placeholderMap.set(el, comment);
}

/**
 * 将元素从 Comment 占位处挂载回 DOM（幂等：未替换时无操作）
 */
function attachFromPlaceholder(el: Element) {
  const comment = placeholderMap.get(el);
  if (!comment) return;
  comment.parentNode?.replaceChild(el, comment);
  placeholderMap.delete(el);
}

/**
 * 评估权限并同步 DOM 显隐（mounted / updated 共用）
 */
function syncVisibility(
  el: Element,
  binding: DirectiveBinding<PermissionValue>,
) {
  if (checkAccessible(binding)) {
    attachFromPlaceholder(el);
  } else {
    detachWithPlaceholder(el, binding);
  }
}

const permissionDirective: Directive<HTMLElement, PermissionValue> = {
  mounted: syncVisibility,
  updated: syncVisibility,
  unmounted(el) {
    placeholderMap.delete(el);
  },
};

/**
 * 注册 v-permission 指令
 */
export function registerPermissionDirective(app: App) {
  app.directive('permission', permissionDirective);
}

export { hasPermission, permissionDirective };
