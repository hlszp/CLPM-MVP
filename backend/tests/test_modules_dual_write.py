"""模块启用状态双写回归（P0 修复 2026-09-29）。

读路径（_load_sync/load_enabled_modules）优先 module_plugin 表，写路径若只更新
sys_config，PUT 的修改会在重启后被表内旧状态静默覆盖（迁移已 seed 8 行、表恒非空）。
锁定行为：save_enabled_modules 必须把状态同步进 module_plugin。
"""

from types import SimpleNamespace

import pytest

from app.core import modules as modules_core
from app.core.modules import _sync_module_plugin_statuses, save_enabled_modules


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.added: list = []
        self.commits = 0

    async def execute(self, *_args, **_kwargs):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _plugin_row(key: str, status: str, display: str = "旧名"):
    return SimpleNamespace(module_key=key, status=status, display_name=display)


@pytest.fixture(autouse=True)
def _reset_module_cache():
    modules_core.reset_cache()
    yield
    modules_core.reset_cache()


_BASE_KEYS = {"monitor", "assess", "reports", "config", "system"}


async def test_save_syncs_statuses_into_module_plugin_table(monkeypatch):
    """表空时补种 8 行：enabled→ENABLED、base→CORE、未启用→UNINSTALLED。"""
    monkeypatch.setattr(modules_core, "get_enabled_modules", lambda: set(modules_core.MODULES))
    db = _FakeDb([_FakeResult(scalar=SimpleNamespace(value="[]")), _FakeResult(rows=[])])

    saved = await save_enabled_modules(db, {"diagnosis", "handling"}, operator="tester")

    assert db.commits == 1
    assert len(db.added) == 8
    status_by_key = {row.module_key: row.status for row in db.added}
    assert status_by_key["diagnosis"] == "ENABLED"
    assert status_by_key["handling"] == "ENABLED"
    assert status_by_key["tuning"] == "UNINSTALLED"
    for base_key in _BASE_KEYS:
        assert status_by_key[base_key] == "CORE", base_key
    assert saved == set(modules_core.MODULES) - {"tuning"}


async def test_sync_overrides_stale_maintenance_status():
    """表中残留 MAINTENANCE 旧状态时被管理开关显式覆盖，display_name 不动。"""
    row = _plugin_row("diagnosis", "MAINTENANCE", display="回路诊断")
    db = _FakeDb([_FakeResult(rows=[row])])

    await _sync_module_plugin_statuses(db, set(modules_core.MODULES) - {"diagnosis"})

    assert row.status == "UNINSTALLED"
    assert row.display_name == "回路诊断"
    # diagnosis 原地更新；其余 7 个缺行模块补种
    assert {r.module_key for r in db.added} == set(modules_core.MODULES) - {"diagnosis"}
