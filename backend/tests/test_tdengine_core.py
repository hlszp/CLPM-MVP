"""TDengine core utility tests.

P3 #54：覆盖 make_subtable_name 公共函数 + _parse_tag_to_table_column 整合。
确保子表名生成规则统一，不出现散落多处实现导致的回归风险。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.tdengine import _parse_tag_to_table_column, make_subtable_name


class TestMakeSubtableName:
    """make_subtable_name 公共函数测试。"""

    @pytest.mark.parametrize(
        ("loop_part", "expected"),
        [
            # 基础位号（不带 .）
            ("LIC-101", "d_loop_lic_101"),
            ("HDS-RX-TIC-101", "d_loop_hds_rx_tic_101"),
            # 含点号
            ("41FIC40504.PIDA", "d_loop_41fic40504_pida"),
            ("HDS.RX.TIC.101", "d_loop_hds_rx_tic_101"),
            # 大小写混合
            ("LIC-101.PV", "d_loop_lic_101_pv"),
            # 已含下划线
            ("LIC_101_PV", "d_loop_lic_101_pv"),
            # 连续多个下划线合并
            ("LIC---101", "d_loop_lic_101"),
            ("LIC...101", "d_loop_lic_101"),
            ("LIC-_.101", "d_loop_lic_101"),
            # 纯数字位号
            ("12345", "d_loop_12345"),
            # 空字符串边界（只返回前缀）
            ("", "d_loop_"),
        ],
    )
    def test_make_subtable_name_cases(self, loop_part: str, expected: str) -> None:
        """参数化覆盖常见位号格式。"""
        assert make_subtable_name(loop_part) == expected

    def test_make_subtable_name_lowercases(self) -> None:
        """所有字母必须转小写。"""
        assert make_subtable_name("UPPER-CASE") == "d_loop_upper_case"
        assert make_subtable_name("MixedCase") == "d_loop_mixedcase"

    def test_make_subtable_name_replaces_hyphen_and_dot(self) -> None:
        """连字符和点号必须替换为下划线。"""
        result = make_subtable_name("a-b.c-d.e")
        assert result == "d_loop_a_b_c_d_e"

    def test_make_subtable_name_collapses_repeated_underscores(self) -> None:
        """连续多个下划线必须合并为单个。"""
        assert make_subtable_name("a___b") == "d_loop_a_b"
        assert make_subtable_name("a---b") == "d_loop_a_b"

    def test_make_subtable_name_idempotent(self) -> None:
        """对已规范化结果再调用应保持稳定。"""
        result = make_subtable_name("LIC-101")
        assert make_subtable_name(result) == "d_loop_d_loop_lic_101"


class TestParseTagToTableColumn:
    """_parse_tag_to_table_column 测试（对齐实际 signal_sim TDengine schema）。

    实际 schema：
    - 子表名: t_<tag_name_lower>（保留完整 tag 名含角色后缀）
    - 数据列: val（所有表统一）
    - 质量列: None（无质量码列）
    """

    @pytest.mark.parametrize(
        ("tag_name", "expected_subtable", "expected_column", "expected_quality_col"),
        [
            # 标准 PV 角色
            (
                "HDS-RX-TIC-101.PV",
                "t_hds_rx_tic_101_pv",
                "val",
                None,
            ),
            # SP 角色
            (
                "LIC-101.SP",
                "t_lic_101_sp",
                "val",
                None,
            ),
            # OP 角色
            (
                "LIC-101.OP",
                "t_lic_101_op",
                "val",
                None,
            ),
            # MODE 角色
            (
                "LIC-101.MODE",
                "t_lic_101_mode",
                "val",
                None,
            ),
            # PID_P 角色
            (
                "LIC-101.PID_P",
                "t_lic_101_pid_p",
                "val",
                None,
            ),
            # PID_I 角色
            (
                "LIC-101.PID_I",
                "t_lic_101_pid_i",
                "val",
                None,
            ),
            # PID_D 角色
            (
                "LIC-101.PID_D",
                "t_lic_101_pid_d",
                "val",
                None,
            ),
            # 无角色后缀（完整 tag 名直接转换）
            (
                "LIC-101",
                "t_lic_101",
                "val",
                None,
            ),
            # 未知角色（仍保留在表名中）
            (
                "LIC-101.UNKNOWN",
                "t_lic_101_unknown",
                "val",
                None,
            ),
            # 三段式 tag 名（实际 signal_sim 格式）
            (
                "41FIC20021.PIDA.PV",
                "t_41fic20021_pida_pv",
                "val",
                None,
            ),
        ],
    )
    def test_parse_tag_to_table_column(
        self,
        tag_name: str,
        expected_subtable: str,
        expected_column: str,
        expected_quality_col,
    ) -> None:
        """tag_name 解析为子表名 + 列名 + 质量列名。"""
        subtable, column, quality_col = _parse_tag_to_table_column(tag_name)
        assert subtable == expected_subtable
        assert column == expected_column
        assert quality_col == expected_quality_col


class TestMakeSubtableNameWhitelist:
    """P1：make_subtable_name 白名单归一化，防宽表 SQL 拼接注入."""

    @pytest.mark.parametrize(
        ("loop_part", "expected"),
        [
            # 单引号（SQL 注入面）归一化为下划线
            ("LIC'101", "d_loop_lic_101"),
            # 双引号
            ('LIC"101', "d_loop_lic_101"),
            # 空格
            ("LIC 101", "d_loop_lic_101"),
            # 分号 + SQL 注入片段：非法字符全部归一化，不形成可执行注入
            ("a'; DROP TABLE x; --", "d_loop_a_drop_table_x_"),
            # 中文等非法字符归一化为下划线
            ("回路-101", "d_loop__101"),
            # 反斜杠
            ("LIC\\101", "d_loop_lic_101"),
        ],
    )
    def test_illegal_chars_normalized(self, loop_part: str, expected: str) -> None:
        """非法字符（引号/空格/分号/中文等）必须替换为下划线。"""
        assert make_subtable_name(loop_part) == expected

    def test_legal_names_unchanged(self) -> None:
        """合法字符集（字母/数字/-/_/.）生成结果必须与归一化前一致。"""
        assert make_subtable_name("HDS-RX-TIC-101") == "d_loop_hds_rx_tic_101"
        assert make_subtable_name("41FIC40504.PIDA") == "d_loop_41fic40504_pida"
        assert make_subtable_name("LIC-_.101") == "d_loop_lic_101"

    def test_normalized_output_is_sql_safe(self) -> None:
        """归一化结果只含字母/数字/下划线/前缀，无注入面。"""
        import re as _re

        result = make_subtable_name("x'; DELETE FROM d_loop_a WHERE '1'='1")
        assert _re.fullmatch(r"d_loop_[a-z0-9_]*", result)


class TestParseTsStr:
    """P2：_parse_ts_str 解析失败返回 None，不再用 datetime.now() 兜底."""

    def test_iso_string(self) -> None:
        from app.core.tdengine import _parse_ts_str

        assert _parse_ts_str("2026-07-15T10:00:00") == datetime(2026, 7, 15, 10, 0, 0)

    def test_iso_with_z(self) -> None:
        from app.core.tdengine import _parse_ts_str

        assert _parse_ts_str("2026-07-15T10:00:00Z") == datetime(2026, 7, 15, 10, 0, 0)

    def test_epoch_string(self) -> None:
        from datetime import UTC

        from app.core.tdengine import _parse_ts_str

        epoch = datetime(2026, 7, 15, 10, 0, 0, tzinfo=UTC).timestamp()
        result = _parse_ts_str(str(epoch))
        assert result is not None
        # fromtimestamp(tz=None) 按本地时区还原，比较回 epoch 时刻
        assert result.timestamp() == pytest.approx(epoch)

    def test_invalid_returns_none(self) -> None:
        from app.core.tdengine import _parse_ts_str

        assert _parse_ts_str("not-a-timestamp") is None
        assert _parse_ts_str("") is None


class TestExecuteSqlErrorDistinction:
    """P2：execute_sql 区分'数据源故障'与'真无数据'.

    默认（raise_on_error=False）保持旧行为降级返回 []；
    raise_on_error=True 时连接/查询失败抛 TDengineError。
    """

    @staticmethod
    def _mock_client(
        *, status_code: int = 200, payload: dict | None = None, exc: Exception | None = None
    ):
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock()
        if exc is not None:
            client.post = AsyncMock(side_effect=exc)
        else:
            resp = MagicMock()
            resp.status_code = status_code
            resp.text = "error-body"
            resp.json = MagicMock(return_value=payload or {})
            client.post = AsyncMock(return_value=resp)
        return client

    @pytest.mark.asyncio
    async def test_connection_error_default_returns_empty(self) -> None:
        from unittest.mock import AsyncMock, patch

        import httpx

        from app.core.tdengine import execute_sql

        client = self._mock_client(exc=httpx.ConnectError("connection refused"))
        with patch("app.core.tdengine._get_client", new=AsyncMock(return_value=client)):
            assert await execute_sql("SELECT 1") == []

    @pytest.mark.asyncio
    async def test_connection_error_raises_when_requested(self) -> None:
        from unittest.mock import AsyncMock, patch

        import httpx

        from app.core.tdengine import TDengineError, execute_sql

        client = self._mock_client(exc=httpx.ConnectError("connection refused"))
        with (
            patch("app.core.tdengine._get_client", new=AsyncMock(return_value=client)),
            pytest.raises(TDengineError),
        ):
            await execute_sql("SELECT 1", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_http_error_raises_when_requested(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.core.tdengine import TDengineError, execute_sql

        client = self._mock_client(status_code=500)
        with patch("app.core.tdengine._get_client", new=AsyncMock(return_value=client)):
            assert await execute_sql("SELECT 1") == []
            with pytest.raises(TDengineError):
                await execute_sql("SELECT 1", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_tdengine_error_code_raises_when_requested(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.core.tdengine import TDengineError, execute_sql

        client = self._mock_client(payload={"code": 380, "message": "Table does not exist"})
        with patch("app.core.tdengine._get_client", new=AsyncMock(return_value=client)):
            assert await execute_sql("SELECT 1") == []
            with pytest.raises(TDengineError):
                await execute_sql("SELECT 1", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_success_returns_rows(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.core.tdengine import execute_sql

        client = self._mock_client(
            payload={
                "code": 0,
                "column_meta": [["a", "INT", 4]],
                "data": [[1], [2]],
            }
        )
        with patch("app.core.tdengine._get_client", new=AsyncMock(return_value=client)):
            rows = await execute_sql("SELECT 1", raise_on_error=True)
        assert rows == [{"a": 1}, {"a": 2}]


class TestQueryTrendDataErrorDistinction:
    """P2：query_trend_data 同样支持 raise_on_error 区分故障与无数据."""

    @pytest.mark.asyncio
    async def test_http_error_raises_when_requested(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.core.tdengine import TDengineError, query_trend_data

        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 503
        resp.text = "unavailable"
        client.post = AsyncMock(return_value=resp)

        with patch("app.core.tdengine._get_client", new=AsyncMock(return_value=client)):
            # 默认降级为空数组（旧行为兼容）
            rows = await query_trend_data(
                "LIC-101.PV", "2026-07-01T00:00:00Z", "2026-07-02T00:00:00Z"
            )
            assert rows == []
            with pytest.raises(TDengineError):
                await query_trend_data(
                    "LIC-101.PV",
                    "2026-07-01T00:00:00Z",
                    "2026-07-02T00:00:00Z",
                    raise_on_error=True,
                )


class TestQueryWideTableNativeChunking:
    """P2：query_wide_table_native 超过 7 天窗口按日分片，结果与单片一致."""

    @staticmethod
    def _parse_sql_bounds(sql: str) -> tuple[str, str, bool]:
        """从 SQL 中解析 ts 上下界与是否闭区间."""
        import re as _re

        m = _re.search(r"ts >= '([^']+)' AND ts (<=|<) '([^']+)'", sql)
        assert m, f"SQL 格式不符: {sql}"
        return m.group(1), m.group(3), m.group(2) == "<="

    @pytest.mark.asyncio
    async def test_small_window_single_query(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.core.tdengine_native import query_wide_table_native

        mock_exec = AsyncMock(return_value=[{"ts": "2026-07-01T00:00:00.000Z"}])
        with patch("app.core.tdengine_native.execute_native", new=mock_exec):
            rows = await query_wide_table_native(
                "d_loop_x", "2026-07-01T00:00:00.000Z", "2026-07-07T00:00:00.000Z"
            )
        assert mock_exec.call_count == 1
        assert "ts <= '2026-07-07T00:00:00.000Z'" in mock_exec.call_args.args[0]
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_large_window_splits_by_day(self) -> None:
        from unittest.mock import AsyncMock, patch

        from app.core.tdengine_native import query_wide_table_native

        calls: list[str] = []

        async def fake_exec(sql: str) -> list[dict]:
            calls.append(sql)
            return [{"ts": f"row-{len(calls)}"}]

        with patch("app.core.tdengine_native.execute_native", new=AsyncMock(side_effect=fake_exec)):
            rows = await query_wide_table_native(
                "d_loop_x", "2026-07-01T06:00:00.000Z", "2026-07-10T06:00:00.000Z"
            )

        # 9 天窗口 → 10 个自然日分片（首末各半天 + 中间 8 整天）
        assert len(calls) == 10
        # 结果流式拼接
        assert rows == [{"ts": f"row-{i}"} for i in range(1, 11)]
        # 首片用原始 start 串
        start_s, _, _ = self._parse_sql_bounds(calls[0])
        assert start_s == "2026-07-01T06:00:00.000Z"
        # 末片闭区间、用原始 end 串
        _, end_s, inclusive = self._parse_sql_bounds(calls[-1])
        assert end_s == "2026-07-10T06:00:00.000Z"
        assert inclusive is True
        # 中间分片半开区间（< 次日 00:00），边界点不重复
        for sql in calls[:-1]:
            _, mid_end, mid_inclusive = self._parse_sql_bounds(sql)
            assert mid_inclusive is False
            assert mid_end.endswith("T00:00:00.000Z")

    @pytest.mark.asyncio
    async def test_chunked_result_equals_single_query(self) -> None:
        """分片查询结果必须与单片 `ts >= start AND ts <= end` 完全一致（无重复无遗漏）."""
        from unittest.mock import AsyncMock, patch

        from app.core.tdengine_native import query_wide_table_native

        start, end = "2026-07-01T06:00:00.000Z", "2026-07-10T06:00:00.000Z"
        # 构造覆盖分片边界的数据集（含整点午夜边界点）
        dataset = [
            {"ts": "2026-07-01T05:59:59.000Z"},  # 窗口前
            {"ts": "2026-07-01T06:00:00.000Z"},  # 窗口起点
            {"ts": "2026-07-02T00:00:00.000Z"},  # 分片边界（午夜）
            {"ts": "2026-07-05T12:34:56.000Z"},
            {"ts": "2026-07-10T00:00:00.000Z"},  # 分片边界（午夜）
            {"ts": "2026-07-10T06:00:00.000Z"},  # 窗口终点
            {"ts": "2026-07-10T06:00:01.000Z"},  # 窗口后
        ]

        async def fake_exec(sql: str) -> list[dict]:
            s, e, inclusive = self._parse_sql_bounds(sql)
            if inclusive:
                return [r for r in dataset if s <= r["ts"] <= e]
            return [r for r in dataset if s <= r["ts"] < e]

        with patch("app.core.tdengine_native.execute_native", new=AsyncMock(side_effect=fake_exec)):
            chunked = await query_wide_table_native("d_loop_x", start, end)

        expected = [r for r in dataset if start <= r["ts"] <= end]
        assert chunked == expected

    @pytest.mark.asyncio
    async def test_naive_window_chunk_boundaries_stay_naive(self) -> None:
        """naive 输入的分片边界保持 naive 格式（不改变服务器时区解释口径）."""
        from unittest.mock import AsyncMock, patch

        from app.core.tdengine_native import query_wide_table_native

        calls: list[str] = []

        async def fake_exec(sql: str) -> list[dict]:
            calls.append(sql)
            return []

        with patch("app.core.tdengine_native.execute_native", new=AsyncMock(side_effect=fake_exec)):
            await query_wide_table_native("d_loop_x", "2026-07-01T00:00:00", "2026-07-10T00:00:00")

        assert len(calls) == 9
        for sql in calls:
            s, e, _ = self._parse_sql_bounds(sql)
            assert "Z" not in s and "Z" not in e


class TestConnectionPoolTimezone:
    """P0-3：taosrest 连接固定 timezone=UTC，TIMESTAMP 列返回 aware UTC."""

    def test_create_connection_pins_utc_timezone(self) -> None:
        from datetime import UTC
        from unittest.mock import MagicMock, patch

        from app.core.tdengine_native import TDengineConnectionPool

        with patch("taosrest.connect", return_value=MagicMock()) as mock_connect:
            TDengineConnectionPool._create_connection()

        assert mock_connect.call_count == 1
        assert mock_connect.call_args.kwargs["timezone"] is UTC
