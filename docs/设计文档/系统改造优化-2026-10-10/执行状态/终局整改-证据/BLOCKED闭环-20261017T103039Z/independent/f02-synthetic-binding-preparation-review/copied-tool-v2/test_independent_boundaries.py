"""Non-author controls; fake DB only, and inherited deny-network fixtures."""

import json

import pytest

from test_prepare_bindings import Store, Transaction, no_external_io, original, tool


@pytest.mark.asyncio
async def test_readonly_exit_failure_after_commit_never_claims_verified(original, tmp_path, monkeypatch):
    plan, reference = original
    store = Store(plan, reference)
    exit_original = Transaction.__aexit__

    async def failed_readonly_exit(self, kind, value, traceback):
        await exit_original(self, kind, value, traceback)
        if self.conn.number == 3:
            raise OSError("independent failure at complete RO transaction exit")

    monkeypatch.setattr(Transaction, "__aexit__", failed_readonly_exit)
    with pytest.raises(OSError):
        await tool.apply(plan, reference, tmp_path / "case", store.connect, store.backup, store.tasks)
    state = json.loads((tmp_path / "case/state.json").read_text())
    assert store.commits == 1 and state["committed"] is True
    assert state["persistedReadbackVerified"] is False
    assert all(connection.closed for connection in store.connections)


@pytest.mark.asyncio
async def test_task_appearing_after_backup_blocks_update(original, tmp_path):
    plan, reference = original
    store = Store(plan, reference)
    calls = 0

    async def tasks():
        nonlocal calls
        calls += 1
        return {"taskKeys": [], "brokerPendingOrUnacked": int(calls == 2), "celeryResultKeys": []}

    with pytest.raises(ValueError):
        await tool.apply(plan, reference, tmp_path / "case", store.connect, store.backup, tasks)
    assert calls == 2 and tool.UPDATE_SQL not in store.commands and store.commits == 0
    tool.same(store.tables, reference)
    state = json.loads((tmp_path / "case/state.json").read_text())
    assert state["committed"] is False and state["persistedReadbackVerified"] is False


@pytest.mark.asyncio
async def test_existing_evidence_output_never_overwritten_or_connects(original, tmp_path):
    plan, reference = original
    store = Store(plan, reference)
    output = tmp_path / "immutable"
    output.mkdir()
    marker = output / "original.txt"
    marker.write_text("original evidence")
    with pytest.raises(FileExistsError):
        await tool.apply(plan, reference, output, store.connect, store.backup, store.tasks)
    assert marker.read_text() == "original evidence" and store.connections == []
