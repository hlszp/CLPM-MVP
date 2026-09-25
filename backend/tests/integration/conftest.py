"""集成测试（-m integration，默认不执行）的收集配置。

2026-09-25 宽表退役（S1+S2）：宽表 `st_loop_data` 的写入路径与 provider legacy 分支
已被删除，以下两个文件断言的正是该形态，且 import 了已删符号
（`_write_td_chunks` / `query_wide_table_native` / `_legacy_query` 等）。

处理方式：`collect_ignore` 让 pytest 不收集它们（避免 `-m integration` 收集期 ImportError
直接中断整套集成测试），文件本身作为历史记录保留，待重写为点表口径后再移出本清单。
"""

collect_ignore = [
    # 宽表读写 / shadow 双写 / legacy 回退形态断言（待重写）
    "test_refactor_acceptance.py",
    "test_refactor_writer_and_import.py",
]
