"""评估 profile 注册表与发布代际（P3-08a 公共接线，10§3.5/§7.2 + 11§5）.

契约要点：
- profile 分发使用**服务端编译确定的枚举/策略表**，不得接收任意模块路径
  或客户端代码（10§3.5）；本模块即该枚举/策略表的唯一定义点；
- 评估新算法运行必须固定六要素：``assessmentProfile / algorithmVersion /
  dataPolicyVersion / configRevision / buildDigest / releaseGeneration``
  （+ ``schemaVersion`` 快照结构版本），任务开始固定，运行中发布不改变
  旧任务的计算上下文（10§3.5/§7.2）；
- 发布代际经 ``assessment_release_registry`` 登记（上线清单：六要素 +
  消费者最低版本、作用范围及回退方式）；**新版 writer 在兼容 reader 之后
  启用**；回退=激活已登记的旧代际（保留全部记录/输入包，不删证据）；
- 版本选择不另建开关服务——登记表 + 既有配置发布/审计机制，EA-D05
  直接采用（验收后直接启用新 profile，无线上 SHADOW/节点试点前置，
  10§9/§7.2）。

回退纪律（11§5）：回退保留能解释新记录的 reader 或暂停入口；retire 只改
登记状态，不删记录、不盲目 downgrade。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError

#: profile 未注册 / 已注册但算法未接入（planned）的显式拒绝错误码
ERR_ASSESSMENT_PROFILE_UNKNOWN = "ERR_ASSESSMENT_PROFILE_UNKNOWN"
ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE = "ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE"
ERR_RELEASE_GENERATION_NOT_FOUND = "ERR_RELEASE_GENERATION_NOT_FOUND"
ERR_RELEASE_GENERATION_CONFLICT = "ERR_RELEASE_GENERATION_CONFLICT"

RELEASE_STATUS_STAGED = "STAGED"
RELEASE_STATUS_ACTIVE = "ACTIVE"
RELEASE_STATUS_RETIRED = "RETIRED"


class AssessmentProfileId:
    """评估 profile 标识（服务端编译确定；沿用 str 常量而非 enum 以便
    登记表按值校验，未注册值统一走 resolve 显式拒绝）.

    - ``LEGACY_V2``：现行正式口径（KPI_CALC_v2.0，通过既有 golden 的
      legacy 策略；复用共同实现，不复制整套 27 计算器）；
    - ``CANDIDATE_V3_RC1``：10§3.5 推荐候选名 ``KPI_CALC_v3.0-rc1``——
      **算法实现归 P3-09/10**，接入前 planned（选择即显式拒绝，不冒称
      可用）；具体编号在 EA-D05 发布冻结时定稿。
    """

    LEGACY_V2 = "LEGACY_V2"
    CANDIDATE_V3_RC1 = "CANDIDATE_V3_RC1"


#: profile 策略表（服务端编译确定；新增 profile 只加不改）
PROFILES: dict[str, dict[str, Any]] = {
    AssessmentProfileId.LEGACY_V2: {
        "algorithmVersion": "KPI_CALC_v2.0",
        "dataPolicyVersion": "pre_v1",
        "paramsSchemaVersion": "v1",
        "selectable": True,
        "note": "现行正式评估口径（golden 既有基线）",
    },
    AssessmentProfileId.CANDIDATE_V3_RC1: {
        "algorithmVersion": "KPI_CALC_v3.0-rc1",
        "dataPolicyVersion": "pre_v2-rc1",
        "paramsSchemaVersion": "v2-rc1",
        "selectable": False,
        "note": (
            "评估算法改造候选（10 号文 §3/§9）；行为实现归 P3-09/10——"
            "接入前本表登记 selectable=False，选择即显式拒绝（不冒称可用）"
        ),
    },
}


def resolve_assessment_profile(profile_id: str | None = None) -> dict[str, Any]:
    """解析 profile 策略（None → 默认正式 profile；未注册/不可选显式拒绝）.

    Returns:
        ``{"profileId", "algorithmVersion", "dataPolicyVersion",
           "paramsSchemaVersion"}``

    Raises:
        BizError: ERR_ASSESSMENT_PROFILE_UNKNOWN（未注册标识——服务端策略表
            为唯一来源，不接受任意字符串）；ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE
            （已注册但算法未接入，planned 态）。
    """
    pid = profile_id or AssessmentProfileId.LEGACY_V2
    entry = PROFILES.get(pid)
    if entry is None:
        raise BizError(
            code=ERR_ASSESSMENT_PROFILE_UNKNOWN,
            message=(
                f"评估 profile {pid} 未在服务端注册表登记（策略表为唯一来源，"
                f"可用：{sorted(PROFILES)}）"
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not entry.get("selectable"):
        raise BizError(
            code=ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE,
            message=(
                f"评估 profile {pid} 已登记但算法未接入（{entry.get('note')}），"
                "不可选择——P3-09/10 接入后在策略表放开"
            ),
            status_code=status.HTTP_409_CONFLICT,
        )
    return {
        "profileId": pid,
        "algorithmVersion": entry["algorithmVersion"],
        "dataPolicyVersion": entry["dataPolicyVersion"],
        "paramsSchemaVersion": entry["paramsSchemaVersion"],
    }


# ---------------------------------------------------------------------------
# 发布代际（上线清单登记）
# ---------------------------------------------------------------------------


async def get_active_release(db: AsyncSession, profile_id: str) -> dict[str, Any] | None:
    """读取该 profile 当前 ACTIVE 发布代际（无则 None=legacy 未登记口径）.

    返回上线清单六要素 + 登记元信息；同 profile 至多一条 ACTIVE
    （partial unique index 保证，见 models/assessment_release.py）。
    """
    from app.models.assessment_release import AssessmentReleaseRegistry

    result = await db.execute(
        select(AssessmentReleaseRegistry).where(
            AssessmentReleaseRegistry.assessment_profile == profile_id,
            AssessmentReleaseRegistry.status == RELEASE_STATUS_ACTIVE,
        )
    )
    row = result.scalars().first()
    if row is None:
        return None
    return release_row_to_context(row)


def release_row_to_context(row: Any) -> dict[str, Any]:
    """登记行 → 上线清单六要素上下文（任务边界固定，运行中不变）."""
    from app.services.calc_snapshot import executable_build_digest

    # buildDigest 优先取登记值（发布时冻结）；缺省回落当前构建摘要
    # （登记行不伪补构建——离线登记时可能尚未构建）
    build_digest = row.build_digest or executable_build_digest()
    return {
        "assessmentProfile": row.assessment_profile,
        "algorithmVersion": row.algorithm_version,
        "dataPolicyVersion": row.data_policy_version,
        "configRevision": row.config_revision,
        "buildDigest": build_digest,
        "releaseGeneration": row.release_generation,
        "snapshotSchemaVersion": row.snapshot_schema_version,
        "consumerMinVersion": row.consumer_min_version,
        "scope": row.scope,
        "rollbackProfile": row.rollback_profile,
        "releaseStatus": row.status,
    }


async def resolve_task_release_context(
    db: AsyncSession, profile_id: str | None = None
) -> dict[str, Any]:
    """任务边界解析评估发布上下文（六要素；无 ACTIVE 代际时显式 null 回落）.

    回落语义（诚实口径，不伪代际）：登记表无 ACTIVE 行 →
    ``releaseGeneration=None``（legacy 口径=发布代际未登记），其余要素按
    profile 策略表 + 当前构建摘要填实。新任务重读（发布新代际后新任务
    取新值，在跑任务不受影响——调用方在任务边界一次性固定）。
    """
    from app.services.calc_snapshot import SNAPSHOT_SCHEMA_VERSION, executable_build_digest

    profile = resolve_assessment_profile(profile_id)
    active = await get_active_release(db, profile["profileId"])
    base = {
        "assessmentProfile": profile["profileId"],
        "algorithmVersion": profile["algorithmVersion"],
        "dataPolicyVersion": profile["dataPolicyVersion"],
        "paramsSchemaVersion": profile["paramsSchemaVersion"],
    }
    if active is not None:
        # 登记行的 configRevision/buildDigest/releaseGeneration 等优先（发布时冻结）
        return {**base, **active}
    return {
        **base,
        "configRevision": None,  # 由调用方按既有 config_publish pin 口径填
        "buildDigest": executable_build_digest(),
        "releaseGeneration": None,  # 发布代际未登记（legacy 口径，显式 null）
        "snapshotSchemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "consumerMinVersion": None,
        "scope": None,
        "rollbackProfile": None,
        "releaseStatus": "UNREGISTERED",
    }


async def register_release(
    db: AsyncSession,
    *,
    release_generation: str,
    assessment_profile: str,
    algorithm_version: str,
    data_policy_version: str,
    config_revision: str | None,
    snapshot_schema_version: str,
    build_digest: str | None = None,
    consumer_min_version: str | None = None,
    scope: dict | None = None,
    rollback_profile: str | None = None,
    note: str | None = None,
) -> Any:
    """登记一个发布代际（STAGED；激活前不生效）.

    校验：profile 必须在策略表登记；algorithm_version 与策略表一致
    （防登记漂移——只改版本字符串不构成旧版回退，10§3.5）。
    """
    from app.models.assessment_release import AssessmentReleaseRegistry

    entry = PROFILES.get(assessment_profile)
    if entry is None:
        raise BizError(
            code=ERR_ASSESSMENT_PROFILE_UNKNOWN,
            message=f"评估 profile {assessment_profile} 未在服务端注册表登记",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if algorithm_version != entry["algorithmVersion"]:
        raise BizError(
            code=ERR_RELEASE_GENERATION_CONFLICT,
            message=(
                f"登记的 algorithmVersion {algorithm_version} 与策略表"
                f"（{entry['algorithmVersion']}）不一致——版本由统一来源发布，"
                "不允许登记漂移"
            ),
            status_code=status.HTTP_409_CONFLICT,
        )
    row = AssessmentReleaseRegistry(
        release_generation=release_generation,
        assessment_profile=assessment_profile,
        algorithm_version=algorithm_version,
        data_policy_version=data_policy_version,
        config_revision=config_revision,
        snapshot_schema_version=snapshot_schema_version,
        build_digest=build_digest,
        consumer_min_version=consumer_min_version,
        scope=scope,
        rollback_profile=rollback_profile,
        note=note,
        status=RELEASE_STATUS_STAGED,
    )
    db.add(row)
    await db.flush()
    return row


async def activate_release(db: AsyncSession, release_generation: str) -> Any:
    """激活发布代际（EA-D05：必要验收通过后对既定正式范围直接启用）.

    同 profile 原 ACTIVE 行自动 RETIRE（发布代际切换=登记状态迁移，保留
    全部历史行与记录，不删证据）。回退=再次 activate 旧代际。
    """
    from app.models.assessment_release import AssessmentReleaseRegistry

    row = (
        await db.execute(
            select(AssessmentReleaseRegistry).where(
                AssessmentReleaseRegistry.release_generation == release_generation
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise BizError(
            code=ERR_RELEASE_GENERATION_NOT_FOUND,
            message=f"发布代际 {release_generation} 未登记",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    # 不允许跨状态跳跃：RETIRED 可再激活（回退路径），STAGED→ACTIVE 常规
    now = datetime.now(UTC).replace(tzinfo=None)
    current = (
        await db.execute(
            select(AssessmentReleaseRegistry).where(
                AssessmentReleaseRegistry.assessment_profile == row.assessment_profile,
                AssessmentReleaseRegistry.status == RELEASE_STATUS_ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if current is not None and current.id != row.id:
        current.status = RELEASE_STATUS_RETIRED
        current.retired_at = now
    row.status = RELEASE_STATUS_ACTIVE
    row.activated_at = now
    row.retired_at = None
    await db.flush()
    return row


def release_manifest_source_section(release_ctx: dict[str, Any] | None) -> dict[str, Any]:
    """六要素 → 输入包 manifest.source 增量节（只加不改；缺省 null）."""
    ctx = release_ctx or {}
    return {
        "assessmentProfile": ctx.get("assessmentProfile"),
        "releaseGeneration": ctx.get("releaseGeneration"),
        "dataPolicyVersion": ctx.get("dataPolicyVersion"),
    }


__all__ = [
    "ERR_ASSESSMENT_PROFILE_NOT_AVAILABLE",
    "ERR_ASSESSMENT_PROFILE_UNKNOWN",
    "ERR_RELEASE_GENERATION_CONFLICT",
    "ERR_RELEASE_GENERATION_NOT_FOUND",
    "AssessmentProfileId",
    "PROFILES",
    "RELEASE_STATUS_ACTIVE",
    "RELEASE_STATUS_RETIRED",
    "RELEASE_STATUS_STAGED",
    "activate_release",
    "get_active_release",
    "register_release",
    "release_manifest_source_section",
    "release_row_to_context",
    "resolve_assessment_profile",
    "resolve_task_release_context",
]
