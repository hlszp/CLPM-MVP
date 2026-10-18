"""Loop batch operation schemas (配置增强).

批量配置回路（监控/统计/级别 + P2-04 工程属性 dry-run 预览）+ 批量软删除。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import CamelModel


class LoopBatchUpdates(CamelModel):
    """批量更新字段（至少一个非 None）。

    - isMonitored: 是否监控（is_active=True 表示启用监控）
    - isStatEnabled: 是否纳入统计（当前复用 is_active 语义）
    - importanceLevel: 回路重要等级 1/2/3
    - includeInEvaluation: 是否参与评估
    - P2-04 工程属性：controlActionDirection / opOutputLowerLimit /
      opOutputUpperLimit / dcsModelId / engineeringTemplateId /
      engineeringTemplateVersion（批量语义：None=不更新该字段）

    P1 #10: isMonitored 与 isStatEnabled 共用 is_active 字段，
    同时传值会导致后者覆盖前者（静默数据错误），Schema 层强制互斥。
    """

    is_monitored: bool | None = Field(None, description="是否监控")
    is_stat_enabled: bool | None = Field(None, description="是否纳入统计")
    # v5.3 对齐 DDS v4.1：level → importance_level
    importance_level: int | None = Field(None, ge=1, le=3, description="回路重要等级 1/2/3")
    include_in_evaluation: bool | None = Field(None, description="是否参与评估")
    # P2-04（07 号文 §3.3）工程属性批次
    control_action_direction: str | None = Field(
        None,
        pattern="^(DIRECT|REVERSE|UNKNOWN)$",
        description="控制作用方向：DIRECT/REVERSE/UNKNOWN（未知为显式态）",
    )
    op_output_lower_limit: float | None = Field(None, description="OP 输出下限位")
    op_output_upper_limit: float | None = Field(None, description="OP 输出上限位")
    dcs_model_id: str | None = Field(None, description="关联 DCS 型号 ID")
    engineering_template_id: str | None = Field(None, description="关联工程模板 ID")
    engineering_template_version: int | None = Field(
        None, ge=1, description="固定引用的模板版本号（须同时指定模板）"
    )

    @model_validator(mode="after")
    def check_monitor_stat_exclusive(self) -> LoopBatchUpdates:
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
    - 更新模式：提供 updates 字段（监控/统计/级别/参评 + P2-04 工程属性）
    - 删除模式：action="delete"

    校验：
    - loopIds 不能为空
    - action="delete" 时 updates 必须为 None
    - updates 非 None 时至少包含一个待更新字段
    - dryRun=true 时与 action="delete" 互斥（删除不提供预览）
    """

    # max_length 与其他批量入参对齐（对照 schemas/tag.py 的 BatchWaveformRequest
    # max_length=50）：无上限时单请求可产生 O(N×3) 次 DB 往返 + N 条审计插入。
    loop_ids: list[str] = Field(..., min_length=1, max_length=200, description="回路 ID 列表")
    updates: LoopBatchUpdates | None = Field(None, description="批量更新字段")
    action: str | None = Field(None, pattern="^(delete)$", description="批量动作（delete=软删除）")
    dry_run: bool = Field(
        False,
        description="P2-04 dry-run 预览：只返回逐回路差异/部分失败状态/受影响清单，零写入",
    )
    operation_id: UUID | None = Field(
        None,
        description="工程属性快照批次幂等 ID；同 ID 只能重放同一请求。此模式显式 null=清空",
    )
    expected_state_tokens: dict[str, str] | None = Field(
        None, description="工程属性快照模式执行时携带预览返回的逐回路状态令牌；失配逐条冲突"
    )

    @model_validator(mode="after")
    def check_action_updates_exclusive(self) -> LoopBatchConfigRequest:
        """action 和 updates 互斥；非删除动作必须有 updates。"""
        if self.action == "delete":
            if self.operation_id is not None:
                raise ValueError("工程快照批次不支持删除")
            if self.updates is not None:
                raise ValueError("action=delete 时不能同时提供 updates")
            if self.dry_run:
                raise ValueError("dryRun 预览仅支持更新模式（删除不提供预览）")
            return self
        # 非删除模式必须有 updates
        if self.updates is None:
            raise ValueError("非删除模式必须提供 updates 字段")
        if self.operation_id is not None:
            allowed = {
                "control_action_direction",
                "op_output_lower_limit",
                "op_output_upper_limit",
                "dcs_model_id",
                "engineering_template_id",
                "engineering_template_version",
            }
            supplied = self.updates.model_fields_set
            if not supplied or supplied - allowed:
                raise ValueError("工程快照批次仅支持工程属性字段，至少显式指定一个字段")
            if (
                "control_action_direction" in supplied
                and self.updates.control_action_direction is None
            ):
                raise ValueError("controlActionDirection 不可清空，请使用 UNKNOWN")
            if not self.dry_run and self.expected_state_tokens is None:
                raise ValueError("工程快照执行必须携带预览的 expectedStateTokens")
            return self
        # updates 至少有一个非 None 字段
        if all(
            getattr(self.updates, f) is None
            for f in (
                "is_monitored",
                "is_stat_enabled",
                "importance_level",
                "include_in_evaluation",
                "control_action_direction",
                "op_output_lower_limit",
                "op_output_upper_limit",
                "dcs_model_id",
                "engineering_template_id",
                "engineering_template_version",
            )
        ):
            raise ValueError("updates 至少包含一个待更新字段")
        return self


class LoopBatchChangeItem(CamelModel):
    """dry-run 预览中单个字段的差异。"""

    field: str = Field(..., description="字段名（snake_case，与 updates 键一致）")
    before: object = Field(None, description="变更前值")
    after: object = Field(None, description="变更后值")


class LoopBatchResultItem(CamelModel):
    """dry-run 预览/执行结果中的单回路条目。"""

    loop_id: str = Field(..., description="回路 ID")
    tag_name: str | None = Field(None, description="回路位号")
    status: str = Field(
        ...,
        description="OK=将更新/已更新；UNCHANGED=无差异跳过；FAIL=回路级校验失败；NOT_FOUND=回路不存在",
    )
    changes: list[LoopBatchChangeItem] = Field(default_factory=list, description="字段级差异")
    error: str | None = Field(None, description="失败原因（status=FAIL/NOT_FOUND 时）")
    before_state: dict | None = Field(
        None, description="工程快照模式操作前完整工程属性，含显式 null"
    )
    state_token: str | None = Field(None, description="操作前状态令牌；执行时防止预览后并发修改")


class LoopBatchConfigResult(CamelModel):
    """POST /api/v1/loops/batch-config 响应。"""

    affected: int = Field(..., description="受影响的回路数量（dry-run=将更新数）")
    action: str = Field(..., description="执行的动作：update/delete")
    loop_ids: list[str] = Field(default_factory=list, description="受影响的回路 ID 列表")
    skipped: list[dict] | None = Field(
        None, description="跳过的回路列表（仅 delete 动作，含 loopId/reason）"
    )
    dry_run: bool = Field(False, description="本次是否为 dry-run 预览")
    results: list[LoopBatchResultItem] | None = Field(
        None, description="逐回路结果（update 动作；差异/失败状态可追溯）"
    )
    snapshot_id: str | None = Field(None, description="工程批次恢复引用；仅真实执行持久化")
    replayed: bool = Field(False, description="同 operationId、同请求的幂等重放")


class LoopBatchRestoreRequest(CamelModel):
    """恢复自己的工程属性批次；每批最多 200 条，不接受客户端指定原始值。"""

    reason: str = Field(..., min_length=1, max_length=500)
    dry_run: bool = False
    loop_ids: list[str] | None = Field(None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def non_blank_reason(self) -> LoopBatchRestoreRequest:
        if not self.reason.strip():
            raise ValueError("恢复原因不能为空白")
        return self


__all__ = [
    "LoopBatchChangeItem",
    "LoopBatchConfigRequest",
    "LoopBatchConfigResult",
    "LoopBatchResultItem",
    "LoopBatchUpdates",
]
