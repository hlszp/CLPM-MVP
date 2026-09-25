"""写入布局 与 读取路由 一致性自检的回归（2026-09-25 生产排查新增）。

覆盖：五种组合的判定、回写语义陷阱提示、平台兼容探活辅助。
这些都是"配置看起来对、数据却没落库/趋势全空"的判定核心，判定错会把现场
引向错误方向，因此逐组合锁定。
"""

from __future__ import annotations

import os

import pytest

from app.services.data_source import history_layout as hl


class _FakeScalars:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def first(self) -> object | None:
        return self._row


class _FakeResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._row)


class _FakeDB:
    """只支持 manifest 那一句查询的最小桩。"""

    def __init__(self, row: object | None = None) -> None:
        self._row = row

    async def execute(self, *_args: object, **_kwargs: object) -> _FakeResult:
        return _FakeResult(self._row)


@pytest.fixture
def patch_layout(monkeypatch: pytest.MonkeyPatch):
    """把 storage_mode 与读取路由换成可控值。"""

    def _apply(write_mode: str, read_layout: str) -> None:
        async def _mode(_db: object) -> str:
            return write_mode

        async def _resolve(_db: object, **_kwargs: object) -> str:
            return read_layout

        monkeypatch.setattr(hl, "get_storage_mode", _mode)
        from app.services.data_source import point_history_metadata as phm

        monkeypatch.setattr(phm, "resolve_layout", _resolve)

    return _apply


class TestSelfCheckMatrix:
    async def test_legacy_write_and_legacy_read_is_consistent(self, patch_layout) -> None:
        patch_layout("legacy", "legacy")
        check = await hl.get_layout_selfcheck(_FakeDB())
        assert check["consistent"] is True
        assert check["severity"] == hl.SELFCHECK_OK
        assert check["writesWideTable"] is True
        assert check["writesPointTable"] is False

    async def test_point_write_and_point_read_is_consistent(self, patch_layout) -> None:
        patch_layout("point", "point")
        check = await hl.get_layout_selfcheck(_FakeDB())
        assert check["consistent"] is True
        assert check["writesPointTable"] is True

    async def test_shadow_write_consistent_with_both_reads(self, patch_layout) -> None:
        for read_layout in ("legacy", "point"):
            patch_layout("shadow", read_layout)
            check = await hl.get_layout_selfcheck(_FakeDB())
            assert check["consistent"] is True, read_layout

    async def test_point_write_with_legacy_read_is_error(self, patch_layout) -> None:
        """写点表、读宽表 → 趋势读到空表（生产"趋势图没有任何数据"的典型组合）。"""
        patch_layout("point", "legacy")
        check = await hl.get_layout_selfcheck(_FakeDB())
        assert check["consistent"] is False
        assert check["severity"] == hl.SELFCHECK_ERROR
        assert "空的宽表" in check["diagnosis"]
        assert "layout=point" in check["diagnosis"]

    async def test_legacy_write_with_point_read_is_error(self, patch_layout) -> None:
        """读点表、写宽表 → 点表无数据，趋势全空。"""
        patch_layout("legacy", "point")
        check = await hl.get_layout_selfcheck(_FakeDB())
        assert check["consistent"] is False
        assert check["severity"] == hl.SELFCHECK_ERROR
        assert "storage_mode" in check["diagnosis"]

    async def test_resolve_failure_degrades_to_warning(self, monkeypatch) -> None:
        """读取路由解析失败时必须显性告警，而不是假装一致。"""

        async def _mode(_db: object) -> str:
            return "point"

        async def _boom(_db: object, **_kwargs: object) -> str:
            raise RuntimeError("manifest 表不可读")

        monkeypatch.setattr(hl, "get_storage_mode", _mode)
        from app.services.data_source import point_history_metadata as phm

        monkeypatch.setattr(phm, "resolve_layout", _boom)

        check = await hl.get_layout_selfcheck(_FakeDB())
        assert check["consistent"] is None
        assert check["severity"] == hl.SELFCHECK_WARNING
        assert "无法判定" in check["diagnosis"]


class TestWritebackTrapHint:
    def test_hint_fires_on_legacy_with_writeback_on(self) -> None:
        hint = hl.writeback_trap_hint("legacy", True)
        assert hint is not None
        assert "storage_mode" in hint
        assert "st_point_data_v1" in hint

    def test_hint_silent_when_writer_will_start(self) -> None:
        assert hl.writeback_trap_hint("shadow", True) is None
        assert hl.writeback_trap_hint("point", True) is None

    def test_hint_silent_when_writeback_off(self) -> None:
        assert hl.writeback_trap_hint("legacy", False) is None


class TestPlatformHelpers:
    """Windows 兼容改动的非 Windows 侧行为（Windows 分支在 CI 上无法实跑）。"""

    def test_pid_alive_for_self_and_missing(self) -> None:
        from app.main import _pid_alive

        assert _pid_alive(os.getpid()) is True
        assert _pid_alive(999_999) is False

    def test_celery_autostart_escape_hatch(self, monkeypatch) -> None:
        from app.main import _celery_autostart_forced

        monkeypatch.delenv("CELERY_AUTOSTART", raising=False)
        assert _celery_autostart_forced() is False
        for raw in ("1", "true", "TRUE", " yes ", "on"):
            monkeypatch.setenv("CELERY_AUTOSTART", raw)
            assert _celery_autostart_forced() is True, raw
        monkeypatch.setenv("CELERY_AUTOSTART", "0")
        assert _celery_autostart_forced() is False
