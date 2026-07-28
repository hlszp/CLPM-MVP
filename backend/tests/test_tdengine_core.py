"""TDengine core utility tests.

P3 #54：覆盖 make_subtable_name 公共函数 + _parse_tag_to_table_column 整合。
确保子表名生成规则统一，不出现散落多处实现导致的回归风险。
"""

from __future__ import annotations

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
