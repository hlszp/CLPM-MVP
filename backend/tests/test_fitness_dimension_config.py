"""三性分离（R5，2026-10-03）维度口径配置单元测试。

覆盖：
- derive_dimension_levels：默认映射与现行三模块行为等价 + 配置覆盖
- load_dimension_maps：JSON 解析容错（非法 JSON/未知维度/未知 tag/非法档位忽略）
- GET/PUT /configs/fitness-dimension-maps：合并视图/校验/重置（mock db）
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints import fitness_dimension_config as fdc
from app.core.exceptions import BizError
from app.services.loop_fitness import (
    derive_dimension_levels,
    load_dimension_maps,
)


class TestDeriveDimensionLevels:
    """默认映射等价性（零行为变化迁移）与分离语义。"""

    def test_data_insufficient_is_l0_everywhere(self) -> None:
        d = derive_dimension_levels(["DATA_INSUFFICIENT"])
        assert d == {"assess": "L0", "diagnose": "L0", "tune": "L0"}

    def test_l1_tags_block_all_dimensions(self) -> None:
        for tag in ("MANUAL_DOMINANT", "LOW_AUTO_RATE"):
            d = derive_dimension_levels([tag])
            assert d == {"assess": "L1", "diagnose": "L1", "tune": "L1"}, (tag, d)

    def test_l2_tags_do_not_affect_assess(self) -> None:
        # 分离价值：OP 饱和/SP-PV 偏离不影响「能不能评」
        for tag in ("OP_SATURATED", "SP_PV_DEVIATION"):
            d = derive_dimension_levels([tag])
            assert d["assess"] == "L4"
            assert d["diagnose"] == "L2"
            assert d["tune"] == "L2"

    def test_l3_tags_do_not_affect_assess(self) -> None:
        for tag in ("NO_EXCITATION", "WEAK_RESPONSE"):
            d = derive_dimension_levels([tag])
            assert d["assess"] == "L4"
            assert d["diagnose"] == "L3"
            assert d["tune"] == "L3"

    def test_no_hits_is_l4(self) -> None:
        assert derive_dimension_levels([]) == {
            "assess": "L4",
            "diagnose": "L4",
            "tune": "L4",
        }

    def test_strictest_hit_wins(self) -> None:
        # 手动主导 + OP 饱和 → L1 最严
        d = derive_dimension_levels(["MANUAL_DOMINANT", "OP_SATURATED"])
        assert d["diagnose"] == "L1"

    def test_config_override_removes_tag_and_changes_level(self) -> None:
        maps = load_dimension_maps(
            {
                "fitness.dimension_maps": json.dumps(
                    {"assess": {"LOW_AUTO_RATE": None}, "diagnose": {"OP_SATURATED": "L1"}}
                )
            }
        )
        d = derive_dimension_levels(["LOW_AUTO_RATE"], maps)
        assert d["assess"] == "L4"  # 覆盖移除 → 不再降档
        d2 = derive_dimension_levels(["OP_SATURATED"], maps)
        assert d2["diagnose"] == "L1"  # 覆盖收紧 L2 → L1
        assert d2["tune"] == "L2"  # 未配置维度保持默认


class TestLoadDimensionMaps:
    """配置解析容错。"""

    def test_none_configs_returns_defaults(self) -> None:
        maps = load_dimension_maps(None)
        assert maps["assess"].get("OP_SATURATED") is None

    def test_invalid_json_falls_back(self) -> None:
        maps = load_dimension_maps({"fitness.dimension_maps": "{not json"})
        assert maps["diagnose"]["OP_SATURATED"] == "L2"  # 默认值

    def test_unknown_entries_ignored(self) -> None:
        maps = load_dimension_maps(
            {
                "fitness.dimension_maps": json.dumps(
                    {
                        "nonsense-dim": {"OP_SATURATED": "L1"},
                        "diagnose": {"NOT_A_TAG": "L1", "OP_SATURATED": "L9"},
                    }
                )
            }
        )
        assert maps["diagnose"]["OP_SATURATED"] == "L2"  # 两项均被忽略
        assert "nonsense-dim" not in maps


def _make_db(cfg: MagicMock | None) -> AsyncMock:
    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = cfg
    db.execute = AsyncMock(return_value=exec_result)
    return db


class TestDimensionMapsEndpoint:
    """端点层：合并视图 + 校验 + 重置。"""

    async def test_get_view_shape(self) -> None:
        db = _make_db(None)
        resp = await fdc.get_fitness_dimension_maps(db=db, _=MagicMock())
        data = resp["data"]
        assert [d["key"] for d in data["dimensions"]] == ["assess", "diagnose", "tune"]
        for dim in data["dimensions"]:
            assert len(dim["tags"]) == 6  # 6 个可配置 tag
            for t in dim["tags"]:
                assert t["value"] == t["defaultValue"]  # 无覆盖 → 生效=默认

    async def test_put_rejects_unknown_dimension(self) -> None:
        db = _make_db(None)
        body = fdc.DimensionMapsSaveRequest(maps={"nope": {}})
        try:
            await fdc.save_fitness_dimension_maps(db=db, body=body, user=MagicMock())
            raise AssertionError("应抛 BizError")
        except BizError as e:
            assert e.code == "ERR_FITNESS_DIMENSION_UNKNOWN"

    async def test_put_rejects_data_insufficient(self) -> None:
        db = _make_db(None)
        body = fdc.DimensionMapsSaveRequest(maps={"assess": {"DATA_INSUFFICIENT": "L2"}})
        try:
            await fdc.save_fitness_dimension_maps(db=db, body=body, user=MagicMock())
            raise AssertionError("DATA_INSUFFICIENT 不可配置，应抛 BizError")
        except BizError as e:
            assert e.code == "ERR_FITNESS_DIMENSION_UNKNOWN_TAG"

    async def test_put_rejects_bad_level(self) -> None:
        db = _make_db(None)
        body = fdc.DimensionMapsSaveRequest(maps={"assess": {"OP_SATURATED": "L9"}})
        try:
            await fdc.save_fitness_dimension_maps(db=db, body=body, user=MagicMock())
            raise AssertionError("非法档位应抛 BizError")
        except BizError as e:
            assert e.code == "ERR_FITNESS_DIMENSION_BAD_LEVEL"

    async def test_put_reset_all_deletes_config(self) -> None:
        cfg = MagicMock()
        db = _make_db(cfg)
        body = fdc.DimensionMapsSaveRequest(resetAll=True)
        resp = await fdc.save_fitness_dimension_maps(db=db, body=body, user=MagicMock())
        assert resp["code"] == "0"
        db.delete.assert_awaited_once_with(cfg)
