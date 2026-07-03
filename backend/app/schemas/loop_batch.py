"""Loop batch operation schemas (配置增强).

批量配置回路（监控/统计/级别）+ 批量软删除。
"""

from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.base import CamelModel


class LoopBatchUpdates(CamelModel):
    """批量更新字段（至少一个非 None）。

    - isMonitored: 是否监控（is_active=True 表示启用监控）
    - isStatEnabled: 是否纳入统计（当前复用 is_active 语义）
    - level: 回路级别 1/2/3

    P1 #10: isMonitored 与 isStatEnabled 共用 is_active 字段，
    同时传值会导致后者覆盖前者（静默数据错误），Schema 层强制互斥。
    """

    is_monitored: bool | None = Field(None, description="是否监控")
    is_stat_enabled: bool | None = Field(None, description="是否纳入统计")
    level: int | None = Field(None, ge=1, le=3, description="回路级别 1/2/3")

    @model_validator(mode="after")
    def check_monitor_stat_exclusive(self) -> "LoopBatchUpdates":
        """P1 #10: is_monitored 与 is_stat_enabled 不能同时更新。

        当前 LoopLedger 无独立 is_stat_enabled 字段，二者都写入 is_active，
        同时传值会导致后者静默覆盖前者。后续若新增独立字段可解除此限制。
        """
        if self.is_monitored is not None and self.is_stat_enabled is not None:
            raise ValueError(
                "isMonitored 与 isStatEnabled 不能同时更新（当前共用 is_active 字段），请分两次调用"
            )
        return self


class LoopBatchConfigRequest(CamelModel):
    """POST /api/v1/loops/batch-config 请求体。

    两种模式（互斥）：
    - 更新模式：提供 updates 字段（isMonitored/isStatEnabled/level）
    - 删除模式：action="delete"

    校验：
    - loopIds 不能为空
    - action="delete" 时 updates 必须为 None
    - updates 非 None 时至少包含一个待更新字段
    """

    loop_ids: list[str] = Field(..., min_length=1, description="回路 ID 列表")
    updates: LoopBatchUpdates | None = Field(None, description="批量更新字段")
    action: str | None = Field(None, pattern="^(delete)$", description="批量动作（delete=软删除）")

    @model_validator(mode="after")
    def check_action_updates_exclusive(self) -> LoopBatchConfigRequest:
        """action 和 updates 互斥；非删除动作必须有 updates。"""
        if self.action == "delete":
            if self.updates is not None:
                raise ValueError("action=delete 时不能同时提供 updates")
            return self
        # 非删除模式必须有 updates
        if self.updates is None:
            raise ValueError("非删除模式必须提供 updates 字段")
        # updates 至少有一个非 None 字段
        if all(
            getattr(self.updates, f) is None for f in ("is_monitored", "is_stat_enabled", "level")
        ):
            raise ValueError("updates 至少包含一个待更新字段")
        return self


class LoopBatchConfigResult(CamelModel):
    """POST /api/v1/loops/batch-config 响应。"""

    affected: int = Field(..., description="受影响的回路数量")
    action: str = Field(..., description="执行的动作：update/delete")
    loop_ids: list[str] = Field(default_factory=list, description="受影响的回路 ID 列表")
    skipped: list[dict] | None = Field(None, description="跳过的回路列表（仅 delete 动作，含 loopId/reason）")


__all__ = [
    "LoopBatchConfigRequest",
    "LoopBatchConfigResult",
    "LoopBatchUpdates",
]
