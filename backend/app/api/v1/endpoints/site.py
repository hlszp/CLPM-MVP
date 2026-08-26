"""站点基础信息接口（IDS v3.2 §2.10 扩展）.

承载客户/部署方基础信息：公司名/LOGO/联系人/授权回路数等。
登录页通过公开接口读取公司简称/全称/封面 LOGO，ADMIN 在系统管理-基础信息
页编辑全部字段。

路由清单：
- GET  /site/basic-info  — 公开免登录，返回登录页所需字段（含封面 LOGO）
- GET  /configs/site     — 登录可读，返回完整字段
- PUT  /configs/site     — 仅 ADMIN，更新基础信息（写审计）
- POST /site/logo        — 仅 ADMIN，上传 LOGO 图片，返回静态 URL
                           query 参数 type=cover|content 区分封面/内容页 LOGO

存储：sys_config 单条 JSON key ``site.basic_info``。
静态资源：上传的 LOGO 落地到 ``app/static/logo/``，经 /static 挂载对外暴露。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.site import (
    LogoUploadResponse,
    SiteBasicInfoPublic,
    SiteConfigResponse,
    SiteConfigUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["site"])

# sys_config 存储键
_KEY_SITE_BASIC_INFO = "site.basic_info"
_KEY_SITE_BASIC_INFO_DESC = "站点基础信息（JSON：公司/联系/授权/部署等）"

# LOGO 上传约束
_LOGO_MAX_BYTES = 2 * 1024 * 1024  # 2MB
_LOGO_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
# __file__ = backend/app/api/v1/endpoints/site.py
# 需要回到 backend/app/（与 main.py 中 StaticFiles 挂载的 app/static 一致），
# 即 .parent × 4：endpoints → v1 → api → app
_LOGO_DIR = Path(__file__).resolve().parents[3] / "static" / "logo"
_LOGO_URL_PREFIX = "/static/logo/"
_READ_CHUNK = 1024 * 1024  # 1MB

# LOGO 类型枚举（cover=封面页横向，content=内容页方形）
_LOGO_TYPES = ("cover", "content")

# 公开字段白名单（仅这几项对未登录访客暴露）
_PUBLIC_FIELDS = ("companyFullName", "companyShortName", "coverLogoUrl")

# 默认值（迁移未灌入或字段缺失时的兜底）
_DEFAULTS: dict[str, Any] = {
    "companyFullName": "致联化工科技有限公司",
    "companyShortName": "致联化工",
    "logoUrl": "",
    "coverLogoUrl": "",
    "contactPerson": "",
    "contactPhone": "",
    "contactEmail": "",
    "address": "",
    "authorizedLoopCount": None,
    "licenseExpireDate": None,
    "systemDeployId": "",
    "systemDeployDate": None,
    "serviceProvider": "",
}


def _now_naive() -> datetime:
    """当前 UTC naive datetime（对齐 ORM 字段无时区）."""
    return datetime.now(UTC).replace(tzinfo=None)


async def _load_site_config(db: AsyncSession) -> dict[str, Any]:
    """读取 site.basic_info JSON，缺失/解析失败回退默认值."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == _KEY_SITE_BASIC_INFO))
    cfg = result.scalar_one_or_none()
    if not cfg or not cfg.value:
        return dict(_DEFAULTS)
    try:
        data = json.loads(cfg.value)
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
    except (ValueError, TypeError):
        return dict(_DEFAULTS)
    # 合并默认值，补齐缺失字段
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in _DEFAULTS})
    return merged


async def _save_site_config(db: AsyncSession, value: dict[str, Any], operator: str) -> None:
    """写入 site.basic_info JSON（upsert）."""
    result = await db.execute(select(SysConfig).where(SysConfig.key == _KEY_SITE_BASIC_INFO))
    cfg = result.scalar_one_or_none()
    now = _now_naive()
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if cfg is None:
        db.add(
            SysConfig(
                key=_KEY_SITE_BASIC_INFO,
                value=raw,
                description=_KEY_SITE_BASIC_INFO_DESC,
                updated_by=operator,
                updated_at=now,
            )
        )
    else:
        cfg.value = raw
        cfg.description = _KEY_SITE_BASIC_INFO_DESC
        cfg.updated_by = operator
        cfg.updated_at = now


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    before_value: str | None,
    after_value: str | None,
) -> None:
    """写入审计日志."""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type="sys_config",
        target_id=_KEY_SITE_BASIC_INFO,
        before_value=before_value,
        after_value=after_value,
        operated_at=_now_naive(),
    )
    db.add(log)


# ---------------------------------------------------------------------------
# GET /site/basic-info — 公开免登录（登录页渲染所需）
# ---------------------------------------------------------------------------


@router.get(
    "/site/basic-info",
    response_model=ApiResponse[SiteBasicInfoPublic],
)
async def get_site_basic_info_public(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """公开基础信息（免登录，仅返回登录页所需字段）.

    登录页 auth.vue 在 onMounted 调用，失败时各字段返回空字符串，
    由前端用兜底默认值渲染，不阻塞登录页加载。
    """
    data = await _load_site_config(db)
    public = SiteBasicInfoPublic(
        companyFullName=data.get("companyFullName", "") or "",
        companyShortName=data.get("companyShortName", "") or "",
        coverLogoUrl=data.get("coverLogoUrl", "") or "",
    )
    return success(data=public.model_dump())


# ---------------------------------------------------------------------------
# GET /configs/site — 登录可读完整基础信息
# ---------------------------------------------------------------------------


@router.get(
    "/configs/site",
    response_model=ApiResponse[SiteConfigResponse],
)
async def get_site_config(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """完整基础信息（登录可读，供基础信息配置页加载）."""
    data = await _load_site_config(db)
    resp = SiteConfigResponse.model_validate(data)
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# PUT /configs/site — ADMIN 更新基础信息
# ---------------------------------------------------------------------------


@router.put(
    "/configs/site",
    response_model=ApiResponse[SiteConfigResponse],
)
async def update_site_config(
    body: SiteConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新基础信息（仅 ADMIN，事务性 + 写审计）."""
    current = await _load_site_config(db)
    before_raw = json.dumps(current, ensure_ascii=False, default=str)

    # 仅覆盖请求中非 None 的字段（部分更新）
    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        if k in current:
            current[k] = v

    after_raw = json.dumps(current, ensure_ascii=False, default=str)
    await _save_site_config(db, current, user.username)
    await _write_audit(
        db=db,
        operator=user.username,
        operation_type="SITE_BASIC_INFO_UPDATE",
        before_value=before_raw,
        after_value=after_raw,
    )

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("更新站点基础信息事务提交失败")
        raise BizError(
            code="ERR_INTERNAL",
            message="事务提交失败，已回滚",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    resp = SiteConfigResponse.model_validate(current)
    logger.info("站点基础信息已更新: operator=%s", user.username)
    return success(data=resp.model_dump(), message="保存成功")


# ---------------------------------------------------------------------------
# POST /site/logo — ADMIN 上传 LOGO 图片
# ---------------------------------------------------------------------------


@router.post(
    "/site/logo",
    response_model=ApiResponse[LogoUploadResponse],
)
async def upload_logo(
    file: UploadFile = File(..., description="LOGO 图片 (png/jpg/svg/webp, ≤2MB)"),
    type: str = Query(
        "content",
        description="LOGO 类型：cover=封面页（横向布局）/ content=内容页（方形）",
    ),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """上传 LOGO 图片，返回静态访问 URL.

    文件落地到 ``app/static/logo/``，经 /static 挂载对外暴露。
    文件名用 uuid 防冲突，保留原扩展名。

    ``type`` 参数仅用于日志记录和前端提示，实际写入哪个字段由前端
    在调用 PUT /configs/site 时指定（logoUrl 或 coverLogoUrl）。
    """
    if type not in _LOGO_TYPES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"type 参数仅支持 {list(_LOGO_TYPES)}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not file.filename:
        raise BizError(
            code="ERR_VALIDATION",
            message="文件名不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ext = Path(file.filename).suffix.lower()
    if ext not in _LOGO_ALLOWED_EXT:
        raise BizError(
            code="ERR_FILE_TYPE",
            message=f"仅支持 {sorted(_LOGO_ALLOWED_EXT)} 格式",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    # 分块读取 + 大小校验
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > _LOGO_MAX_BYTES:
            raise BizError(
                code="ERR_FILE_TOO_LARGE",
                message=f"文件大小超过上限 {_LOGO_MAX_BYTES // (1024 * 1024)}MB",
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        chunks.append(chunk)
    file_bytes = b"".join(chunks)
    if not file_bytes:
        raise BizError(
            code="ERR_VALIDATION",
            message="文件内容为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 落地（cover 加前缀便于区分）
    _LOGO_DIR.mkdir(parents=True, exist_ok=True)
    prefix = "cover_" if type == "cover" else ""
    filename = f"{prefix}{uuid4().hex}{ext}"
    dest = _LOGO_DIR / filename
    dest.write_bytes(file_bytes)

    url = f"{_LOGO_URL_PREFIX}{filename}"
    logger.info(
        "LOGO 上传成功: type=%s, file=%s, size=%d, operator=%s",
        type,
        filename,
        len(file_bytes),
        user.username,
    )
    return success(
        data=LogoUploadResponse(url=url).model_dump(),
        message="LOGO 上传成功",
    )


__all__ = ["router"]
