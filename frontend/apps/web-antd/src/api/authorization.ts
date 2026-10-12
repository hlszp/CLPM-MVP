/**
 * CLPM 授权管理 API（P4-01 落地、P4-04 接通前端消费）
 *
 * 消费 /system/authorization/* 8 端点（ADMIN）与 /auth/effective-access
 * （认证即可，在线生效轮询锚点）。数据库保存驱动真实授权，前端不维护
 * 第二套权限常量——原静态权限矩阵已由本 API 族替换（P4-04）。
 */
import { requestClient } from '#/api/request';

const AUTHORIZATION_BASE = '/system/authorization';

export namespace AuthorizationApi {
  /** 目录条目（页面组/页面/动作码统一形态） */
  export interface CatalogItem {
    code: string;
    description?: null | string;
    isProtected: boolean;
    kind: 'ACTION' | 'PAGE' | 'PAGE_GROUP';
    moduleKey?: null | string;
    parentCode?: null | string;
    requiresCodes: string[];
    sortOrder: number;
  }

  /** GET /system/authorization/catalog */
  export interface CatalogResult {
    actions: CatalogItem[];
    note: string;
    pages: CatalogItem[];
    protectedCodes: string[];
    revision: number;
    roles: string[];
  }

  /** 单角色当前授权（显式码集合） */
  export interface RoleGrants {
    actions: string[];
    pages: string[];
    role: string;
  }

  /** GET /system/authorization/roles */
  export interface RolesResult {
    revision: number;
    roles: RoleGrants[];
  }

  /** GET /system/authorization/roles/{role}（依赖闭包 + 模块标注） */
  export interface EffectiveAccessResult {
    actions: string[];
    note: string;
    pages: string[];
    role: string;
    unavailableByModule: Record<string, string[]>;
  }

  /** PUT /system/authorization/roles/{role} */
  export interface RoleUpdateParams {
    /** 乐观锁：调用方持有的当前 revision（失配 409） */
    expectedRevision: number;
    actionCodes: string[];
    pageCodes: string[];
    /** 变更原因（审计必填，1-500 字符） */
    reason: string;
  }

  export interface RoleUpdateResult {
    actions: string[];
    pages: string[];
    revision: number;
    role: string;
  }

  /** GET /system/authorization/users */
  export interface UserScopeInfo {
    displayName?: null | string;
    nodeId?: null | string;
    role: string;
    scopeType: 'ALL' | 'NODE_SUBTREE';
    userId: string;
    username: string;
  }

  /** PUT /system/authorization/users/{userId}/scope */
  export interface ScopeUpdateParams {
    expectedRevision: number;
    /** NODE_SUBTREE 必填（plant_node id）；ALL 必须为空 */
    nodeId?: null | string;
    reason: string;
    scopeType: 'ALL' | 'NODE_SUBTREE';
  }

  export interface ScopeUpdateResult {
    nodeId?: null | string;
    revision: number;
    scopeType: string;
    userId: string;
    username: string;
  }

  /** 策略版本账本行 */
  export interface PolicyRevisionInfo {
    afterValue?: null | Record<string, unknown> | unknown[];
    beforeValue?: null | Record<string, unknown> | unknown[];
    createdAt?: null | string;
    operation: string;
    operator: string;
    reason?: null | string;
    revision: number;
    rollbackOf?: null | number;
    role?: null | string;
    userId?: null | string;
  }

  /** GET /system/authorization/revisions */
  export interface PolicyRevisionsResult {
    revision: number;
    revisions: PolicyRevisionInfo[];
  }

  /** POST /system/authorization/revisions/{revision}/rollback */
  export interface RollbackParams {
    expectedRevision: number;
    reason: string;
  }

  export interface RollbackResult {
    operation: string;
    revision: number;
    rollbackOf: number;
  }

  /** GET /auth/effective-access（当前登录主体；P4-04 在线生效轮询锚） */
  export interface MyEffectiveAccess {
    actions: string[];
    note: string;
    pages: string[];
    policyRevision: number;
    role: string;
    scope: { nodeId?: null | string; scopeType: 'ALL' | 'NODE_SUBTREE' };
    unavailableByModule: { actions: string[]; pages: string[] };
  }
}

/** 目录（页面组/页面/动作 + 依赖 + 模块标注 + protected 清单） */
export function getAuthorizationCatalogApi() {
  return requestClient.get<AuthorizationApi.CatalogResult>(
    `${AUTHORIZATION_BASE}/catalog`,
  );
}

/** 五角色当前矩阵 + 策略 revision */
export function getAuthorizationRolesApi() {
  return requestClient.get<AuthorizationApi.RolesResult>(
    `${AUTHORIZATION_BASE}/roles`,
  );
}

/** 单角色矩阵 + 有效授权视图（依赖闭包 + 模块禁用标注） */
export function getAuthorizationRoleApi(role: string) {
  return requestClient.get<AuthorizationApi.EffectiveAccessResult>(
    `${AUTHORIZATION_BASE}/roles/${role}`,
  );
}

/** 更新角色矩阵（expectedRevision 409；未知码/漏依赖/管理员保护拒绝） */
export function updateAuthorizationRoleApi(
  role: string,
  data: AuthorizationApi.RoleUpdateParams,
) {
  return requestClient.put<AuthorizationApi.RoleUpdateResult>(
    `${AUTHORIZATION_BASE}/roles/${role}`,
    data,
  );
}

/** 全部用户 scope 列表（无行=按 ALL） */
export function getAuthorizationUsersApi() {
  return requestClient.get<AuthorizationApi.UserScopeInfo[]>(
    `${AUTHORIZATION_BASE}/users`,
  );
}

/** 更新用户数据范围（ALL / NODE_SUBTREE + plant_node 绑定校验） */
export function updateUserScopeApi(
  userId: string,
  data: AuthorizationApi.ScopeUpdateParams,
) {
  return requestClient.put<AuthorizationApi.ScopeUpdateResult>(
    `${AUTHORIZATION_BASE}/users/${userId}/scope`,
    data,
  );
}

/** 策略版本账本（倒序，回退支撑视图） */
export function getAuthorizationRevisionsApi() {
  return requestClient.get<AuthorizationApi.PolicyRevisionsResult>(
    `${AUTHORIZATION_BASE}/revisions`,
  );
}

/** 回退 = 发布逆操作（恢复目标 revision 的 before 快照为新 revision） */
export function rollbackAuthorizationRevisionApi(
  revision: number,
  data: AuthorizationApi.RollbackParams,
) {
  return requestClient.post<AuthorizationApi.RollbackResult>(
    `${AUTHORIZATION_BASE}/revisions/${revision}/rollback`,
    data,
  );
}

/**
 * 当前登录主体有效权限视图（认证即可，非 ADMIN 专属）。
 *
 * - policyRevision：在线生效轮询锚（≤30s 比对，变更即刷新路由/菜单/按钮）；
 * - 解释与实际 403 同源（authz_runtime.get_role_codes，无第二套解释器）。
 */
export function getMyEffectiveAccessApi() {
  return requestClient.get<AuthorizationApi.MyEffectiveAccess>(
    '/auth/effective-access',
  );
}
