"""8 类异常值检测参数配置单元测试.

覆盖：
- thresholds 运行时覆盖合并（覆盖项生效 + 未覆盖回落默认 + 重置）
- 检测开关：set/is_detector_enabled + detect_all 跳过停用类型（8 类逐一参数化）
- outlier_params 服务：parse/merge 视图/build_stored_payload
- API 往返：GET 默认 → PUT 覆盖 → GET 合并视图 + 运行时缓存生效 + 审计
- 越界校验拒绝（pct>1、窗口<2、截止频率<=0、非法控制类型/开关键）
- 权限：非 ADMIN 拒绝 PUT

存储走 sys_config JSON（outlier_params.current），测试用内存 store 模拟。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.contracts.data_types import ControlType, OutlierReason
from app.models.sys_config import SysConfig
from app.services.preprocessing import outlier_params as svc
from app.services.preprocessing.outlier_detection import OutlierDetector
from app.services.preprocessing.thresholds import (
    get_detector_switches,
    get_threshold,
    is_detector_enabled,
    set_detector_switches,
    set_threshold_overrides,
)
from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 运行时缓存隔离：每个用例结束后重置覆盖与开关，避免污染其他测试
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_outlier_runtime():
    yield
    set_threshold_overrides(None)
    set_detector_switches(None)


# ---------------------------------------------------------------------------
# 阈值覆盖合并逻辑
# ---------------------------------------------------------------------------


class TestThresholdOverrideMerge:
    """覆盖项生效 + 未覆盖回落默认 + 缓存重置。"""

    def test_no_override_returns_defaults(self):
        """未设置覆盖时 get_threshold 返回算法默认。"""
        t = get_threshold(ControlType.FLOW)
        assert t.base_sampling_freq == 1
        assert t.jump_threshold_pct == 0.8
        assert t.frozen_window_points == 5

    def test_override_single_param_merged(self):
        """覆盖 FC 单个参数：覆盖项生效，其余参数回落默认。"""
        set_threshold_overrides({"FC": {"base_sampling_freq": 2, "jump_threshold_pct": 0.6}})
        t = get_threshold(ControlType.FLOW)
        assert t.base_sampling_freq == 2  # 覆盖生效
        assert t.jump_threshold_pct == 0.6  # 覆盖生效
        assert t.frozen_window_points == 5  # 未覆盖回落默认
        assert t.spike_threshold_pct == 0.5  # 未覆盖回落默认
        assert t.sampling_freq_label == "2s"

    def test_override_does_not_leak_to_other_types(self):
        """覆盖 FC 不影响其他控制类型。"""
        set_threshold_overrides({"FC": {"base_sampling_freq": 99}})
        assert get_threshold(ControlType.PRESSURE).base_sampling_freq == 2
        assert get_threshold(ControlType.COMPOSITION).base_sampling_freq == 10

    def test_override_result_is_frozen_dataclass(self):
        """合并结果仍是 frozen dataclass（不可变）。"""
        set_threshold_overrides({"TC": {"jump_threshold_pct": 0.9}})
        t = get_threshold(ControlType.TEMPERATURE)
        assert t.jump_threshold_pct == 0.9
        with pytest.raises(AttributeError):
            t.jump_threshold_pct = 0.1  # type: ignore[misc]

    def test_unknown_param_keys_ignored(self):
        """覆盖中的未知参数键被忽略，不污染合并结果。"""
        set_threshold_overrides({"FC": {"bogus_field": 123, "base_sampling_freq": 3}})
        t = get_threshold(ControlType.FLOW)
        assert t.base_sampling_freq == 3
        assert not hasattr(t, "bogus_field")

    def test_reset_overrides_restores_defaults(self):
        """传入 None 重置覆盖，恢复纯默认。"""
        set_threshold_overrides({"FC": {"base_sampling_freq": 99}})
        assert get_threshold(ControlType.FLOW).base_sampling_freq == 99
        set_threshold_overrides(None)
        assert get_threshold(ControlType.FLOW).base_sampling_freq == 1


# ---------------------------------------------------------------------------
# 检测开关
# ---------------------------------------------------------------------------


class TestDetectorSwitches:
    """8 类检测开关的设置与读取。"""

    def test_default_all_enabled(self):
        """默认 8 类检测全部启用。"""
        switches = get_detector_switches()
        assert set(switches) == {
            "nan",
            "out_of_range",
            "frozen",
            "jump",
            "spike",
            "ts_anomaly",
            "qc_bad",
            "hf_noise",
        }
        assert all(switches.values())

    def test_disable_single_detector(self):
        """停用单个检测类型，其余保持启用。"""
        set_detector_switches({"jump": False})
        assert is_detector_enabled("jump") is False
        assert is_detector_enabled("spike") is True
        assert is_detector_enabled("nan") is True

    def test_unknown_switch_key_ignored(self):
        """未知开关键被忽略。"""
        set_detector_switches({"bogus": False})
        assert "bogus" not in get_detector_switches()

    def test_reset_switches_restores_all_enabled(self):
        """传入 None 重置为全部启用。"""
        set_detector_switches({"jump": False, "frozen": False})
        set_detector_switches(None)
        assert all(get_detector_switches().values())


# ---------------------------------------------------------------------------
# detect_all 按开关跳过检测类型（8 类逐一参数化）
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2024, 1, 1)


def _timestamps(n: int, *, duplicate: bool = False) -> list[datetime]:
    ts = [_BASE_TS + timedelta(seconds=i) for i in range(n)]
    if duplicate and n >= 3:
        ts[2] = ts[1]  # 重复时间戳 → TS_ANOMALY
    return ts


def _run_detect_all(
    values: list,
    *,
    quality_codes: list[int] | None = None,
    timestamps: list[datetime] | None = None,
) -> dict[int, list[OutlierReason]]:
    detector = OutlierDetector(get_threshold(ControlType.FLOW))
    n = len(values)
    return detector.detect_all(
        tag_name="pv",
        values=values,
        timestamps=timestamps or _timestamps(n),
        range_min=0.0,
        range_max=100.0,
        quality_codes=quality_codes,
        is_normalized=True,
    )


def _all_reason_values(results: dict[int, list[OutlierReason]]) -> set[str]:
    return {r.value for reasons in results.values() for r in reasons}


class TestDetectAllSwitchSkip:
    """开关停用时 detect_all 跳过该检测类型（不参与异常判断和标记）。"""

    @pytest.mark.parametrize(
        "switch_key,reason,kwargs",
        [
            ("nan", "NaN", {"values": [50.0, None, 50.0, 50.1, 50.2, 50.3, 50.4]}),
            (
                "out_of_range",
                "OUT_OF_RANGE",
                {"values": [50.0, 200.0, 50.0, 50.1, 50.2, 50.3, 50.4]},
            ),
            ("frozen", "FROZEN", {"values": [50.0] * 10}),
            (
                "jump",
                "JUMP",
                {"values": [50.0, 50.0, 200.0, 50.0, 50.0, 50.0, 50.0]},
            ),
            (
                "spike",
                "SPIKE",
                {"values": [50.0, 50.0, 200.0, 50.0, 50.0, 50.0, 50.0]},
            ),
            (
                "ts_anomaly",
                "TS_ANOMALY",
                {
                    "values": [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6],
                    "timestamps": _timestamps(7, duplicate=True),
                },
            ),
            (
                "qc_bad",
                "QC_BAD",
                {
                    "values": [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6],
                    "quality_codes": [1, 1, 0, 1, 1, 1, 1],
                },
            ),
            (
                "hf_noise",
                "HF_NOISE",
                {"values": [0.0, 100.0] * 8},
            ),
        ],
    )
    def test_disabled_detector_is_skipped(self, switch_key, reason, kwargs):
        """启用时检出该异常；停用后 detect_all 不再产生该原因码。"""
        # 默认全部启用：应先检出该异常，确保场景构造有效
        enabled_results = _run_detect_all(**kwargs)
        assert reason in _all_reason_values(enabled_results), f"场景构造失效：启用时未检出 {reason}"

        set_detector_switches({switch_key: False})
        disabled_results = _run_detect_all(**kwargs)
        assert reason not in _all_reason_values(disabled_results)

    def test_disable_does_not_affect_other_detectors(self):
        """停用 jump 不影响 spike / out_of_range 的正常检出。"""
        values = [50.0, 50.0, 200.0, 50.0, 50.0, 50.0, 50.0]
        set_detector_switches({"jump": False})
        reasons = _all_reason_values(_run_detect_all(values))
        assert "JUMP" not in reasons
        assert "SPIKE" in reasons
        assert "OUT_OF_RANGE" in reasons

    def test_all_disabled_returns_empty(self):
        """8 类全部停用时 detect_all 返回空。"""
        set_detector_switches(dict.fromkeys(get_detector_switches(), False))
        values = [50.0, None, 200.0, 50.0, 50.0, 50.0, 50.0]
        results = _run_detect_all(
            values,
            quality_codes=[0] * 7,
            timestamps=_timestamps(7, duplicate=True),
        )
        assert results == {}


# ---------------------------------------------------------------------------
# outlier_params 服务：存储解析 / 合并视图 / 运行时应用
# ---------------------------------------------------------------------------


class TestOutlierParamsService:
    """服务层纯函数测试。"""

    def test_parse_stored_none_and_corrupt(self):
        """缺失/损坏的存储 JSON 返回 None（回落默认）。"""
        assert svc.parse_stored(None) is None
        assert svc.parse_stored("") is None
        assert svc.parse_stored("{not json") is None
        assert svc.parse_stored('["list"]') is None

    def test_build_merged_view_defaults(self):
        """无存储配置时合并视图为纯默认，overridden 全 False。"""
        view = svc.build_merged_view(None)
        assert len(view.thresholds) == 5
        flow = next(t for t in view.thresholds if t.control_type == "FC")
        assert flow.params.base_sampling_freq == 1
        assert flow.params.jump_threshold_pct == 0.8
        assert all(v is False for v in flow.overridden.values())
        assert all(view.switches.values())
        assert view.updated_at is None
        assert view.updated_by is None

    def test_build_merged_view_with_overrides(self):
        """存储覆盖项进入合并视图并打 overridden 标记。"""
        stored = {
            "thresholds": {"FC": {"baseSamplingFreq": 2}},
            "switches": {"frozen": False},
            "updatedAt": "2026-07-20T00:00:00+00:00",
            "updatedBy": "admin",
        }
        view = svc.build_merged_view(stored)
        flow = next(t for t in view.thresholds if t.control_type == "FC")
        assert flow.params.base_sampling_freq == 2
        assert flow.params.jump_threshold_pct == 0.8  # 未覆盖回落默认
        assert flow.overridden["baseSamplingFreq"] is True
        assert flow.overridden["jumpThresholdPct"] is False
        assert view.switches["frozen"] is False
        assert view.switches["jump"] is True
        assert view.updated_by == "admin"

    def test_apply_runtime_updates_caches(self):
        """apply_runtime 将存储配置写入进程内缓存（阈值合并 + 开关）。"""
        stored = {
            "thresholds": {"TC": {"noiseCutoffHz": 0.5}},
            "switches": {"hf_noise": False},
        }
        svc.apply_runtime(stored)
        t = get_threshold(ControlType.TEMPERATURE)
        assert t.noise_cutoff_hz == 0.5
        assert t.jump_threshold_pct == 0.3  # 未覆盖回落默认
        assert is_detector_enabled("hf_noise") is False
        assert is_detector_enabled("nan") is True

    def test_apply_runtime_corrupt_params_falls_back(self):
        """存储参数损坏时跳过该控制类型，其余生效，不抛异常。"""
        stored = {
            "thresholds": {
                "FC": {"jumpThresholdPct": 99},  # 越界，校验失败跳过
                "PC": {"jumpThresholdPct": 0.7},
            },
            "switches": {},
        }
        svc.apply_runtime(stored)
        assert get_threshold(ControlType.FLOW).jump_threshold_pct == 0.8  # 默认
        assert get_threshold(ControlType.PRESSURE).jump_threshold_pct == 0.7  # 生效

    def test_build_stored_payload_strips_empty(self):
        """存储 payload 剔除空覆盖并附带更新人/时间。"""
        from app.schemas.config import OutlierThresholdParams

        payload = svc.build_stored_payload(
            thresholds={"FC": OutlierThresholdParams(base_sampling_freq=2)},
            switches={"jump": False},
            operator="admin",
        )
        assert payload["thresholds"] == {"FC": {"baseSamplingFreq": 2}}
        assert payload["switches"] == {"jump": False}
        assert payload["updatedBy"] == "admin"
        assert payload["updatedAt"]


# ---------------------------------------------------------------------------
# API 往返（内存 store 模拟 sys_config）
# ---------------------------------------------------------------------------


class _CfgRow:
    """模拟 ORM 行：value setter 写回 store（模拟脏检查 flush）。"""

    def __init__(self, store: dict, key: str) -> None:
        self._store = store
        self.key = key
        self.description = None
        self.updated_by = None
        self.updated_at = None

    @property
    def value(self):
        return self._store.get(self.key)

    @value.setter
    def value(self, v):
        self._store[self.key] = v


@pytest.fixture
def sys_config_store() -> dict:
    return {}


@pytest.fixture
def bound_store(mock_db, sys_config_store):
    """将 mock_db 的 execute/add 绑定到内存 sys_config store，返回 add 调用记录。"""
    store = sys_config_store
    added: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        key = svc.SYS_CONFIG_KEY
        row = _CfgRow(store, key) if key in store else None
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        return result

    def add_side_effect(obj):
        if isinstance(obj, SysConfig):
            store[obj.key] = obj.value or ""
        added.append(obj)

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)
    mock_db.add = MagicMock(side_effect=add_side_effect)
    return added


_URL = "/api/v1/configs/outlier-params"
_HEADERS = {"Authorization": "Bearer fake-token"}


class TestOutlierParamsApi:
    """GET/PUT /configs/outlier-params 端点测试。"""

    def test_get_defaults_when_unconfigured(self, client, mock_db, bound_store) -> None:
        """未配置时 GET 返回算法默认合并视图。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(_URL, headers=_HEADERS)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data["thresholds"]) == 5
        flow = next(t for t in data["thresholds"] if t["controlType"] == "FC")
        assert flow["params"]["baseSamplingFreq"] == 1
        assert flow["params"]["frozenWindowPoints"] == 5
        assert flow["params"]["jumpThresholdPct"] == 0.8
        assert all(v is False for v in flow["overridden"].values())
        assert data["switches"] == {
            "nan": True,
            "out_of_range": True,
            "frozen": True,
            "jump": True,
            "spike": True,
            "ts_anomaly": True,
            "qc_bad": True,
            "hf_noise": True,
        }
        assert data["updatedAt"] is None

    def test_put_get_roundtrip(self, client, mock_db, bound_store, sys_config_store) -> None:
        """PUT 覆盖 → GET 合并视图反映覆盖 + 运行时缓存生效 + 审计写入。"""
        payload = {
            "thresholds": {"FC": {"baseSamplingFreq": 2, "jumpThresholdPct": 0.6}},
            "switches": {"jump": False},
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(_URL, json=payload, headers=_HEADERS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        flow = next(t for t in data["thresholds"] if t["controlType"] == "FC")
        assert flow["params"]["baseSamplingFreq"] == 2
        assert flow["params"]["jumpThresholdPct"] == 0.6
        assert flow["params"]["frozenWindowPoints"] == 5  # 未覆盖回落默认
        assert flow["overridden"]["baseSamplingFreq"] is True
        assert flow["overridden"]["frozenWindowPoints"] is False
        assert data["switches"]["jump"] is False
        assert data["switches"]["nan"] is True
        assert data["updatedBy"] == "admin"
        assert data["updatedAt"]

        # 运行时进程内缓存已刷新（热路径立即生效）
        assert get_threshold(ControlType.FLOW).base_sampling_freq == 2
        assert get_threshold(ControlType.FLOW).jump_threshold_pct == 0.6
        assert get_threshold(ControlType.PRESSURE).jump_threshold_pct == 0.5  # 不受影响
        assert is_detector_enabled("jump") is False
        assert is_detector_enabled("spike") is True

        # sys_config 已写入
        stored = json.loads(sys_config_store[svc.SYS_CONFIG_KEY])
        assert stored["thresholds"]["FC"] == {
            "baseSamplingFreq": 2,
            "jumpThresholdPct": 0.6,
        }
        assert stored["updatedBy"] == "admin"

        # 审计日志已写入
        from app.models.audit import SysAuditLog

        audits = [o for o in bound_store if isinstance(o, SysAuditLog)]
        assert len(audits) == 1
        assert audits[0].operation_type == "OUTLIER_PARAMS_UPDATE"
        assert audits[0].target_id == svc.SYS_CONFIG_KEY
        assert audits[0].operator == "admin"

        # 再次 GET：从 store 读出，与 PUT 响应一致
        with mock_current_user(TEST_USERS["admin"]):
            resp2 = client.get(_URL, headers=_HEADERS)
        assert resp2.status_code == 200
        flow2 = next(t for t in resp2.json()["data"]["thresholds"] if t["controlType"] == "FC")
        assert flow2["params"]["baseSamplingFreq"] == 2
        assert flow2["overridden"]["baseSamplingFreq"] is True

    def test_put_twice_update_branch(self, client, mock_db, bound_store, sys_config_store) -> None:
        """第二次 PUT 走 update 分支（已有 sys_config 行）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp1 = client.put(
                _URL,
                json={"thresholds": {"FC": {"baseSamplingFreq": 2}}, "switches": {}},
                headers=_HEADERS,
            )
            assert resp1.status_code == 200
            resp2 = client.put(
                _URL,
                json={"thresholds": {"FC": {"baseSamplingFreq": 4}}, "switches": {}},
                headers=_HEADERS,
            )
        assert resp2.status_code == 200
        stored = json.loads(sys_config_store[svc.SYS_CONFIG_KEY])
        assert stored["thresholds"]["FC"]["baseSamplingFreq"] == 4
        assert get_threshold(ControlType.FLOW).base_sampling_freq == 4

    @pytest.mark.parametrize(
        "payload",
        [
            {"thresholds": {"FC": {"jumpThresholdPct": 1.5}}, "switches": {}},  # pct > 1
            {"thresholds": {"FC": {"frozenStdPct": -0.1}}, "switches": {}},  # pct < 0
            {"thresholds": {"FC": {"frozenWindowPoints": 1}}, "switches": {}},  # 窗口 < 2
            {"thresholds": {"FC": {"minConsecutivePoints": 1}}, "switches": {}},
            {"thresholds": {"FC": {"noiseCutoffHz": 0}}, "switches": {}},  # 截止频率 <= 0
            {"thresholds": {"FC": {"baseSamplingFreq": 0}}, "switches": {}},
            {"thresholds": {"XX": {"jumpThresholdPct": 0.5}}, "switches": {}},  # 非法控制类型
            {"thresholds": {}, "switches": {"bogus": True}},  # 非法开关键
        ],
    )
    def test_put_validation_rejected(
        self, client, mock_db, bound_store, sys_config_store, payload
    ) -> None:
        """越界/非法输入被 422 拒绝，且不写入 store。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(_URL, json=payload, headers=_HEADERS)
        assert resp.status_code == 422
        assert svc.SYS_CONFIG_KEY not in sys_config_store

    def test_put_forbidden_for_non_admin(self, client, mock_db, bound_store) -> None:
        """非 ADMIN 角色 PUT 返回 403。"""
        payload = {"thresholds": {"FC": {"baseSamplingFreq": 2}}, "switches": {}}
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(_URL, json=payload, headers=_HEADERS)
        assert resp.status_code == 403

    def test_get_allowed_for_engineer(self, client, mock_db, bound_store) -> None:
        """IC_ENGINEER / PE_ENGINEER 可读 GET。"""
        for username in ("ic_engineer", "pe_engineer"):
            with mock_current_user(TEST_USERS[username]):
                resp = client.get(_URL, headers=_HEADERS)
            assert resp.status_code == 200
