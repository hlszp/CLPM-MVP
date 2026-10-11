"""P2-01 单源参数目录与统一校验测试（C09 反例 + 注册表全集不变量）.

覆盖：
- 注册表全集不变量：PARAM_META / _DEFAULTS / PARAM_CATEGORY 三表键一致，
  注册元数据 §3.2 全集字段齐备（远端注册 key 为前后端参数键全集）
- C09 反例（validate_metric_params 纯函数）：未知键/旧键迁移提示/0.5 点数/
  NaN/Inf/数值布尔（双向）/越界/档位颠倒→原子拒绝；合法输入放行
- CFG-05 端点级：PUT /configs/algorithm-params 非法参数 400（不落库）
- CFG-04：validate_metric_threshold 结构校验 + update_metric_config 原子拒绝
- TUN-05：TUNING_METHODS_INFO 注册表校验（schema 层 + tune_pid 服务层）
- CFG-02：GET /configs/diagnosis 返回 methodMeta（readOnly 标注）

设计依据：docs/设计文档/系统改造优化-2026-10-10/ 03 §5 P2-01、04 C09
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import algorithm_config as ac
from app.services.algorithm_config import (
    _DEFAULTS,
    PARAM_CATEGORY,
    PARAM_META,
    validate_metric_params,
    validate_metric_threshold,
)
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 注册表全集不变量（远端注册 key 为前后端参数键全集）
# ---------------------------------------------------------------------------


class TestRegistryInvariants:
    """PARAM_META / _DEFAULTS / PARAM_CATEGORY 三表一致性 + 元数据全集字段."""

    def test_param_meta_covers_all_default_keys(self) -> None:
        """每个指标的 _DEFAULTS 键必须全部在 PARAM_META 注册（防新增键漏登记）."""
        for metric_code, ct_map in _DEFAULTS.items():
            all_keys: set[str] = set()
            for ct_params in ct_map.values():
                all_keys.update(ct_params.keys())
            registered = set(PARAM_META.get(metric_code, {}).keys())
            missing = all_keys - registered
            assert not missing, f"{metric_code} 缺注册键: {missing}"

    def test_param_meta_no_extra_keys_beyond_defaults(self) -> None:
        """PARAM_META 注册键必须有对应默认值（防废弃键滞留注册表）."""
        for metric_code, params in PARAM_META.items():
            in_defaults = metric_code in _DEFAULTS
            assert in_defaults, f"PARAM_META 指标 {metric_code} 不在 _DEFAULTS"
            default_keys: set[str] = set()
            for ct_params in _DEFAULTS[metric_code].values():
                default_keys.update(ct_params.keys())
            extra = set(params.keys()) - default_keys
            assert not extra, f"{metric_code} 注册了无默认值的键: {extra}"

    def test_param_category_covers_all_registered_keys(self) -> None:
        """PARAM_CATEGORY 分组覆盖全部注册键（分组展示不漏键）."""
        for metric_code, params in PARAM_META.items():
            cats = PARAM_CATEGORY.get(metric_code, {})
            missing = set(params.keys()) - set(cats.keys())
            assert not missing, f"{metric_code} 缺分组键: {missing}"

    def test_registry_metadata_full_set_fields(self) -> None:
        """方案 §3.2 注册元数据全集字段齐备（build_param_meta 下发视图）."""
        required_fields = {
            "label",
            "description",
            "type",
            "unit",
            "min",
            "max",
            "default",
            "defaultBasis",
            "category",
            "kind",
            "risk",
            "riskNote",
            "scope",
            "maintainRoles",
            "finite",
            "applicableControlTypes",
        }
        base_fields = required_fields - {"min", "max"}
        for metric_code in PARAM_META:
            meta_view = ac.build_param_meta(metric_code)
            assert meta_view, f"{metric_code} 元数据视图为空"
            for key, entry in meta_view.items():
                missing = base_fields - set(entry.keys())
                assert not missing, f"{metric_code}.{key} 缺元数据字段: {missing}"
                assert entry["type"] in ("bool", "int", "float")
                assert entry["kind"] in ("business", "internal")
                assert entry["risk"] in ("LOW", "MEDIUM", "HIGH")
                assert entry["maintainRoles"] == ["ADMIN"]  # DEC-03
                if entry["type"] in ("int", "float"):
                    # 数值型：有限数标记 + 合法范围必填
                    assert entry["finite"] is True
                    assert "min" in entry and "max" in entry, (
                        f"{metric_code}.{key} 数值型缺 min/max"
                    )
                else:
                    assert entry["finite"] is False

    def test_int_typed_params_declared(self) -> None:
        """点数/个数类参数必须声明 int 类型（供 0.5 反例校验）."""
        int_expected = {
            ("oscillation_rate", "min_zero_crossings"),
            ("oscillation_rate", "min_half_period_samples"),
            ("oscillation_rate", "sp_tracking_window"),
            ("fast_rate", "recovery_persistence"),
            ("stability_rate", "sp_tracking_window"),
        }
        for metric_code, key in int_expected:
            assert PARAM_META[metric_code][key].get("type") == "int", f"{metric_code}.{key}"

    def test_defaults_within_declared_ranges(self) -> None:
        """冻结默认值必须落在注册范围内（默认与范围同源自洽）."""
        for metric_code, params in PARAM_META.items():
            defaults = _DEFAULTS.get(metric_code, {}).get("STABLE", {})
            for key, m in params.items():
                if key not in defaults:
                    continue
                value = defaults[key]
                if m.get("type") == "bool":
                    continue
                assert "min" not in m or value >= m["min"], (
                    f"{metric_code}.{key} 默认 {value} 低于下限 {m.get('min')}"
                )
                assert "max" not in m or value <= m["max"], (
                    f"{metric_code}.{key} 默认 {value} 超过上限 {m.get('max')}"
                )

    def test_deprecated_keys_not_in_registry(self) -> None:
        """废弃键不得再留在注册表（旧键只进迁移提示）."""
        for metric_code, key in ac._DEPRECATED_PARAM_KEYS:
            assert key not in PARAM_META.get(metric_code, {})


# ---------------------------------------------------------------------------
# C09 反例：validate_metric_params（CFG-05 纯函数）
# ---------------------------------------------------------------------------


class TestValidateMetricParamsC09:
    """C09：未知键/旧键/0.5 点数/NaN/Inf/数值布尔/越界/档位颠倒原子拒绝."""

    def test_valid_params_pass(self) -> None:
        """合法输入放行（兼容红线：合法输入形状不变）."""
        assert (
            validate_metric_params(
                "oscillation_rate",
                {"similarity_threshold": 0.55, "min_ratio": 0.05, "max_ratio": 15.0},
            )
            == []
        )
        assert validate_metric_params("fast_rate", {"settling_tolerance": 0.0}) == []
        assert validate_metric_params("stability_rate", {"band_in_score_enabled": True}) == []

    def test_unknown_metric_rejected(self) -> None:
        assert validate_metric_params("no_such_metric", {}) == ["未知指标代码: no_such_metric"]

    def test_unknown_key_rejected(self) -> None:
        errors = validate_metric_params("oscillation_rate", {"similarity_threshold": 0.5, "foo": 1})
        assert errors == ["未知参数键: foo"]

    def test_deprecated_key_rejected_with_migration_hint(self) -> None:
        """旧键 e_max_percentile 拒绝并给出迁移提示（CFG-01 后端口径）."""
        errors = validate_metric_params("accuracy_rate", {"e_max_percentile": 90})
        assert len(errors) == 1
        assert "已废弃参数键: e_max_percentile" in errors[0]
        assert "e_max_tolerance_ratio" in errors[0]

    def test_half_point_count_rejected(self) -> None:
        """0.5 点数拒绝（int 参数收非整数）."""
        errors = validate_metric_params("oscillation_rate", {"sp_tracking_window": 0.5})
        assert errors == ["参数 sp_tracking_window 应为整数（点数/个数），收到 0.5"]
        errors = validate_metric_params("fast_rate", {"recovery_persistence": 2.5})
        assert any("应为整数" in e for e in errors)

    def test_nan_rejected(self) -> None:
        """NaN 拒绝（两比较均 False 的旧漏洞）."""
        errors = validate_metric_params("fast_rate", {"settling_tolerance": float("nan")})
        assert errors == ["参数 settling_tolerance=nan 不是有限数（NaN/Inf 拒绝）"]
        errors = validate_metric_params(
            "effective_auto_rate", {"default_e_max_ratio": float("nan")}
        )
        assert any("不是有限数" in e for e in errors)

    def test_inf_rejected(self) -> None:
        errors = validate_metric_params("saturation_rate", {"saturation_epsilon": float("inf")})
        assert errors == ["参数 saturation_epsilon=inf 不是有限数（NaN/Inf 拒绝）"]
        errors = validate_metric_params("saturation_rate", {"saturation_epsilon": float("-inf")})
        assert any("不是有限数" in e for e in errors)

    def test_bool_for_numeric_rejected(self) -> None:
        """数值位收布尔拒绝（数值布尔正向）."""
        errors = validate_metric_params("fast_rate", {"ideal_settling_ratio": True})
        assert errors == ["参数 ideal_settling_ratio 应为数值，收到布尔值"]

    def test_numeric_for_bool_rejected(self) -> None:
        """布尔位收数值 0/1 拒绝（数值布尔反向，旧实现漏判）."""
        errors = validate_metric_params("stability_rate", {"band_in_score_enabled": 1})
        assert len(errors) == 1
        assert "应为布尔值" in errors[0]
        errors = validate_metric_params("oscillation_rate", {"sp_step_exclusion_enabled": 0})
        assert any("应为布尔值" in e for e in errors)

    def test_string_value_rejected(self) -> None:
        errors = validate_metric_params("fast_rate", {"ideal_settling_ratio": "1.0"})
        assert any("应为数值" in e for e in errors)

    def test_out_of_range_rejected(self) -> None:
        errors = validate_metric_params("output_trip_index", {"trip_normal": 5.0})
        assert errors == ["参数 trip_normal=5.0 超过上限 1.0"]
        errors = validate_metric_params("oscillation_rate", {"similarity_threshold": 0.05})
        assert errors == ["参数 similarity_threshold=0.05 低于下限 0.1"]

    def test_tier_inversion_rejected_same_dict(self) -> None:
        """档位颠倒原子拒绝（同行程边界键值均在范围内但次序颠倒）."""
        errors = validate_metric_params(
            "output_trip_index",
            {"trip_inactive": 0.06, "trip_normal": 0.05, "trip_frequent": 1.0},
        )
        assert len(errors) == 1
        assert "trip_inactive=0.06 必须小于 trip_normal=0.05" in errors[0]

    def test_tier_inversion_via_base_merge_rejected(self) -> None:
        """部分覆盖单值合法、与存量合并后档位颠倒 → 拒绝（base 合并求值）."""
        # 存量 trip_inactive=0.06，本次单写 trip_normal=0.05（自身在范围内）
        errors = validate_metric_params(
            "output_trip_index",
            {"trip_normal": 0.05},
            base={"trip_inactive": 0.06, "trip_normal": 0.1, "trip_frequent": 1.0},
        )
        assert len(errors) == 1
        assert "组合约束" in errors[0]

    def test_tier_inversion_normal_vs_frequent(self) -> None:
        errors = validate_metric_params(
            "output_trip_index",
            {"trip_normal": 0.5, "trip_frequent": 0.2},
        )
        assert any("trip_normal=0.5 必须小于 trip_frequent=0.2" in e for e in errors)

    def test_fast_rate_threshold_factor_combo(self) -> None:
        """fast_rate：settling_tolerance 与 ideal_settling_ratio 组合（阈值因子为正）.

        当前范围内因子恒正（防御性约束）；构造 base 中越界值验证规则可触发。
        """
        errors = validate_metric_params(
            "fast_rate",
            {"ideal_settling_ratio": 1.0},
            base={"ideal_settling_ratio": 1.0, "settling_tolerance": -1.5},
        )
        assert len(errors) == 1
        assert "必须为正" in errors[0]

    def test_valid_trip_tiers_pass(self) -> None:
        assert (
            validate_metric_params(
                "output_trip_index",
                {"trip_inactive": 0.01, "trip_normal": 0.1, "trip_frequent": 1.0},
            )
            == []
        )

    def test_combo_skipped_when_keys_missing(self) -> None:
        """部分键缺失时组合约束跳过（不误伤部分覆盖写入）."""
        assert validate_metric_params("output_trip_index", {"trip_normal": 0.2}) == []
        assert validate_metric_params("oscillation_rate", {"min_ratio": 0.05}) == []


# ---------------------------------------------------------------------------
# CFG-05 端点级：PUT /configs/algorithm-params 非法参数原子拒绝
# ---------------------------------------------------------------------------


def _make_scalar_none_result() -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


def _make_scalars_all_result(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_all_result(items: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = items
    return result


class TestAlgorithmParamsPutValidation:
    """PUT /configs/algorithm-params/{metric}：C09 反例 400 + 合法保存不变."""

    def _put(self, client, metric: str, params: dict):
        with mock_current_user(TEST_USERS["admin"]):
            return client.put(
                f"/api/v1/configs/algorithm-params/{metric}",
                json={"items": [{"controlType": "STABLE", "params": params}]},
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_put_nan_rejected_400_no_commit(self, client, mock_db, fake_redis) -> None:
        """NaN 参数 400 ERR_PARAM_INVALID，未写库未提交（原子拒绝）.

        经 content= 发送裸 NaN 字面量：Python json.loads 宽松解析接受该 token
        （JSON 规范外但 CPython 解析器放行），复现 NaN 绕过两比较均为 False
        的旧漏洞路径。
        """
        mock_db.add = MagicMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/configs/algorithm-params/fast_rate",
                content=(
                    '{"items": [{"controlType": "STABLE", "params": {"settling_tolerance": NaN}}]}'
                ),
                headers={"Authorization": "Bearer fake-token", "Content-Type": "application/json"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM_INVALID"
        assert "不是有限数" in resp.json()["message"]
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_awaited()

    def test_put_unknown_key_rejected_400(self, client, mock_db, fake_redis) -> None:
        mock_db.add = MagicMock()
        resp = self._put(client, "oscillation_rate", {"similarity_threshold": 0.5, "foo": 1})
        assert resp.status_code == 400
        assert "未知参数键: foo" in resp.json()["message"]
        mock_db.commit.assert_not_awaited()

    def test_put_deprecated_key_hints_migration(self, client, mock_db, fake_redis) -> None:
        mock_db.add = MagicMock()
        resp = self._put(client, "accuracy_rate", {"e_max_percentile": 90})
        assert resp.status_code == 400
        message = resp.json()["message"]
        assert "已废弃参数键: e_max_percentile" in message
        assert "e_max_tolerance_ratio" in message

    def test_put_half_point_count_rejected_400(self, client, mock_db, fake_redis) -> None:
        resp = self._put(client, "oscillation_rate", {"sp_tracking_window": 0.5})
        assert resp.status_code == 400
        assert "应为整数" in resp.json()["message"]

    def test_put_bool_for_numeric_rejected_400(self, client, mock_db, fake_redis) -> None:
        resp = self._put(client, "fast_rate", {"ideal_settling_ratio": True})
        assert resp.status_code == 400
        assert "收到布尔值" in resp.json()["message"]

    def test_put_numeric_for_bool_rejected_400(self, client, mock_db, fake_redis) -> None:
        resp = self._put(client, "stability_rate", {"band_in_score_enabled": 1})
        assert resp.status_code == 400
        assert "应为布尔值" in resp.json()["message"]

    def test_put_out_of_range_rejected_400(self, client, mock_db, fake_redis) -> None:
        resp = self._put(client, "output_trip_index", {"trip_normal": 5.0})
        assert resp.status_code == 400
        assert "超过上限" in resp.json()["message"]

    def test_put_tier_inversion_rejected_400(self, client, mock_db, fake_redis) -> None:
        resp = self._put(
            client,
            "output_trip_index",
            {"trip_inactive": 0.06, "trip_normal": 0.05, "trip_frequent": 1.0},
        )
        assert resp.status_code == 400
        assert "档位不可颠倒" in resp.json()["message"]

    def test_put_valid_still_saves(self, client, mock_db, fake_redis) -> None:
        """合法参数保存成功（兼容不变：合法输入形状不变）."""
        saved_row = MagicMock()
        saved_row.metric_code = "oscillation_rate"
        saved_row.control_type = "STABLE"
        saved_row.params = {"similarity_threshold": 0.55}
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_scalar_none_result(),
                _make_scalars_all_result([saved_row]),
                _make_all_result([]),
            ]
        )
        mock_db.add = MagicMock()
        resp = self._put(client, "oscillation_rate", {"similarity_threshold": 0.55})
        assert resp.status_code == 200
        mock_db.commit.assert_awaited_once()

    def test_put_inversion_via_existing_record_rejected(self, client, mock_db, fake_redis) -> None:
        """存量 trip_inactive=0.06 + 本次单写 trip_normal=0.05 → 合并后颠倒拒绝."""
        existing = MagicMock()
        existing.params = {"trip_inactive": 0.06, "trip_normal": 0.1, "trip_frequent": 1.0}
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = existing
        mock_db.execute = AsyncMock(return_value=existing_result)
        mock_db.add = MagicMock()
        resp = self._put(client, "output_trip_index", {"trip_normal": 0.05})
        assert resp.status_code == 400
        assert "组合约束" in resp.json()["message"]
        # 原子拒绝：不合并、不提交
        assert existing.params["trip_normal"] == 0.1
        mock_db.commit.assert_not_awaited()

    def test_get_all_returns_full_metric_name_set(self, client, mock_db, fake_redis) -> None:
        """GET 全量视图 8 指标中文名齐备（含 stability_rate/saturation_rate）."""
        mock_db.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/algorithm-params",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        metrics = resp.json()["data"]["metrics"]
        names = {m["metricCode"]: m["metricName"] for m in metrics}
        assert names["stability_rate"] == "稳定率"
        assert names["saturation_rate"] == "饱和率"


# ---------------------------------------------------------------------------
# CFG-04：metric_config.threshold 结构校验
# ---------------------------------------------------------------------------


class TestValidateMetricThreshold:
    """validate_metric_threshold：注册表指标严格校验 + 展示类指标宽松校验."""

    def test_registry_metric_strict_unknown_key(self) -> None:
        errors = validate_metric_threshold("accuracy_rate", {"e_max_percentile": 90})
        assert any("已废弃参数键" in e for e in errors)

    def test_registry_metric_strict_nan(self) -> None:
        errors = validate_metric_threshold("saturation_rate", {"saturation_epsilon": math.nan})
        assert any("不是有限数" in e for e in errors)

    def test_registry_metric_valid_passes(self) -> None:
        assert validate_metric_threshold("fast_rate", {"settling_tolerance": 0.05}) == []

    def test_legacy_display_threshold_passes(self) -> None:
        """展示类 KPI 阈值（种子形态 min/max/alert 字符串）兼容放行."""
        assert (
            validate_metric_threshold(
                "GOOD_VALUE_RATE", {"min": 80, "max": 100, "alert": "warning"}
            )
            == []
        )

    def test_legacy_nested_dict_rejected(self) -> None:
        errors = validate_metric_threshold("GOOD_VALUE_RATE", {"min": {"deep": 1}})
        assert any("值类型非法" in e for e in errors)

    def test_legacy_list_rejected(self) -> None:
        errors = validate_metric_threshold("GOOD_VALUE_RATE", {"max": [1, 2]})
        assert any("值类型非法" in e for e in errors)

    def test_legacy_nan_rejected(self) -> None:
        errors = validate_metric_threshold("GOOD_VALUE_RATE", {"min": math.nan})
        assert any("不是有限数" in e for e in errors)

    def test_legacy_empty_string_rejected(self) -> None:
        errors = validate_metric_threshold("GOOD_VALUE_RATE", {"alert": "  "})
        assert any("空字符串" in e for e in errors)


class TestUpdateMetricConfigThresholdGuard:
    """update_metric_config：坏结构 threshold 原子拒绝（写库前）."""

    @staticmethod
    def _db_with_config(metric_code: str) -> AsyncMock:
        from tests.test_performance import _make_metric_config

        config = _make_metric_config(metric_code=metric_code)
        db = AsyncMock()
        found = MagicMock()
        found.scalar_one_or_none.return_value = config
        db.execute = AsyncMock(return_value=found)
        db.add = MagicMock()
        return db, config

    async def test_registry_metric_bad_threshold_rejected(self) -> None:
        from app.core.exceptions import BizError
        from app.services.performance import update_metric_config

        db, config = self._db_with_config("fast_rate")
        with pytest.raises(BizError) as exc_info:
            await update_metric_config(
                db, config.id, "admin", threshold={"settling_tolerance": math.nan}
            )
        assert exc_info.value.code == "ERR_PARAM_INVALID"
        # 原子拒绝：threshold 未变更、无审计写入、未提交
        assert config.threshold != {"settling_tolerance": math.nan}
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_registry_metric_unknown_threshold_key_rejected(self) -> None:
        from app.core.exceptions import BizError
        from app.services.performance import update_metric_config

        db, config = self._db_with_config("accuracy_rate")
        with pytest.raises(BizError) as exc_info:
            await update_metric_config(db, config.id, "admin", threshold={"e_max_percentile": 90})
        assert exc_info.value.code == "ERR_PARAM_INVALID"
        assert "e_max_tolerance_ratio" in exc_info.value.message

    async def test_legacy_display_bad_structure_rejected(self) -> None:
        from app.core.exceptions import BizError
        from app.services.performance import update_metric_config

        db, config = self._db_with_config("GOOD_VALUE_RATE")
        with pytest.raises(BizError) as exc_info:
            await update_metric_config(db, config.id, "admin", threshold={"min": {"a": 1}})
        assert exc_info.value.code == "ERR_PARAM_INVALID"

    async def test_legacy_display_seed_shape_accepted(self) -> None:
        """展示类阈值种子形状可通过（兼容红线）."""
        from app.services.performance import update_metric_config

        db, config = self._db_with_config("GOOD_VALUE_RATE")
        result = await update_metric_config(
            db,
            config.id,
            "admin",
            threshold={"min": 85, "max": 100, "alert": "warning"},
        )
        assert result["threshold"] == {"min": 85, "max": 100, "alert": "warning"}
        db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# TUN-05：整定参数契约校验
# ---------------------------------------------------------------------------


class TestTuningParamsContract:
    """TUNING_METHODS_INFO 注册表校验：未知键/类型/范围/非法枚举拒绝."""

    def test_valid_params_pass(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        assert validate_tuning_params("IMC", {"lambdaRatio": 1.5}) == []
        assert validate_tuning_params("ZN", {"controllerType": "PI"}) == []
        assert validate_tuning_params("SIMC", {"tauCRatio": 0.5}) == []

    def test_unknown_key_rejected(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        errors = validate_tuning_params("IMC", {"lambdaRatio": 1.0, "foo": 2})
        assert len(errors) == 1
        assert "未知算法参数键: foo" in errors[0]

    def test_invalid_enum_rejected(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        errors = validate_tuning_params("ZN", {"controllerType": "XYZ"})
        assert errors == ["参数 controllerType=XYZ 非法，合法值: ['P', 'PI', 'PID']"]

    def test_enum_numeric_rejected(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        errors = validate_tuning_params("COHEN_COON", {"controllerType": 3})
        assert any("应为字符串枚举" in e for e in errors)

    def test_out_of_range_rejected(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        assert validate_tuning_params("SIMC", {"tauCRatio": 9.0}) == [
            "参数 tauCRatio=9.0 超过上限 5.0"
        ]
        assert validate_tuning_params("LAMBDA", {"lambdaRatio": 0.01}) == [
            "参数 lambdaRatio=0.01 低于下限 0.1"
        ]

    def test_nan_inf_bool_rejected(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        assert any(
            "不是有限数" in e for e in validate_tuning_params("IMC", {"lambdaRatio": math.nan})
        )
        assert any(
            "不是有限数" in e for e in validate_tuning_params("IMC", {"lambdaRatio": math.inf})
        )
        assert validate_tuning_params("IMC", {"lambdaRatio": True}) == [
            "参数 lambdaRatio 应为数值，收到布尔值"
        ]

    def test_string_value_rejected(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        assert any("应为数值" in e for e in validate_tuning_params("SIMC", {"tauCRatio": "0.5"}))

    def test_unknown_algorithm_rejected(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        errors = validate_tuning_params("NOT_AN_ALGO", {})
        assert errors == ["不支持的整定算法: NOT_AN_ALGO"]

    def test_union_mode_accepts_cross_algorithm_keys(self) -> None:
        """矩阵联合模式：跨算法键合法（lambdaRatio+controllerType+tauCRatio 共存）."""
        from app.services.tuning_algorithms import validate_tuning_params

        assert (
            validate_tuning_params(
                None, {"lambdaRatio": 1.2, "controllerType": "PI", "tauCRatio": 0.5}
            )
            == []
        )

    def test_union_mode_rejects_unknown_and_bad_values(self) -> None:
        from app.services.tuning_algorithms import validate_tuning_params

        assert any("未知算法参数键" in e for e in validate_tuning_params(None, {"foo": 1}))
        assert any("非法" in e for e in validate_tuning_params(None, {"controllerType": "X"}))


class TestTuneRequestSchemaContract:
    """schema 层：TuneRequest/TuneMatrixRequest algorithmParams 原子拒绝（422）."""

    _model = {"K": 1.0, "tau": 30.0, "theta": 5.0}

    def _make(self, **overrides):
        from app.schemas.tuning import TuneRequest

        payload = {
            "modelType": "FOPDT",
            "modelParams": self._model,
            "algorithm": "IMC",
        }
        payload.update(overrides)
        return TuneRequest(**payload)

    def test_valid_passes(self) -> None:
        req = self._make(algorithmParams={"lambdaRatio": 1.5})
        assert req.algorithmParams == {"lambdaRatio": 1.5}

    def test_unknown_key_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="未知算法参数键: foo"):
            self._make(algorithmParams={"foo": 1})

    def test_out_of_range_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="低于下限"):
            self._make(algorithmParams={"lambdaRatio": 0.05})

    def test_nan_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="不是有限数"):
            self._make(algorithmParams={"lambdaRatio": math.nan})

    def test_invalid_controller_type_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="非法"):
            self._make(algorithm="ZN", algorithmParams={"controllerType": "XYZ"})

    def test_cross_algorithm_key_rejected_for_single_tune(self) -> None:
        """单算法 /tune：他算法专属键视为未知键（严格口径）."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="未知算法参数键: tauCRatio"):
            self._make(algorithm="IMC", algorithmParams={"tauCRatio": 1.0})

    def test_matrix_union_accepts_cross_keys(self) -> None:
        from app.schemas.tuning import TuneMatrixRequest

        req = TuneMatrixRequest(
            modelType="FOPDT",
            modelParams=self._model,
            algorithmParams={"lambdaRatio": 1.2, "controllerType": "P", "tauCRatio": 0.5},
        )
        assert set(req.algorithmParams) == {"lambdaRatio", "controllerType", "tauCRatio"}

    def test_matrix_rejects_unknown_key(self) -> None:
        import pydantic

        from app.schemas.tuning import TuneMatrixRequest

        with pytest.raises(pydantic.ValidationError, match="未知算法参数键: foo"):
            TuneMatrixRequest(
                modelType="FOPDT",
                modelParams=self._model,
                algorithmParams={"foo": 1},
            )

    def test_invalid_algorithm_literal_rejected(self) -> None:
        """非法 method 拒绝（Literal 枚举外 → 422）."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            self._make(algorithm="NOT_AN_ALGO")


class TestTunePidServiceContract:
    """服务层：tune_pid 对本算法消费键做值级校验（绕过 schema 的通道防护）."""

    @staticmethod
    def _source_context() -> object:
        from app.services.tuning import TuningModelAuthorization

        return TuningModelAuthorization(
            model_type="FOPDT",
            model_params={"K": 1.0, "tau": 30.0, "theta": 5.0},
            loop_id="loop-1",
            model_source="MANUAL",
            source_record_id=None,
            risk_confirmed=True,
        )

    async def test_invalid_controller_type_rejected(self) -> None:
        from app.core.exceptions import BizError
        from app.services.tuning import tune_pid

        with pytest.raises(BizError) as exc_info:
            await tune_pid(
                model_type="FOPDT",
                model_params={},
                algorithm="ZN",
                algorithm_params={"controllerType": "XYZ"},
                source_context=self._source_context(),
            )
        assert exc_info.value.code == "ERR_TUNING_PARAM_INVALID"
        assert "非法" in exc_info.value.message

    async def test_out_of_range_lambda_rejected(self) -> None:
        from app.core.exceptions import BizError
        from app.services.tuning import tune_pid

        with pytest.raises(BizError) as exc_info:
            await tune_pid(
                model_type="FOPDT",
                model_params={},
                algorithm="IMC",
                algorithm_params={"lambdaRatio": 0.01},
                source_context=self._source_context(),
            )
        assert exc_info.value.code == "ERR_TUNING_PARAM_INVALID"

    async def test_valid_params_still_work(self) -> None:
        from app.services.tuning import tune_pid

        result = await tune_pid(
            model_type="FOPDT",
            model_params={},
            algorithm="IMC",
            algorithm_params={"lambdaRatio": 1.5},
            source_context=self._source_context(),
        )
        assert result["algorithm"] == "IMC"
        # 回显参数与消费一致（禁止静默兜底改写业务值）
        assert result["algorithmParams"] == {"lambdaRatio": 1.5}
        assert result["notes"] == "IMC 整定：λ = 1.5 × θ"

    async def test_unknown_algorithm_keeps_legacy_error(self) -> None:
        """未注册算法维持既有 ERR_INVALID_ALGORITHM 语义（不被参数校验拦截）."""
        from app.core.exceptions import BizError
        from app.services.tuning import tune_pid

        with pytest.raises(BizError) as exc_info:
            await tune_pid(
                model_type="FOPDT",
                model_params={},
                algorithm="IDENTIFICATION_ONLY",
                algorithm_params=None,
                source_context=self._source_context(),
            )
        assert exc_info.value.code == "ERR_INVALID_ALGORITHM"


# ---------------------------------------------------------------------------
# CFG-02：诊断方法元数据 readOnly 标注
# ---------------------------------------------------------------------------


class TestDiagnosisMethodMeta:
    """GET /configs/diagnosis 返回 methodMeta：只读说明 vs 真实可调字段."""

    def test_method_meta_marks_readonly_fields(self, client, mock_db, fake_redis) -> None:
        from app.api.v1.endpoints.configs import DIAGNOSIS_FIELD_META

        rows: list = []
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows
        mock_db.execute = AsyncMock(return_value=result_mock)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/configs/diagnosis",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        method_meta = resp.json()["data"]["methodMeta"]
        assert method_meta == DIAGNOSIS_FIELD_META
        # 可存但活执行不消费的字段 → readOnly（不再暗示可存生效）
        for field in ("algorithmType", "calcMethod", "params"):
            assert method_meta[field]["readOnly"] is True
            assert method_meta[field]["consumedByLiveEngine"] is False
        # 真实可调字段
        assert method_meta["threshold"]["readOnly"] is False
        assert method_meta["threshold"]["consumedByLiveEngine"] is True
        assert method_meta["isEnabled"]["readOnly"] is False

    def test_field_meta_constants(self) -> None:
        from app.api.v1.endpoints.configs import DIAGNOSIS_FIELD_META

        assert set(DIAGNOSIS_FIELD_META) == {
            "algorithmType",
            "calcMethod",
            "params",
            "threshold",
            "isEnabled",
            "diagName",
        }
