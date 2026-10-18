"""授权管理 API schemas（P4-01）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.authz_catalog import VALID_ROLES

VALID_ROLE_LIST = list(VALID_ROLES)


class CatalogItem(BaseModel):
    code: str
    kind: str = Field(description="PAGE_GROUP / PAGE / ACTION")
    parentCode: str | None = None
    requiresCodes: list[str] = Field(default_factory=list)
    moduleKey: str | None = None
    isProtected: bool = False
    description: str | None = None
    sortOrder: int = 0


class CatalogResponse(BaseModel):
    revision: int
    pages: list[CatalogItem] = Field(description="PAGE_GROUP + PAGE")
    actions: list[CatalogItem]
    protectedCodes: list[str]
    roles: list[str]
    note: str


class RoleGrants(BaseModel):
    role: str
    pages: list[str]
    actions: list[str]


class RolesResponse(BaseModel):
    revision: int
    roles: list[RoleGrants]


class EffectiveAccessResponse(BaseModel):
    role: str
    pages: list[str]
    actions: list[str]
    unavailableByModule: dict[str, list[str]]
    note: str


class RoleUpdateRequest(BaseModel):
    expectedRevision: int = Field(description="乐观锁：调用方持有的当前 revision")
    pageCodes: list[str] = Field(default_factory=list)
    actionCodes: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500, description="变更原因（审计必填）")


class RoleUpdateResponse(BaseModel):
    role: str
    revision: int
    pages: list[str]
    actions: list[str]


class ScopeUpdateRequest(BaseModel):
    expectedRevision: int
    scopeType: str = Field(description="ALL / NODE_SUBTREE")
    nodeId: str | None = Field(default=None, description="NODE_SUBTREE 必填（plant_node id）")
    reason: str = Field(min_length=1, max_length=500)


class ScopeUpdateResponse(BaseModel):
    userId: str
    username: str
    revision: int
    scopeType: str
    nodeId: str | None


class UserScopeInfo(BaseModel):
    userId: str
    username: str
    displayName: str | None = None
    role: str
    scopeType: str
    nodeId: str | None = None


class RollbackRequest(BaseModel):
    expectedRevision: int
    reason: str = Field(min_length=1, max_length=500)


class RollbackResponse(BaseModel):
    operation: str = "ROLLBACK"
    revision: int
    rollbackOf: int


class UserScopeView(BaseModel):
    """当前用户数据范围视图（P4-04 /auth/effective-access 下发）。"""

    scopeType: str = Field(description="ALL / NODE_SUBTREE（无显式行按 ALL）")
    nodeId: str | None = Field(default=None, description="NODE_SUBTREE 下的 plant_node id")


class MyEffectiveAccess(BaseModel):
    """GET /auth/effective-access 响应（01§3.5 建议接口，P4-04 在线生效锚点）。"""

    policyRevision: int = Field(description="当前授权策略 revision（前端轮询比对锚）")
    role: str
    pages: list[str] = Field(description="显式授权页面码（菜单可达页口径）")
    actions: list[str] = Field(description="有效动作码（依赖闭包展开；与 require_perms 判定同源）")
    unavailableByModule: dict[str, list[str]] = Field(description="模块禁用下不可用的已授权码")
    scope: UserScopeView
    note: str


class PolicyRevisionInfo(BaseModel):
    revision: int
    operation: str
    role: str | None = None
    userId: str | None = None
    reason: str | None = None
    operator: str
    beforeValue: dict | list | None = None
    afterValue: dict | list | None = None
    rollbackOf: int | None = None
    createdAt: str | None = None


class PolicyRevisionsResponse(BaseModel):
    revision: int
    revisions: list[PolicyRevisionInfo]
