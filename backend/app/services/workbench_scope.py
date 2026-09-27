"""工作台 scope_id 口径（单一事实源）。

预计算（写 workbench_window_summary）与总览（读）**必须**使用同一函数，否则两侧错位，
表现为装置/单元排名空白。

- GLOBAL 恒为 0（不由本函数产生）；
- 节点优先用 AAS 整数编号 `PlantNode.source_node_id`；
- 缺失时用节点 UUID 的 CRC32 派生**稳定正整数**，落在保留段 [0x40000000, 0x7FFFFFFF]，
  避免与 AAS 编号冲突 —— 使未同步 AAS 编号的环境（如种子数据的开发环境）也能出排名。
"""

from __future__ import annotations

import zlib

FALLBACK_BASE = 0x40000000
FALLBACK_MASK = 0x3FFFFFFF


def node_scope_id(source_node_id: object, node_uuid: str) -> int:
    """节点 → scope_id（与 workbench_window_summary.scope_id 的 integer 列对齐）。"""
    if source_node_id is not None:
        try:
            return int(source_node_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    return FALLBACK_BASE | (zlib.crc32(node_uuid.encode("utf-8")) & FALLBACK_MASK)
