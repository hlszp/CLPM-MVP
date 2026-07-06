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
    """_parse_tag_to_table_column 整合测试（验证 make_subtable_name 已正确接入）。"""

    @pytest.mark.parametrize(
        ("tag_name", "expected_subtable", "expected_column", "expected_quality_col"),
        [
            # 标准 PV 角色
            (
                "HDS-RX-TIC-101.PV",
                "d_loop_hds_rx_tic_101",
                "pv",
                "pv_quality",
            ),
            # SP 角色（无质量列）
            (
                "LIC-101.SP",
                "d_loop_lic_101",
                "sp",
                None,
            ),
            # OP 角色
            (
                "LIC-101.OP",
                "d_loop_lic_101",
                "op",
                None,
            ),
            # MODE 角色
            (
                "LIC-101.MODE",
                "d_loop_lic_101",
                "mode",
                None,
            ),
            # PID_P 角色
            (
                "LIC-101.PID_P",
                "d_loop_lic_101",
                "pid_p",
                None,
            ),
            # PID_I 角色
            (
                "LIC-101.PID_I",
                "d_loop_lic_101",
                "pid_i",
                None,
            ),
            # PID_D 角色
            (
                "LIC-101.PID_D",
                "d_loop_lic_101",
                "pid_d",
                None,
            ),
            # 无角色后缀（默认 PV）
            (
                "LIC-101",
                "d_loop_lic_101",
                "pv",
                "pv_quality",
            ),
            # 未知角色（column 默认 pv，quality_col 走 _QUALITY_COLUMN_MAP.get → None）
            (
                "LIC-101.UNKNOWN",
                "d_loop_lic_101",
                "pv",
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

    def test_parse_tag_uses_make_subtable_name(self) -> None:
        """_parse_tag_to_table_column 的子表名应与 make_subtable_name 一致。

        这是 P3 #54 的核心防护：确保 _parse_tag_to_table_column 调用
        make_subtable_name 而非内联实现，防止规则散落回归。
        """
        # 测试多个典型 tag_name
        test_cases = [
            ("HDS-RX-TIC-101.PV", "HDS-RX-TIC-101"),
            ("LIC-101.SP", "LIC-101"),
            ("41FIC40504.PIDA.PV", "41FIC40504.PIDA"),
        ]
        for tag_name, expected_loop_part in test_cases:
            subtable, _, _ = _parse_tag_to_table_column(tag_name)
            assert subtable == make_subtable_name(expected_loop_part), (
                f"_parse_tag_to_table_column({tag_name!r}) subtable "
                f"({subtable!r}) != make_subtable_name({expected_loop_part!r}) "
                f"({_:= make_subtable_name(expected_loop_part)!r})"
            )
