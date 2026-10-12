"""驾驶舱聚合端点（11 号设计方案 §10，C1 批次）.

- GET /cockpit/overview              — KPI 指标带 + 闭环治理漏斗（一次取齐）
- GET /cockpit/backend-access-roles  — "管理后台"入口允许角色清单（sys_config 配置化）
- GET /cockpit/node-tree             — 工厂→装置→单元三层树 + 各节点回路计数

权限：驾驶舱接口仅要求登录（只读数据，无角色细分）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_scope_ctx, require_perms
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.services.authz_scope import ScopeContext
from app.services.cockpit_overview import (
    build_backend_access_roles,
    build_node_tree,
    build_overview,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cockpit", tags=["cockpit"])


@router.get("/overview", response_model=ApiResponse[dict])
async def get_cockpit_overview(
    window: str = Query("24h", pattern="^(24h|7d|30d)$", description="时间窗口：24h/7d/30d"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_perms("portal:view")),
) -> dict:
    """驾驶舱总览：KPI 指标带（评分/自控率/回路数/劣化/待办/预警）+ 闭环治理漏斗。

    环比口径：当前窗口 vs 上一等长窗口（unit_kpi_summary 加权均值）。
    部分失败容错：单块异常返回空/None，不阻断其余块。
    P4-03 登记残留：全厂聚合口径，NODE_SUBTREE 范围未收窄（聚合服务
    cockpit_overview 不支持 unit 过滤；范围用户暂见全厂口径数字，
    收窄需聚合服务改造，移交后续批裁决）。
    """
    data = await build_overview(db, window=window)
    return success(data=data)


@router.get("/backend-access-roles", response_model=ApiResponse[dict])
async def get_backend_access_roles(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_perms("portal:view")),
) -> dict:
    """ "管理后台"入口允许角色清单（sys_config 键 cockpit.backend_access_roles）。

    逗号分隔角色列表；缺失/为空回退默认 IC_ENGINEER,PE_ENGINEER,ADMIN,EXPERT。
    """
    data = await build_backend_access_roles(db)
    return success(data=data)


@router.get("/node-tree", response_model=ApiResponse[list[dict]])
async def get_node_tree(
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_perms("portal:view")),
    scope: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """工厂→装置→单元三层树 + 各节点回路计数。

    每节点：{id=source_node_id(int), nodeId=plant_node.id, name, type, loopCount,
    children}；loopCount 为 loop_ledger 活跃回路按 unit_id 计数后向上累加。
    P4-03：NODE_SUBTREE 范围仅暴露范围内节点（子树封闭，保留范围内结构）。
    """

    def _filter_tree(nodes: list[dict]) -> list[dict]:
        if scope.allowed_node_ids is None:
            return nodes
        kept: list[dict] = []
        for n in nodes:
            children = _filter_tree(n.get("children") or [])
            if str(n.get("nodeId")) in scope.allowed_node_ids or children:
                node = {**n, "children": children}
                # 范围外祖先仅作路径保留：回路计数按范围内子孙重算
                if str(n.get("nodeId")) not in scope.allowed_node_ids:
                    node["loopCount"] = sum(c.get("loopCount") or 0 for c in children)
                kept.append(node)
        return kept

    data = _filter_tree(await build_node_tree(db))
    return success(data=data)
