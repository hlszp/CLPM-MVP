"""可恢复工程批次的状态ful离线契约；不连接运行库、不启动 Worker。

测试真实快照/冲突/幂等实现；底层批量更新与引用校验分别由既有批量测试和
本文件原始 NULL 写入测试覆盖。PG 行锁语义另需隔离真实库集成复验。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.schemas.loop_batch import LoopBatchConfigRequest, LoopBatchRestoreRequest
from app.services import loop_batch_restore as svc
from app.services.loop_batch import batch_update_loops
from tests.conftest import TEST_USERS, mock_current_user


def make_loop(index=0):
    return SimpleNamespace(
        id=str(uuid4()),
        tag_name=f"RESTORE-{index:04}",
        unit_id="unit-1",
        control_action_direction="UNKNOWN",
        op_output_lower_limit=None,
        op_output_upper_limit=None,
        dcs_model_id=None,
        engineering_template_id=None if index % 3 == 0 else f"template-{index % 2}",
        engineering_template_version=None if index % 3 == 0 else index % 2 + 1,
        updated_at=datetime(2026, 10, 17),
        updated_by="seed",
    )


class MemorySession:
    """仅模拟数据库 IO；实际业务状态比较/快照/restore 分支均使用生产函数。"""

    def __init__(self, rows):
        self.rows = {row.id: row for row in rows}
        self.logs = {}
        self.added = []
        self.statements = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        result = MagicMock()
        if "FROM loop_ledger" in str(statement):
            ids = statement.compile().params["id_1"]
            result.scalars.return_value.all.return_value = [
                self.rows[lid] for lid in ids if lid in self.rows
            ]
        else:
            result.first.return_value = None
        return result

    async def get(self, model, key):
        return self.logs.get(str(key)) if model is SysAuditLog else None

    def add(self, log):
        assert log.id not in self.logs, "duplicate immutable audit ID"
        self.logs[log.id] = log
        self.added.append(log)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1


async def apply_engineering(db, loop_ids, updates, operator, *, dry_run, **kwargs):
    results = []
    affected = 0
    for lid in loop_ids:
        row = db.rows.get(lid)
        if row is None:
            results.append(
                {
                    "loopId": lid,
                    "tagName": None,
                    "status": "NOT_FOUND",
                    "changes": [],
                    "error": "不存在",
                }
            )
            continue
        changes = [
            {"field": key, "before": getattr(row, key), "after": value}
            for key, value in updates.items()
            if getattr(row, key) != value
        ]
        results.append(
            {
                "loopId": lid,
                "tagName": row.tag_name,
                "status": "OK" if changes else "UNCHANGED",
                "changes": changes,
                "error": None,
            }
        )
        affected += bool(changes)
        if not dry_run:
            for key, value in updates.items():
                setattr(row, key, value)
    return {"affected": affected, "dryRun": dry_run, "results": results}


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(svc, "batch_update_loops", apply_engineering)
    monkeypatch.setattr(svc, "_validate_restore", AsyncMock())


async def execute(db, ids, updates=None, operation_id=None):
    updates = updates or {"engineering_template_id": "new-template"}
    operation_id = operation_id or str(uuid4())
    preview = await svc.run_snapshot_batch(
        db, ids, updates, "admin", operation_id=operation_id, dry_run=True
    )
    tokens = {
        row["loopId"]: row["stateToken"] for row in preview["results"] if row.get("stateToken")
    }
    applied = await svc.run_snapshot_batch(
        db,
        ids,
        updates,
        "admin",
        operation_id=operation_id,
        dry_run=False,
        expected_state_tokens=tokens,
    )
    return applied, tokens


@pytest.mark.asyncio
async def test_1000_mixed_original_states_exact_restore_in_five_chunks(engine):
    rows = [make_loop(index) for index in range(1000)]
    db = MemorySession(rows)
    originals = {row.id: copy.deepcopy(svc._state(row)) for row in rows}
    snapshots = []
    for start in range(0, 1000, 200):
        result, _ = await execute(db, [row.id for row in rows[start : start + 200]])
        assert result["affected"] == 200
        snapshots.append(result["snapshotId"])
    assert len(db.logs) == 5
    for snapshot_id in snapshots:
        first = await svc.get_batch_snapshot(db, snapshot_id, "admin", offset=0, limit=71)
        second = await svc.get_batch_snapshot(db, snapshot_id, "admin", offset=71, limit=129)
        assert first["total"] == 200
        assert len(first["items"] + second["items"]) == 200
        prior_audits, prior_commits = len(db.logs), db.commits
        preview = await svc.restore_batch_snapshot(
            db, snapshot_id, "admin", reason="核验回退", dry_run=True
        )
        assert preview["affected"] == 200
        assert (len(db.logs), db.commits) == (prior_audits, prior_commits)
        actual = await svc.restore_batch_snapshot(
            db, snapshot_id, "admin", reason="核验回退", dry_run=False
        )
        assert actual["affected"] == 200
        count = len(db.logs)
        again = await svc.restore_batch_snapshot(
            db, snapshot_id, "admin", reason="网络重试", dry_run=False
        )
        assert again["affected"] == 0
        assert {item["status"] for item in again["results"]} == {"ALREADY_RESTORED"}
        assert len(db.logs) == count
    assert {row.id: svc._state(row) for row in rows} == originals
    assert len(db.logs) == 1005
    assert any("FOR UPDATE" in statement for statement in db.statements)
    assert any("pg_advisory_xact_lock" in statement for statement in db.statements)


@pytest.mark.asyncio
async def test_same_operation_replay_and_payload_reuse_rejected(engine):
    row = make_loop()
    db = MemorySession([row])
    applied, tokens = await execute(db, [row.id])
    replay = await svc.run_snapshot_batch(
        db,
        [row.id],
        {"engineering_template_id": "new-template"},
        "admin",
        operation_id=applied["snapshotId"],
        dry_run=False,
        expected_state_tokens=tokens,
    )
    assert replay["replayed"] and len(db.logs) == 1 and db.commits == 1
    with pytest.raises(BizError, match="operationId"):
        await svc.run_snapshot_batch(
            db,
            [row.id],
            {"engineering_template_id": "other"},
            "admin",
            operation_id=applied["snapshotId"],
            dry_run=False,
            expected_state_tokens=tokens,
        )


@pytest.mark.asyncio
async def test_preview_aba_change_conflicts_and_does_not_overwrite(engine):
    row = make_loop()
    db = MemorySession([row])
    operation_id = str(uuid4())
    preview = await svc.run_snapshot_batch(
        db,
        [row.id],
        {"engineering_template_id": "new"},
        "admin",
        operation_id=operation_id,
        dry_run=True,
    )
    token = preview["results"][0]["stateToken"]
    assert db.commits == 0 and not db.logs
    row.updated_at += timedelta(seconds=1)  # 编辑后又恢复原值，仍不允许旧预览覆盖。
    result = await svc.run_snapshot_batch(
        db,
        [row.id],
        {"engineering_template_id": "new"},
        "admin",
        operation_id=operation_id,
        dry_run=False,
        expected_state_tokens={row.id: token},
    )
    assert result["results"][0]["status"] == "CONFLICT"
    assert row.engineering_template_id is None


@pytest.mark.asyncio
async def test_restore_conflict_and_partial_failure_are_per_loop(engine, monkeypatch):
    rows = [make_loop(index) for index in range(4)]
    db = MemorySession(rows)
    applied, _ = await execute(db, [row.id for row in rows])
    rows[0].op_output_lower_limit = 23
    rows[0].updated_at += timedelta(seconds=1)
    del db.rows[rows[1].id]

    async def validate(_, state, lid):
        if lid == rows[2].id:
            raise BizError("ERR_TEMPLATE_VERSION", "原模板版本已删除", 409)

    monkeypatch.setattr(svc, "_validate_restore", validate)
    result = await svc.restore_batch_snapshot(
        db, applied["snapshotId"], "admin", reason="部分失败", dry_run=False
    )
    assert [item["status"] for item in result["results"]] == ["CONFLICT", "NOT_FOUND", "FAIL", "OK"]
    assert rows[0].op_output_lower_limit == 23
    assert rows[2].engineering_template_id == "new-template"
    assert rows[3].engineering_template_id is None
    assert result["affected"] == 1


@pytest.mark.asyncio
async def test_already_restored_then_manual_edit_never_overwritten(engine):
    row = make_loop()
    db = MemorySession([row])
    applied, _ = await execute(db, [row.id])
    await svc.restore_batch_snapshot(
        db, applied["snapshotId"], "admin", reason="回退", dry_run=False
    )
    row.engineering_template_id = "manual"
    row.updated_at += timedelta(seconds=1)
    result = await svc.restore_batch_snapshot(
        db, applied["snapshotId"], "admin", reason="重复", dry_run=False
    )
    assert result["results"][0]["status"] == "CONFLICT"
    assert row.engineering_template_id == "manual"


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["other-admin", "pe_engineer"])
async def test_snapshot_owner_is_enforced_for_read_and_restore(engine, reader):
    row = make_loop()
    db = MemorySession([row])
    applied, _ = await execute(db, [row.id])
    with pytest.raises(BizError) as exc:
        await svc.get_batch_snapshot(db, applied["snapshotId"], reader, offset=0, limit=200)
    assert exc.value.status_code == 404
    with pytest.raises(BizError):
        await svc.restore_batch_snapshot(
            db, applied["snapshotId"], reader, reason="越权", dry_run=False
        )


@pytest.mark.asyncio
async def test_restore_scope_and_request_limits(engine, monkeypatch):
    row = make_loop()
    db = MemorySession([row])
    applied, _ = await execute(db, [row.id])
    denied = AsyncMock(side_effect=BizError("ERR_SCOPE_FORBIDDEN", "越权", 403))
    monkeypatch.setattr(svc, "ensure_loops_in_scope", denied)
    with pytest.raises(BizError) as exc:
        await svc.restore_batch_snapshot(
            db, applied["snapshotId"], "admin", reason="范围", dry_run=False, scope=object()
        )
    assert exc.value.status_code == 403
    with pytest.raises(BizError):
        await svc.restore_batch_snapshot(
            db,
            applied["snapshotId"],
            "admin",
            reason="非法",
            dry_run=False,
            loop_ids=[str(uuid4())],
        )
    with pytest.raises(BizError):
        await svc.run_snapshot_batch(
            db,
            [str(uuid4()) for _ in range(201)],
            {"engineering_template_id": "new"},
            "admin",
            operation_id=str(uuid4()),
            dry_run=True,
        )


@pytest.mark.asyncio
async def test_legacy_batch_mode_remains_none_noop_but_snapshot_mode_clears_null():
    row = make_loop(1)
    row.is_active = True
    row.importance_level = 2
    row.include_in_evaluation = True
    row.op_output_lower_limit = 5
    row.dcs_model_id = str(uuid4())
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    result.first.return_value = None
    db.execute.return_value = result
    db.add = MagicMock()
    updates = {
        "engineering_template_id": None,
        "engineering_template_version": None,
        "dcs_model_id": None,
        "op_output_lower_limit": None,
    }
    legacy = await batch_update_loops(db, [row.id], updates, "admin", dry_run=True)
    assert legacy["affected"] == 0
    assert row.engineering_template_id is not None and row.dcs_model_id is not None
    actual = await batch_update_loops(
        db, [row.id], updates, "admin", commit=False, allow_null_engineering=True
    )
    assert actual["affected"] == 1
    assert row.engineering_template_id is None and row.engineering_template_version is None
    assert row.dcs_model_id is None and row.op_output_lower_limit is None
    db.commit.assert_not_awaited()


def test_snapshot_request_requires_tokens_and_preserves_explicit_null():
    payload = {
        "loopIds": [str(uuid4())],
        "updates": {"engineeringTemplateId": None},
        "operationId": str(uuid4()),
    }
    with pytest.raises(ValidationError, match="expectedStateTokens"):
        LoopBatchConfigRequest.model_validate(payload)
    parsed = LoopBatchConfigRequest.model_validate({**payload, "dryRun": True})
    assert parsed.updates.model_dump(exclude_unset=True) == {"engineering_template_id": None}
    with pytest.raises(ValidationError):
        LoopBatchRestoreRequest(reason=" ", dryRun=True)


@pytest.mark.asyncio
async def test_unknown_or_unsupported_snapshot_fails_closed(engine):
    db = MemorySession([])
    with pytest.raises(BizError) as exc:
        await svc.get_batch_snapshot(db, str(uuid4()), "admin", offset=0, limit=200)
    assert exc.value.status_code == 404
    log = SysAuditLog(
        id=str(uuid4()),
        operator="admin",
        operation_type=svc.SNAPSHOT_OPERATION,
        before_value=json.dumps({"version": 0, "ownerId": "admin"}),
        after_value="{}",
    )
    db.add(log)
    with pytest.raises(BizError) as exc:
        await svc.restore_batch_snapshot(db, log.id, "admin", reason="旧格式", dry_run=False)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_stable_user_id_blocks_recreated_same_name_and_allows_owner_rename(engine):
    row = make_loop()
    db = MemorySession([row])
    operation_id = str(uuid4())
    preview = await svc.run_snapshot_batch(
        db,
        [row.id],
        {"engineering_template_id": "new"},
        "admin",
        operation_id=operation_id,
        dry_run=True,
        operator_id="user-1",
    )
    await svc.run_snapshot_batch(
        db,
        [row.id],
        {"engineering_template_id": "new"},
        "admin",
        operation_id=operation_id,
        dry_run=False,
        operator_id="user-1",
        expected_state_tokens={row.id: preview["results"][0]["stateToken"]},
    )
    with pytest.raises(BizError) as exc:
        await svc.get_batch_snapshot(
            db, operation_id, "admin", offset=0, limit=200, operator_id="user-2"
        )
    assert exc.value.status_code == 404
    detail = await svc.get_batch_snapshot(
        db, operation_id, "admin-renamed", offset=0, limit=200, operator_id="user-1"
    )
    assert detail["total"] == 1


@pytest.mark.parametrize("role", ["ic_engineer", "pe_engineer", "sponsor"])
@pytest.mark.parametrize("mode", ["list", "read", "restore", "apply"])
def test_snapshot_endpoints_reject_users_without_batch_permission(client, role, mode):
    snapshot_id = str(uuid4())
    with mock_current_user(TEST_USERS[role]):
        if mode == "list":
            response = client.get(
                "/api/v1/loops/batch-snapshots", headers={"Authorization": "Bearer fake-token"}
            )
        elif mode == "read":
            response = client.get(
                f"/api/v1/loops/batch-snapshots/{snapshot_id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        elif mode == "restore":
            response = client.post(
                f"/api/v1/loops/batch-snapshots/{snapshot_id}/restore",
                json={"reason": "越权", "dryRun": True},
                headers={"Authorization": "Bearer fake-token"},
            )
        else:
            response = client.post(
                "/api/v1/loops/batch-config",
                json={
                    "loopIds": [str(uuid4())],
                    "updates": {"engineeringTemplateId": None},
                    "operationId": snapshot_id,
                    "dryRun": True,
                },
                headers={"Authorization": "Bearer fake-token"},
            )
    assert response.status_code == 403
