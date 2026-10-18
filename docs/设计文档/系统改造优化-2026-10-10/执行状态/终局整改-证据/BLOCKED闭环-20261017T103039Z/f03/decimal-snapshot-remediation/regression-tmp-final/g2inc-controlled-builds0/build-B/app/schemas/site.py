"""站点基础信息配置 Schema（IDS v3.2 §2.10 扩展）.

承载客户/部署方的基础信息：公司名/LOGO/联系人/授权回路数等，
登录页通过公开接口读取公司简称/全称/LOGO 渲染，ADMIN 可在
系统管理-基础信息页编辑全部字段。

存储：sys_config 单条 JSON key ``site.basic_info``（见 endpoints/site.py）。

LOGO 字段说明：
- ``logoUrl``：内容页 LOGO，**方形**（建议 128×128 ~ 256×256），
  显示在主布局每个页面左上角侧边栏顶部（VbenLogo 32×32 渲染）。
- ``coverLogoUrl``：封面页 LOGO，**横向布局**（建议 2:1~4:1 宽高比，
  如 240×80），显示在登录页左上角。
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class SiteBasicInfoPublic(BaseModel):
    """公开基础信息（免登录，登录页渲染所需）."""

    companyFullName: str = Field(default="", description="公司全称")
    companyShortName: str = Field(default="", description="公司简称")
    # 登录页左上角展示的横向 LOGO（封面页专用）
    coverLogoUrl: str = Field(default="", description="封面页 LOGO URL（横向布局，登录页用）")


class SiteConfigResponse(BaseModel):
    """完整基础信息（登录后 ADMIN 可读）."""

    companyFullName: str = Field(default="", description="公司全称")
    companyShortName: str = Field(default="", description="公司简称")
    # 内容页 LOGO（方形，主布局左上角）
    logoUrl: str = Field(default="", description="内容页 LOGO URL（方形，主布局左上角）")
    # 封面页 LOGO（横向，登录页左上角）
    coverLogoUrl: str = Field(default="", description="封面页 LOGO URL（横向布局，登录页用）")
    contactPerson: str = Field(default="", description="联系人")
    contactPhone: str = Field(default="", description="联系电话")
    contactEmail: str = Field(default="", description="联系邮箱")
    address: str = Field(default="", description="公司地址")
    authorizedLoopCount: int | None = Field(default=None, description="授权回路数量")
    licenseExpireDate: date | None = Field(default=None, description="授权到期日期")
    systemDeployId: str = Field(default="", description="系统部署编号")
    systemDeployDate: date | None = Field(default=None, description="系统部署日期")
    serviceProvider: str = Field(default="", description="服务提供方")


class SiteConfigUpdateRequest(BaseModel):
    """基础信息更新请求（全字段可选，ADMIN 提交）."""

    companyFullName: str | None = Field(default=None, description="公司全称")
    companyShortName: str | None = Field(default=None, description="公司简称")
    logoUrl: str | None = Field(default=None, description="内容页 LOGO URL（方形）")
    coverLogoUrl: str | None = Field(default=None, description="封面页 LOGO URL（横向）")
    contactPerson: str | None = Field(default=None, description="联系人")
    contactPhone: str | None = Field(default=None, description="联系电话")
    contactEmail: str | None = Field(default=None, description="联系邮箱")
    address: str | None = Field(default=None, description="公司地址")
    authorizedLoopCount: int | None = Field(default=None, description="授权回路数量")
    licenseExpireDate: date | None = Field(default=None, description="授权到期日期")
    systemDeployId: str | None = Field(default=None, description="系统部署编号")
    systemDeployDate: date | None = Field(default=None, description="系统部署日期")
    serviceProvider: str | None = Field(default=None, description="服务提供方")


class LogoUploadResponse(BaseModel):
    """LOGO 上传响应."""

    url: str = Field(description="LOGO 访问 URL（相对路径，前端拼 API base）")


__all__ = [
    "LogoUploadResponse",
    "SiteBasicInfoPublic",
    "SiteConfigResponse",
    "SiteConfigUpdateRequest",
]
