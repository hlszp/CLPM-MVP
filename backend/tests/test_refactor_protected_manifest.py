"""受保护算法文件零改动守卫（设计 §2.2 / 计划 §6 检查清单）.

- 默认模式：当前文件哈希必须与 golden 清单完全一致；
- 更新模式（仅限用户/主审授权的算法变更后重新固化）：
  ``REFACTOR_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_protected_manifest.py``
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.refactor.protected_manifest import manifest_with_meta

_GOLDEN = Path(__file__).parent / "golden" / "refactor_protected_manifest.json"


def test_protected_files_unchanged() -> None:
    current = manifest_with_meta()
    if os.environ.get("REFACTOR_UPDATE_GOLDEN") == "1":
        _GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        return
    assert _GOLDEN.exists(), (
        "golden 清单缺失：先运行 REFACTOR_UPDATE_GOLDEN=1 生成基线（命令见模块 docstring）"
    )
    baseline = json.loads(_GOLDEN.read_text())
    baseline_files = baseline["files"]
    current_files = current["files"]

    changed = {
        k: (baseline_files[k], current_files[k])
        for k in baseline_files
        if baseline_files.get(k) != current_files.get(k)
    }
    added = set(current_files) - set(baseline_files)
    removed = set(baseline_files) - set(current_files)
    assert not changed, (
        "受保护文件被修改（设计 §2.2 冻结）：\n"
        + "\n".join(f"  {k}: {a[:12]}→{b[:12]}" for k, (a, b) in sorted(changed.items()))
        + "\n如属用户/主审显式授权的算法变更，用 REFACTOR_UPDATE_GOLDEN=1 重新固化并在报告登记"
    )
    assert not added, f"protected 目录新增文件（需主审确认是否属算法变更）：{sorted(added)}"
    assert not removed, f"protected 目录文件被删除/改名：{sorted(removed)}"
