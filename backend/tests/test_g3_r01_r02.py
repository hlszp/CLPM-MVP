"""G3 纠偏批 A 回归测试（14 号文 §2/§3）.

G3-R01 评估语义真实传输：
- ``resolve_semantic_link`` 四态（AVAILABLE / NOT_TRACEABLE / BROKEN / UNLINKED）
  ——legacy 缺语义（历史不可追溯）与新 profile 缺语义（断链暴露）分开；
- /loops/snapshots 端点构造按行透出 ``metricsSemantic`` / ``resultRecordId``
  / ``semanticLinkStatus``（response_model 序列化路径）。

G3-R02 最新结果身份与时序（SQL 形状层；真实 PG 选择语义见
``tests/integration/test_g3_r02_latest_selection_pg.py``）：
- latest_only 窗口排序不再含 status 优先级 CASE（昨日 SUCCESS 不抢今日
  INCONCLUSIVE），按业务窗口时序 ts_start DESC + 稳定键；
- latest_only 下 status/confidence 为最新代表**后置**筛选（先选代表再筛值，
  不先删不足行挑旧成功）；历史模式保持行级预过滤。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID_1 = "00000000-0000-0000-0000-000000000301"
RECORD_ID_1 = "00000000-0000-0000-0000-000000000901"


def _pg_sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ---------------------------------------------------------------------------
# G3-R01：resolve_semantic_link 四态
# ---------------------------------------------------------------------------


class TestResolveSemanticLink:
    def test_available_passes_semantic_raw(self):
        """record 携带 metricsSemantic → AVAILABLE 且原样透出（不造第二份）。"""
        from app.services.performance import SEMANTIC_LINK_AVAILABLE, resolve_semantic_link

        semantic = {
            "fast_rate": {
                "value": None,
                "confidenceLevel": "A",
                "assessmentState": "UNASSESSABLE",
                "details": {
                    "referenceScore": 100.0,
                    "referenceMethod": "STATIC_WINDOW_POLICY",
                    "evidenceStatus": "INSUFFICIENT_DYNAMIC_EVIDENCE",
                    "reasonCode": "NO_DYNAMIC_EVIDENCE",
                },
            },
            "__composite__": {
                "value": None,
                "confidenceLevel": None,
                "assessmentState": "PARTIAL",
                "details": {
                    "incompleteComponents": [{"metric": "fast_rate", "referenceOnly": True}]
                },
            },
        }
        payload = {
            "snapshotStatus": "SUCCESS",
            "metricsSemantic": semantic,
            "composite": semantic["__composite__"],
        }
        out, status = resolve_semantic_link(payload)
        assert status == SEMANTIC_LINK_AVAILABLE
        assert out is semantic  # 原样引用（同一对象，无拷贝/无重解释）

    def test_legacy_without_semantic_not_traceable(self):
        """legacy 记录无语义键（无 assessmentContext）→ 显式历史不可追溯。"""
        from app.services.performance import SEMANTIC_LINK_NOT_TRACEABLE, resolve_semantic_link

        _, status = resolve_semantic_link({"snapshotStatus": "SUCCESS", "score": 90.0})
        assert status == SEMANTIC_LINK_NOT_TRACEABLE

    def test_legacy_profile_without_semantic_not_traceable(self):
        """assessmentContext 标明 LEGACY_V2 且无语义 → 仍历史不可追溯。"""
        from app.services.performance import SEMANTIC_LINK_NOT_TRACEABLE, resolve_semantic_link

        _, status = resolve_semantic_link({"assessmentContext": {"assessmentProfile": "LEGACY_V2"}})
        assert status == SEMANTIC_LINK_NOT_TRACEABLE

    def test_new_profile_missing_semantic_is_broken(self):
        """新 profile（CANDIDATE_V3_RC1）应有语义而缺失 → 断链暴露，不回退 legacy。"""
        from app.services.performance import SEMANTIC_LINK_BROKEN, resolve_semantic_link

        _, status = resolve_semantic_link(
            {"assessmentContext": {"assessmentProfile": "CANDIDATE_V3_RC1"}}
        )
        assert status == SEMANTIC_LINK_BROKEN

    def test_new_profile_corrupt_semantic_is_broken(self):
        """新 profile 且语义损坏（非 dict）→ 断链。"""
        from app.services.performance import SEMANTIC_LINK_BROKEN, resolve_semantic_link

        _, status = resolve_semantic_link(
            {
                "metricsSemantic": "corrupted",
                "assessmentContext": {"assessmentProfile": "CANDIDATE_V3_RC1"},
            }
        )
        assert status == SEMANTIC_LINK_BROKEN

    def test_non_dict_payload_unlinked(self):
        from app.services.performance import SEMANTIC_LINK_UNLINKED, resolve_semantic_link

        out, status = resolve_semantic_link(None)
        assert out is None and status == SEMANTIC_LINK_UNLINKED

    def test_corrupt_context_without_semantic_not_traceable(self):
        """assessmentContext 损坏（profile 不可判定）→ 不可断定新档，历史不可追溯。"""
        from app.services.performance import SEMANTIC_LINK_NOT_TRACEABLE, resolve_semantic_link

        _, status = resolve_semantic_link({"assessmentContext": "corrupted"})
        assert status == SEMANTIC_LINK_NOT_TRACEABLE


# ---------------------------------------------------------------------------
# G3-R01：端点构造接通（HTTP + response_model 序列化）
# ---------------------------------------------------------------------------


def _make_snapshot(
    loop_id: str = LOOP_ID_1, result_record_id: str | None = RECORD_ID_1
) -> MagicMock:
    s = MagicMock()
    s.id = "00000000-0000-0000-0000-000000000501"
    s.loop_id = loop_id
    s.ts_start = datetime.now(UTC)
    s.ts_end = s.ts_start + timedelta(hours=1)
    s.score = None
    s.good_value_rate = None
    s.auto_mode_rate = None
    s.effective_auto_rate = None
    s.steady_rate = None
    s.accuracy_rate = None
    s.oscillation_rate = None
    s.saturation_rate = None
    s.fast_rate = None
    s.stiction_index = None
    s.settling_time = None
    s.output_trip_index = None
    s.status = "SUCCESS"
    s.ideal_settling_time = None
    s.algorithm_version = "KPI_CALC_v3.0-rc1"
    s.sampling_freq = "1s"
    s.quality_policy = "KEEP_ALL_WITH_VALIDITY"
    s.valid_rate = None
    s.confidence_level = "A"
    s.data_lineage = None
    s.instrument_fault_rate = None
    s.pv_mean = None
    s.pv_std = None
    s.sp_mean = None
    s.sp_std = None
    s.op_mean = None
    s.op_std = None
    s.valve_linearity = None
    s.valve_nonlinearity = None
    s.valve_op_min = None
    s.valve_op_max = None
    s.oscillation_amplitude = None
    s.setpoint_crossing_count = None
    s.time_constant = None
    s.fitness_level = None
    s.fitness_tags = None
    s.source = "SCHEDULED"
    s.source_task_id = None
    s.result_record_id = result_record_id
    return s


def _record_row(record_id: str, payload: dict) -> MagicMock:
    r = MagicMock()
    r.id = record_id
    r.payload = payload
    return r


class TestListEndpointSemanticPassthrough:
    def test_semantic_serialized_through_response_model(self, client, mock_db, fake_redis):
        """真实 HTTP 路由 + response_model：行携带 record 语义 → 响应含
        metricsSemantic/semanticLinkStatus/resultRecordId（可用=正式/参考
        F、compositeState 全链可达）。"""
        snap = _make_snapshot()
        list_result = MagicMock()
        list_result.all.return_value = [(snap, "tag-g3r")]
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        semantic = {
            "fast_rate": {
                "value": None,
                "confidenceLevel": "A",
                "assessmentState": "UNASSESSABLE",
                "details": {
                    "referenceScore": 100.0,
                    "evidenceStatus": "INSUFFICIENT_DYNAMIC_EVIDENCE",
                },
            },
            "__composite__": {
                "value": None,
                "confidenceLevel": None,
                "assessmentState": "PARTIAL",
                "details": {},
            },
        }
        rec_result = MagicMock()
        rec_result.all.return_value = [
            _record_row(
                RECORD_ID_1,
                {
                    "metricsSemantic": semantic,
                    "assessmentContext": {"assessmentProfile": "CANDIDATE_V3_RC1"},
                },
            )
        ]

        calls = [list_result, count_result, rec_result]

        async def execute_side_effect(stmt, *args, **kwargs):
            return calls.pop(0)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        item = resp.json()["data"]["items"][0]
        assert item["resultRecordId"] == RECORD_ID_1
        assert item["semanticLinkStatus"] == "AVAILABLE"
        assert item["metricsSemantic"]["fast_rate"]["details"]["referenceScore"] == 100.0
        assert item["metricsSemantic"]["fast_rate"]["value"] is None
        assert item["metricsSemantic"]["__composite__"]["assessmentState"] == "PARTIAL"

    def test_unlinked_snapshot_gets_explicit_status(self, client, mock_db, fake_redis):
        """快照行无 record 链接 → UNLINKED（显式不可追溯，不冒充 legacy 有语义）。"""
        snap = _make_snapshot(result_record_id=None)
        list_result = MagicMock()
        list_result.all.return_value = [(snap, "tag-g3r")]
        count_result = MagicMock()
        count_result.scalar.return_value = 1

        calls = [list_result, count_result]

        async def execute_side_effect(stmt, *args, **kwargs):
            # 第三次 execute（record 查询）不应发生：无 record 指针时跳过
            return calls.pop(0) if calls else rec_never()

        def rec_never():
            raise AssertionError("无 result_record_id 时不应查询账本")

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        item = resp.json()["data"]["items"][0]
        assert item["resultRecordId"] is None
        assert item["semanticLinkStatus"] == "UNLINKED"
        assert item["metricsSemantic"] is None

    def test_broken_link_not_disguised_as_legacy(self, client, mock_db, fake_redis):
        """新 profile 缺语义 → BROKEN（不能把新记录伪装旧记录通过）。"""
        snap = _make_snapshot()
        list_result = MagicMock()
        list_result.all.return_value = [(snap, "tag-g3r")]
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        rec_result = MagicMock()
        rec_result.all.return_value = [
            _record_row(
                RECORD_ID_1,
                {"assessmentContext": {"assessmentProfile": "CANDIDATE_V3_RC1"}},
            )
        ]
        calls = [list_result, count_result, rec_result]

        async def execute_side_effect(stmt, *args, **kwargs):
            return calls.pop(0)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/performance/loops/snapshots",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        item = resp.json()["data"]["items"][0]
        assert item["semanticLinkStatus"] == "BROKEN"
        assert item["metricsSemantic"] is None


# ---------------------------------------------------------------------------
# G3-R02：SQL 形状（latest_only 排序与后置筛选）
# ---------------------------------------------------------------------------


async def _capture_snapshot_sql(**kwargs) -> list:
    from app.services.performance import list_loop_snapshots

    db = AsyncMock()
    captured: list = []

    async def execute_side_effect(stmt, *args, **kw):
        captured.append(stmt)
        result = MagicMock()
        result.all.return_value = []
        result.scalar.return_value = 0
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)
    await list_loop_snapshots(db, **kwargs)
    return captured


@pytest.mark.asyncio
async def test_latest_only_window_ordering_has_no_status_priority() -> None:
    """latest_only 窗口排序：无 status 优先级 CASE（窗口时序优先）。

    昨日 SUCCESS90 不得抢占今日 INCONCLUSIVE——status 表达式从窗口
    ORDER BY 中移除是本反例的结构性保证。
    """
    captured = await _capture_snapshot_sql(latest_only=True)
    assert captured, "应捕获列表+count 两条 SQL"
    for stmt in captured:
        sql = _pg_sql(stmt)
        assert "status != 'INCONCLUSIVE'" not in sql
        assert "CASE" not in sql
    # 列表语句（第一条）含窗口函数定义：ts_start DESC 起始，稳定键破平局
    list_sql = _pg_sql(captured[0])
    window_def = (
        "row_number() OVER (PARTITION BY kpi_snapshot_hourly.loop_id "
        "ORDER BY kpi_snapshot_hourly.ts_start DESC, "
        "kpi_snapshot_hourly.ts_end DESC, kpi_snapshot_hourly.id DESC"
    )
    assert window_def in list_sql


@pytest.mark.asyncio
async def test_latest_only_status_filter_is_post_selection() -> None:
    """latest_only + status：窗口子查询（ROW_NUMBER 定义处）不含 status 条件，
    外层对代表行后置过滤（先选代表再筛值）。"""
    captured = await _capture_snapshot_sql(latest_only=True, status_filter="SUCCESS")
    for stmt in captured:
        sql = _pg_sql(stmt)
        assert "status != 'INCONCLUSIVE'" not in sql
        # 窗口子查询的 WHERE 不含 status；代表后置过滤在查询尾部
        window_where = sql.split("row_number()")[0]
        assert "kpi_snapshot_hourly.status IN" not in window_where
        assert "kpi_snapshot_hourly.status IN ('SUCCESS')" in sql


@pytest.mark.asyncio
async def test_history_mode_status_filter_stays_row_level() -> None:
    """latest_only=False（历史搜索）：status 保持行级预过滤（行为不变）。"""
    captured = await _capture_snapshot_sql(latest_only=False, status_filter="INCONCLUSIVE")
    assert "row_number()" not in _pg_sql(captured[0])
    assert "kpi_snapshot_hourly.status IN ('INCONCLUSIVE')" in _pg_sql(captured[0])


@pytest.mark.asyncio
async def test_latest_only_without_value_filters_keeps_fast_count() -> None:
    """latest_only 无 status/confidence/grade：count 仍为 distinct loop 口径
    （行为与性能保持；仅带代表筛选时才走筛选后计数）。"""
    captured = await _capture_snapshot_sql(latest_only=True)
    count_sql = _pg_sql(captured[1])
    assert "count(distinct(kpi_snapshot_hourly.loop_id))" in count_sql
