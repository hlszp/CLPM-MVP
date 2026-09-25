"""落库形态自检 + 宽表退役结构性断言的回归（2026-09-25）.

宽表超级表退役后：写入与读取唯一形态是测点点表。本文件锁定
① 自检结论恒为单态一致（历史值仅展示，不误报警）；
② app/ 内不再出现宽表表名与宽表写函数（防回流）。

平台兼容探活（_pid_alive / CELERY_AUTOSTART 逃生阀）的用例保留在同一文件末尾。
"""

from __future__ import annotations

import os
import pathlib

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
    """只支持 manifest 那句查询的最小桩。"""

    def __init__(self, row: object | None = None) -> None:
        self._row = row

    async def execute(self, *_args: object, **_kwargs: object) -> _FakeResult:
        return _FakeResult(self._row)


class TestSingleStateSelfCheck:
    async def test_point_mode_is_consistent(self, monkeypatch) -> None:
        async def _mode(_db: object) -> str:
            return "point"

        monkeypatch.setattr(hl, "get_storage_mode", _mode)
        check = await hl.get_layout_selfcheck(_FakeDB())
        assert check["writeMode"] == "point"
        assert check["readLayout"] == "point"
        assert check["writesPointTable"] is True
        assert check["consistent"] is True
        assert check["severity"] == hl.SELFCHECK_OK
        assert check["diagnosis"]

    async def test_retired_value_is_display_only(self, monkeypatch) -> None:
        """sys_config 里的历史 legacy/shadow 值只作展示，不得判为不一致（否则误报警）."""

        async def _mode(_db: object) -> str:
            return "legacy"

        monkeypatch.setattr(hl, "get_storage_mode", _mode)
        check = await hl.get_layout_selfcheck(_FakeDB())
        assert check["readLayout"] == "point", "读取路由恒点表"
        assert check["consistent"] is True
        assert check["severity"] == hl.SELFCHECK_OK
        assert "已不作数" in check["diagnosis"]

    async def test_manifest_read_failure_still_reports_ok(self, monkeypatch) -> None:
        """manifest 读取失败不影响落库形态判定（形态由代码固定）."""

        async def _mode(_db: object) -> str:
            return "point"

        class _BrokenDB:
            async def execute(self, *_a: object, **_k: object) -> object:
                raise RuntimeError("PG down")

        monkeypatch.setattr(hl, "get_storage_mode", _mode)
        check = await hl.get_layout_selfcheck(_BrokenDB())
        assert check["severity"] == hl.SELFCHECK_OK
        assert check["manifest"] is None


class TestWideTableRetired:
    """结构性断言：宽表代码不得回流（用户显式要求，2026-09-25）."""

    def test_no_wide_table_reference_in_app(self) -> None:
        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        hits = [
            str(p.relative_to(app_dir))
            for p in app_dir.rglob("*.py")
            if "st_loop_data" in p.read_text(encoding="utf-8")
        ]
        assert hits == [], f"宽表超级表已退役，app/ 不应再引用：{hits}"

    def test_wide_writer_symbols_removed(self) -> None:
        import app.core.tdengine_native as native

        for name in (
            "batch_insert",
            "batch_insert_multi",
            "query_wide_table_native",
            "query_last_values_before",
            "_format_row",
            "ensure_subtable",
        ):
            assert not hasattr(native, name), f"宽表写函数未清理：{name}"

    def test_legacy_read_path_removed(self) -> None:
        import app.services.data_source.tdengine_provider as provider

        assert not hasattr(provider, "_legacy_wide_rows_query")
        assert not hasattr(provider, "_subtable_cache")

    def test_writeback_trap_hint_removed(self) -> None:
        """回写开关语义陷阱提示随宽表退役失去意义，必须删除（防止误报）."""
        assert not hasattr(hl, "writeback_trap_hint")


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
