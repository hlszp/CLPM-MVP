"""DCS 配置 schemas（品牌/型号/MODE 定义/映射矩阵）.

对齐 DDS §3.1 / 算法说明 §4.0.3，配置驱动的 DCS 管理接口。

路由清单：
- /api/v1/dcs/vendors          — 品牌 CRUD
- /api/v1/dcs/models           — 型号 CRUD
- /api/v1/dcs/mode-definitions  — 标准 MODE 定义 CRUD
- /api/v1/dcs/mode-mappings     — MODE 映射矩阵 CRUD
- /api/v1/dcs/mode-matrix      — MODE 映射矩阵视图（行=标准 MODE，列=各型号）
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# DcsVendor（DCS 品牌）
# ---------------------------------------------------------------------------


class DcsVendorItem(CamelModel):
    """品牌项（响应）。"""

    id: str
    code: str
    name: str
    name_en: str | None = None
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class DcsVendorCreate(CamelModel):
    """品牌创建请求。"""

    code: str = Field(..., max_length=50, description="品牌代码（唯一）")
    name: str = Field(..., max_length=100, description="品牌中文名")
    name_en: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    sort_order: int = Field(0, ge=0)


class DcsVendorUpdate(CamelModel):
    """品牌更新请求（code 不可改）。"""

    name: str | None = Field(None, max_length=100)
    name_en: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    sort_order: int | None = Field(None, ge=0)
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# DcsModel（DCS 型号，全局唯一 code）
# ---------------------------------------------------------------------------


class DcsModelItem(CamelModel):
    """型号项（响应）。"""

    id: str
    vendor_id: str
    vendor_code: str | None = None
    vendor_name: str | None = None
    code: str
    name: str
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class DcsModelCreate(CamelModel):
    """型号创建请求。"""

    vendor_id: str = Field(..., description="所属品牌 ID")
    code: str = Field(..., max_length=100, description="型号代码（全局唯一）")
    name: str = Field(..., max_length=200)
    description: str | None = Field(None, max_length=500)
    sort_order: int = Field(0, ge=0)


class DcsModelUpdate(CamelModel):
    """型号更新请求（code/vendor_id 不可改）。"""

    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=500)
    sort_order: int | None = Field(None, ge=0)
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# ModeDefinition（标准 MODE 定义）
# ---------------------------------------------------------------------------


class ModeDefinitionItem(CamelModel):
    """标准 MODE 定义项（响应）。"""

    id: str
    standard_mode: int
    label_zh: str
    label_en: str
    is_auto: bool
    color: str
    sort_order: int = 0
    description: str | None = None
    updated_at: str | None = None


class ModeDefinitionUpdate(CamelModel):
    """标准 MODE 定义更新请求（standard_mode 不可改）。"""

    label_zh: str | None = Field(None, max_length=20)
    label_en: str | None = Field(None, max_length=20)
    is_auto: bool | None = None
    color: str | None = Field(None, max_length=20)
    description: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# DcsModeMapping（MODE 映射矩阵项）
# ---------------------------------------------------------------------------


class DcsModeMappingItem(CamelModel):
    """MODE 映射项（响应）。"""

    id: str
    dcs_model_id: str | None = None
    model_code: str | None = None
    model_name: str | None = None
    standard_mode: int
    raw_mode_value: int
    description: str | None = None
    updated_at: str | None = None


class DcsModeMappingCreate(CamelModel):
    """MODE 映射创建请求。"""

    dcs_model_id: str | None = Field(None, description="关联型号 ID；NULL=本系统默认映射")
    standard_mode: int = Field(..., ge=0, le=4, description="标准 MODE 值 0-4")
    raw_mode_value: int = Field(..., description="该型号 DCS 实际 MODE 值")
    description: str | None = Field(None, max_length=500)


class DcsModeMappingUpdate(CamelModel):
    """MODE 映射更新请求。"""

    raw_mode_value: int | None = None
    description: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# 矩阵视图（MODE 映射矩阵）
# ---------------------------------------------------------------------------


class ModeMatrixColumn(CamelModel):
    """矩阵列（一个型号一列）。"""

    model_id: str | None = Field(None, description="型号 ID；NULL=本系统默认列")
    model_code: str | None = Field(None, description="型号 code；本系统默认列为 'default'")
    model_name: str | None = Field(None, description="型号名称；本系统默认列为 '本系统默认'")
    vendor_id: str | None = Field(None, description="所属品牌 ID")
    vendor_name: str | None = Field(None, description="所属品牌名称")
    raw_mode_value: int | None = Field(None, description="该型号的实际 MODE 值；NULL=未配置")


class ModeMatrixRow(CamelModel):
    """矩阵行（一个标准 MODE 一行）。"""

    standard_mode: int
    label_zh: str
    label_en: str
    is_auto: bool
    color: str
    columns: list[ModeMatrixColumn] = Field(..., description="各型号列（第一列为本系统默认）")


class ModeMatrixView(CamelModel):
    """MODE 映射矩阵视图（行=标准 MODE，列=各型号）。"""

    rows: list[ModeMatrixRow]
    columns: list[ModeMatrixColumn] = Field(..., description="列头（第一列为本系统默认）")


# ---------------------------------------------------------------------------
# DcsPidStructure（DCS 型号 PID 结构模板，1:1）
# ---------------------------------------------------------------------------

#: 比例项类型可选值
PType = Literal["PROPORTION", "PROPORTION_BAND"]
#: 时间单位可选值
TimeUnit = Literal["SECONDS", "MINUTES"]


class DcsPidStructureItem(CamelModel):
    """PID 结构模板项（响应）。

    字段名保持 snake_case，CamelModel 自动生成 camelCase 别名（dcsModelId/pType…），
    对齐 DcsModelItem 模式：服务层返回 snake_case dict 可被 populate_by_name 校验通过，
    JSON 序列化输出 camelCase 给前端。
    """

    id: str
    dcs_model_id: str = Field(..., description="关联型号 ID")
    model_code: str | None = None
    model_name: str | None = None
    p_type: PType
    i_unit: TimeUnit
    d_unit: TimeUnit
    d_filter_enabled: bool
    d_filter_unit: TimeUnit | None = None
    d_filter_multiplier: bool
    description: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DcsPidStructureUpsert(CamelModel):
    """PID 结构模板创建/更新请求（按 dcs_model_id 幂等 upsert）。

    d_filter_enabled=True 时 d_filter_unit 必填（与 DB CHECK 约束一致）。
    前端以 camelCase（pType/iUnit…）提交，alias_generator 自动映射。
    """

    p_type: PType = Field("PROPORTION", description="比例项类型")
    i_unit: TimeUnit = Field("SECONDS", description="积分时间单位")
    d_unit: TimeUnit = Field("SECONDS", description="微分时间单位")
    d_filter_enabled: bool = Field(False, description="是否启用微分滤波")
    d_filter_unit: TimeUnit | None = Field(None, description="微分滤波单位（启用时必填）")
    d_filter_multiplier: bool = Field(False, description="微分滤波是否为乘法因子")
    description: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _check_filter_unit(self) -> DcsPidStructureUpsert:
        if self.d_filter_enabled and self.d_filter_unit is None:
            raise ValueError("d_filter_enabled=True 时 d_filter_unit 必填（SECONDS/MINUTES）")
        return self


# ---------------------------------------------------------------------------
# 导入结果（v6.1：品牌/型号 Excel 导入导出）
# ---------------------------------------------------------------------------


class DcsImportError(CamelModel):
    """DCS 品牌/型号导入单行错误。"""

    row: int
    code: str | None = None
    message: str


class DcsImportResult(CamelModel):
    """POST /api/v1/dcs/vendors/import 和 /models/import 响应。"""

    total: int
    inserted: int
    updated: int
    failed: int
    errors: list[DcsImportError] = []


__all__ = [
    "DcsImportError",
    "DcsImportResult",
    "DcsModeMappingCreate",
    "DcsModeMappingItem",
    "DcsModeMappingUpdate",
    "DcsModelCreate",
    "DcsModelItem",
    "DcsModelUpdate",
    "DcsPidStructureItem",
    "DcsPidStructureUpsert",
    "DcsVendorCreate",
    "DcsVendorItem",
    "DcsVendorUpdate",
    "ModeDefinitionItem",
    "ModeDefinitionUpdate",
    "ModeMatrixColumn",
    "ModeMatrixRow",
    "ModeMatrixView",
    "PType",
    "TimeUnit",
]
