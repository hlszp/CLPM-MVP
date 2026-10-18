"""统一工作查询端点（P6-05 后续分片二 E2，契约 01a §2.1/§3）.

路由清单（/api/v1/me 下，登录主体只来自 ``get_current_user``，无自由
userId 参数；每次查询经 ScopeContext 重新校验数据范围）：

- GET /me/work — 跨来源统一工作查询（分配给我/系统建议/我的关注/
  最近继续四 workKind；等待→今日计划→现在可行动互斥分区；来源失败
  如实 ERROR 不填伪造 0；cursor 稳定分页）

纯查询端点：不新增/修改任何表，不写库。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_scope_ctx
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.personal_work import PersonalWorkData
from app.services.authz_scope import ScopeContext
from app.services.personal_work import query_my_work

router = APIRouter(prefix="/me", tags=["personal-work"])


@router.get("/work", response_model=ApiResponse[PersonalWorkData])
async def get_my_work_endpoint(
    workKind: str | None = Query(
        None,
        description="按工作视图过滤（ASSIGNED/SYSTEM_SUGGESTION/FOLLOW/RESUME；缺省四视图聚合）",
    ),
    partition: str | None = Query(
        None,
        description=(
            "按分区过滤（ACTIONABLE/TODAY/WAITING；未来计划 FUTURE、已结束 CLOSED 仅经本筛选查询）"
        ),
    ),
    cursor: str | None = Query(None, description="上一页 nextCursor（keyset 稳定分页）"),
    pageSize: int = Query(20, ge=1, le=50, description="每页条数（1..50）"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
    ctx: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """跨来源统一工作查询.

    - 分区互斥：先判等待条件 → 再判今日计划 → 其余当前可行动进"现在可
      行动"；工作日期只用于计划/期限，未完工作不按"今天有更新"筛掉；
    - 排序=有效优先级+期限+稳定 ID，计数与分页同规则；
    - 任一来源失败：``sourceStatuses`` 如实 ERROR、``total=None``（不填
      伪造 0）、``readStatus=PARTIAL``，部分数据仍可见；
    - 撤权对象保留但不回显名称/状态/证据，仅计入 ``inaccessibleCount``；
    - 空待办 ≠ 健康：响应不输出健康断言。
    """
    data = await query_my_work(
        db,
        user_id=str(user.id),
        ctx=ctx,
        work_kind=workKind,
        partition=partition,
        cursor=cursor,
        page_size=pageSize,
    )
    return success(data=data)


__all__ = ["router"]
