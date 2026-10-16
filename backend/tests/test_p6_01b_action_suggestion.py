"""P6 Slice I（01b-b）行动建议反例测试（契约 01a §8 A13/A14 + 03§9 P6-01 验收行）。

映射结论：现行实现零缺口（原因分类/整定滤除语义 G3/D5 已验收，本文件只补
反例证据，不改实现）：

- 阀门/仪表/数据不足 run → 产出**不含**整定类动作（验收行"不无条件推荐整定"；
  模板表 ``STANDARD_ACTION_TEMPLATES`` 按原因分域，整定模板仅属 TUNING 类）；
- 振荡（TUNING）等有证据已决 → 推荐与原因相符（8 类 category→模板全对应；
  INCONCLUSIVE 未决时并存 TUNING 次分类同样被滤除）；
- 权限不足角色（SPONSOR 零写红线）→ 建议只读：GET 200 可读 / PUT 403
  （POST/DELETE 403 已由 test_diagnosis_v2_api.py 锚定，handling 侧 accept/
  reject/ignore/convert SPONSOR·EXPERT 403 已由 test_handling_api.py 锚定）；
- A13/A14：接受建议只翻转建议状态（handling_order 零写入）；计算完成
  （``generate_system_actions``）只写 loop_action_item——均不闭现场工单。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.diagnosis_run import DiagnosisRun
from app.models.handling_order import HandlingOrder
from app.models.loop_action_item import LoopActionItem
from app.services.diagnosis_assessment import (
    JUDGEMENT_INCONCLUSIVE,
    JUDGEMENT_SYMPTOM_DETECTED,
)
from app.services.diagnosis_system_actions import generate_system_actions
from app.services.loop_action_templates import STANDARD_ACTION_TEMPLATES
from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID = str(uuid4())
RUN_ID = str(uuid4())

#: 整定类动作模板文本（TUNING 类专有；其他分类产出不得含其一）
_TUNING_ACTION_TEXTS = tuple(t["action"] for t in STANDARD_ACTION_TEMPLATES["TUNING"])

#: 已决 meta（症状已检出+原因候选：非 NOT_ASSESSED、非 INCONCLUSIVE）
_DECIDED_META = {"judgementStatus": JUDGEMENT_SYMPTOM_DETECTED, "causeStatus": "CANDIDATE"}


# ---------------------------------------------------------------------------
# helpers（模式参照 test_diagnosis_d5_fusion.py）
# ---------------------------------------------------------------------------


def _mock_db(existing_count: int = 0) -> MagicMock:
    exec_result = MagicMock()
    exec_result.scalar.return_value = existing_count
    db = MagicMock()
    db.execute = AsyncMock(return_value=exec_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _run_row(
    status: str = "SUCCESS",
    meta: dict | None = None,
    primary: str | None = "VALVE",
    secondary: list | None = None,
    recommendations: list | None = None,
    review_status: str | None = None,
    review_results: list | None = None,
) -> DiagnosisRun:
    return DiagnosisRun(
        id=RUN_ID,
        loop_id=LOOP_ID,
        status=status,
        assessment_meta=meta,
        primary_category=primary,
        secondary_categories=secondary or [],
        recommendations=recommendations or [],
        review_status=review_status,
        review_results=review_results,
    )


def _evidence_rec(category: str, operator: str) -> dict:
    return {
        "content": "x",
        "basis": "…",
        "direction": "y",
        "priority": 1,
        "evidence": {
            "category": category,
            "operators": [operator],
            "support": 0.9,
            "symptomTag": "OSCILLATION",
        },
    }


def _added_items(db: MagicMock) -> list[LoopActionItem]:
    return [c.args[0] for c in db.add.call_args_list]


def _pg_sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ---------------------------------------------------------------------------
# 1. 反例：阀门/仪表/数据不足 run → 产出不含整定类动作（不无条件推荐整定）
# ---------------------------------------------------------------------------


class TestNoUnconditionalTuningRecommendation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["VALVE", "INSTRUMENT", "DATA_INSUFFICIENT"])
    async def test_decided_run_produces_no_tuning_actions(self, category: str) -> None:
        """已决（症状检出+候选原因）带证据 run：三类产出全部归属本类，
        无任何 TUNING 分类/整定模板文本（检修/校验/补数据，不推整定）。"""
        db = _mock_db(existing_count=0)
        n = await generate_system_actions(
            db,
            _run_row(
                meta=dict(_DECIDED_META),
                primary=category,
                recommendations=[_evidence_rec(category, "evidence_op")],
            ),
        )
        assert n > 0
        added = _added_items(db)
        assert all(o.category == category for o in added)
        assert all(o.category != "TUNING" for o in added)
        for item in added:
            assert not any(t in item.content for t in _TUNING_ACTION_TEXTS), item.content

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category", ["VALVE", "INSTRUMENT", "DATA_INSUFFICIENT"])
    async def test_legacy_run_also_no_tuning_actions(self, category: str) -> None:
        """legacy run（无 assessment_meta）同样不无条件推整定（模板表分域保证）。"""
        db = _mock_db(existing_count=0)
        n = await generate_system_actions(db, _run_row(meta=None, primary=category))
        assert n > 0
        added = _added_items(db)
        assert all(o.category == category for o in added)
        for item in added:
            assert not any(t in item.content for t in _TUNING_ACTION_TEXTS), item.content

    @pytest.mark.asyncio
    async def test_data_insufficient_recommends_backfill_not_tuning(self) -> None:
        """数据不足 → 补数据复诊（补采历史数据），不出现整定动作。"""
        db = _mock_db(existing_count=0)
        n = await generate_system_actions(
            db, _run_row(meta=dict(_DECIDED_META), primary="DATA_INSUFFICIENT")
        )
        assert n == len(STANDARD_ACTION_TEMPLATES["DATA_INSUFFICIENT"])
        added = _added_items(db)
        assert any("补采历史数据" in o.content for o in added)
        assert all("整定" not in o.content for o in added)


# ---------------------------------------------------------------------------
# 2. 正例：有证据已决 → 推荐与原因相符（8 类 category→模板全对应）
# ---------------------------------------------------------------------------


class TestCauseActionCorrespondence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "category",
        sorted(STANDARD_ACTION_TEMPLATES.keys()),
    )
    async def test_category_yields_only_own_templates(self, category: str) -> None:
        """每类原因 → 恰好产出自己的模板集合（条数/动作文本/分类一致，无交叉）。"""
        db = _mock_db(existing_count=0)
        templates = STANDARD_ACTION_TEMPLATES[category]
        n = await generate_system_actions(
            db,
            _run_row(
                meta=dict(_DECIDED_META),
                primary=category,
                recommendations=[_evidence_rec(category, "cause_op")],
            ),
        )
        assert n == len(templates)
        added = _added_items(db)
        assert all(o.category == category for o in added)
        for tpl, item in zip(templates, added, strict=True):
            assert item.content.startswith(tpl["action"])
            assert tpl["description"] in item.content
            assert int(item.priority) == int(tpl["priority"])

    @pytest.mark.asyncio
    async def test_oscillation_tuning_evidence_recommends_retuning(self) -> None:
        """振荡归因参数（TUNING 已决、带算子证据）→ 推荐重新整定 PID（与原因相符）。"""
        db = _mock_db(existing_count=0)
        n = await generate_system_actions(
            db,
            _run_row(
                meta=dict(_DECIDED_META),
                primary="TUNING",
                recommendations=[_evidence_rec("TUNING", "oscillation_acf")],
            ),
        )
        assert n > 0
        added = _added_items(db)
        assert all(o.category == "TUNING" for o in added)
        assert any("重新整定 PID 参数" in o.content for o in added)
        # 建议-证据链绑定：basis 引用算子（三方可溯）
        assert any("oscillation_acf" in o.basis for o in added)

    @pytest.mark.asyncio
    async def test_secondary_categories_also_recommend_matching_actions(self) -> None:
        """并存原因（secondary）已决 → 两类动作都产出且各自相符。"""
        db = _mock_db(existing_count=0)
        n = await generate_system_actions(
            db,
            _run_row(
                meta=dict(_DECIDED_META),
                primary="VALVE",
                secondary=[{"category": "TUNING", "support": 0.8}],
            ),
        )
        added = _added_items(db)
        assert n == len(STANDARD_ACTION_TEMPLATES["VALVE"]) + len(
            STANDARD_ACTION_TEMPLATES["TUNING"]
        )
        assert {o.category for o in added} == {"VALVE", "TUNING"}

    @pytest.mark.asyncio
    async def test_inconclusive_filters_secondary_tuning_keeps_valve(self) -> None:
        """未决（INCONCLUSIVE）：并存 TUNING 次分类同样滤除，阀门检修建议保留。"""
        db = _mock_db(existing_count=0)
        meta = {"judgementStatus": JUDGEMENT_INCONCLUSIVE}
        n = await generate_system_actions(
            db,
            _run_row(
                meta=meta,
                primary="VALVE",
                secondary=[{"category": "TUNING", "support": 0.8}],
            ),
        )
        assert n == len(STANDARD_ACTION_TEMPLATES["VALVE"])
        added = _added_items(db)
        assert all(o.category == "VALVE" for o in added)


# ---------------------------------------------------------------------------
# 3. A13/A14：建议生成/接受不闭现场工单（handling_order 零写入）
# ---------------------------------------------------------------------------


class TestSuggestionLifecycleDoesNotTouchSiteOrder:
    @pytest.mark.asyncio
    async def test_generation_only_writes_action_items(self) -> None:
        """计算完成生成建议：db.add 的全部对象为 LoopActionItem（不触碰 HandlingOrder）。"""
        db = _mock_db(existing_count=0)
        n = await generate_system_actions(
            db,
            _run_row(
                meta=dict(_DECIDED_META),
                primary="TUNING",
                secondary=[{"category": "VALVE", "support": 0.8}],
            ),
        )
        assert n > 0
        added = _added_items(db)
        assert all(isinstance(o, LoopActionItem) for o in added)
        assert not any(isinstance(o, HandlingOrder) for o in added)
        # 生成路径仅一次幂等 count 查询（SELECT），无任何 handling_order 语句
        for c in db.execute.call_args_list:
            assert "handling_order" not in _pg_sql(c.args[0])

    def test_accept_suggestion_only_flips_suggestion_status(self, client) -> None:
        """接受建议（A13）：PENDING→ACCEPTED + 审核人留痕；不创建/不关闭现场工单。

        结构性断言：执行语句仅一条 loop_action_item SELECT；db.add 从未调用
        （无 HandlingOrder 写入）；现场闭环仍由工单自身状态机（显式 convert
        开单 → start/feedback/submit/verify）承担。
        """
        from datetime import datetime

        from app.core.db import get_db

        sug = LoopActionItem(
            id=str(uuid4()),
            run_id=RUN_ID,
            loop_id=LOOP_ID,
            source="SYSTEM",
            category="TUNING",
            content="重新整定 PID 参数：按当前工况重新计算",
            basis="诊断结论：参数问题（PID 整定）",
            priority=1,
            status="PENDING",
            suggested_by="系统",
            suggested_at=datetime(2026, 10, 13, 0, 0, 0),
        )
        r = MagicMock()
        r.scalar_one_or_none.return_value = sug
        captured: list = []

        async def _execute(stmt, *args, **kwargs):  # noqa: ARG001
            captured.append(stmt)
            return r

        mock_db = MagicMock()
        mock_db.execute = _execute
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.add = MagicMock()
        client.app.dependency_overrides[get_db] = lambda: mock_db

        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/handling/suggestions/{sug.id}/accept",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACCEPTED"
        # 仅一次 SELECT（建议行）；语句只涉 loop_action_item，无 handling_order 写入
        assert len(captured) == 1
        sql = _pg_sql(captured[0])
        assert "loop_action_item" in sql
        assert "handling_order" not in sql
        mock_db.add.assert_not_called()


# ---------------------------------------------------------------------------
# 4. 权限不足角色：建议只读（SPONSOR 零写红线）
# ---------------------------------------------------------------------------


class TestSponsorReadonlyOnSuggestions:
    """SPONSOR：建议可读（diagnosis:view 全 5 角色）但任何写操作 403。

    已锚定不重复：POST create 403（test_diagnosis_v2_api.py
    test_create_action_forbidden_for_sponsor）、DELETE 403
    （test_delete_forbidden_for_sponsor）、handling accept/reject/ignore/
    convert SPONSOR·EXPERT 403（test_handling_api.py）。
    """

    def test_sponsor_can_read_run_actions(self, client) -> None:
        """GET /diagnosis/runs/{id}/actions：SPONSOR 200 只读（观察角色可看建议）。"""
        from datetime import UTC, datetime

        from app.core.db import get_db

        run = _run_row(meta=dict(_DECIDED_META), primary="VALVE")
        existing = [
            LoopActionItem(
                id=str(uuid4()),
                run_id=RUN_ID,
                loop_id=LOOP_ID,
                source="SYSTEM",
                category="VALVE",
                content="检查阀门粘滞与非线性：PV-OP 散点呈椭圆回环…",
                basis="诊断结论：阀门/执行机构问题",
                priority=1,
                status="PENDING",
                suggested_by="系统",
                suggested_at=datetime.now(UTC).replace(tzinfo=None),
            )
        ]
        run_r = MagicMock()
        run_r.scalar_one_or_none.return_value = run
        list_r = MagicMock()
        list_r.scalars.return_value.all.return_value = existing
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(side_effect=[run_r, list_r])
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        client.app.dependency_overrides[get_db] = lambda: mock_db

        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                f"/api/v1/diagnosis/runs/{RUN_ID}/actions",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["category"] == "VALVE"
        # 已有 SYSTEM 建议 → 幂等不重建（只读路径不产生写）
        mock_db.add.assert_not_called()

    def test_sponsor_update_action_forbidden(self, client) -> None:
        """PUT /diagnosis/runs/actions/{id}：SPONSOR 403（无 diagnosis:review）。"""
        action_id = str(uuid4())
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.put(
                f"/api/v1/diagnosis/runs/actions/{action_id}",
                headers={"Authorization": "Bearer fake-token"},
                json={"content": "越权改写"},
            )
        assert resp.status_code == 403
