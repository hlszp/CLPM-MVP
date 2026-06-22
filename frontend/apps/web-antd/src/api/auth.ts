/**
 * CLPM 认证 API（业务扩展模块）
 *
 * 对齐 IDS v3.2 接口契约。核心认证 API 位于 `#/api/core/auth`，
 * 本模块提供 CLPM 业务侧的类型再导出与便捷方法。
 *
 * 注意：为避免与 `./core/auth` 同名函数冲突，本模块不从此处导出 loginApi 等函数，
 * 使用时请直接 `import from '#/api/core'`。
 */
export type { AuthApi } from './core/auth';

/**
 * CLPM 角色枚举（对齐 IDS v3.2 §5.1 / PRD §3）
 * - ADMIN：系统管理员
 * - IC_ENGINEER：仪控工程师
 * - PE_ENGINEER：工艺/设备工程师
 * - SPONSOR：生产技术/Sponsor
 * - EXPERT：外部专家
 */
export const CLPM_ROLES = [
  'ADMIN',
  'IC_ENGINEER',
  'PE_ENGINEER',
  'SPONSOR',
  'EXPERT',
] as const;

export type ClpmRole = (typeof CLPM_ROLES)[number];

/**
 * 角色默认首页映射（对齐 PRD §3 + UI/UX v4.1 §5.1）
 */
export const ROLE_DEFAULT_HOME: Record<ClpmRole, string> = {
  ADMIN: '/dashboard',
  EXPERT: '/diagnosis/list',
  IC_ENGINEER: '/dashboard',
  PE_ENGINEER: '/dashboard',
  SPONSOR: '/dashboard',
};

/**
 * 角色中文名称映射
 */
export const ROLE_LABELS: Record<ClpmRole, string> = {
  ADMIN: '系统管理员',
  EXPERT: '外部专家',
  IC_ENGINEER: '仪控工程师',
  PE_ENGINEER: '工艺/设备工程师',
  SPONSOR: '生产技术/Sponsor',
};
