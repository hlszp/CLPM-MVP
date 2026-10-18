"""受保护算法文件清单（设计 §2.2 冻结边界的机器可读登记）.

P0 生成；P3/P4/主审复验时以 ``tests/test_refactor_protected_manifest.py``
逐文件比对 SHA256——protected 区域出现任何差异必须逐行解释。

更新方式（仅当用户/主审显式授权算法变更时）：
    REFACTOR_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_protected_manifest.py

清单范围（设计 §2.2"受保护区域至少包括"）：
- metric_calculator/ 全目录、tuning_identification/ 全目录
- tuning_algorithms.py、diagnosis_orchestrator.py、tasks/arma.py
- 预处理阈值（采样频率/异常值阈值表 = "评估阈值"范畴）
- 数据契约结构 data_types.py（RawTimeSeries/DataBlock/MetricDataBundle 字段）
- preprocessing/quality_code.py（质量映射全局函数，设计 §4.1"不能改其映射"）
- preprocessing/pipeline.py（8 步预处理 = 指标准入门禁链路）

frontend/ 不在本清单：体量过大且由合并审查按 diff 范围把关
（本重构声明"默认不改前端"，主审以分支 diff 为准）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]  # tests/refactor/ → backend/

# (相对 backend/ 的路径, 保护理由)
PROTECTED_FILES: list[tuple[str, str]] = [
    ("app/contracts/data_types.py", "RawTimeSeries/DataBlock/MetricDataBundle 字段与空值语义"),
    ("app/services/preprocessing/quality_code.py", "质量码全局映射（设计 §4.1 禁改）"),
    ("app/services/preprocessing/pipeline.py", "8 步预处理（validity/门禁链路）"),
    ("app/services/preprocessing/thresholds.py", "控制类型采样频率与异常值阈值表"),
    ("app/services/tuning_algorithms.py", "整定算法主体"),
    ("app/services/diagnosis_orchestrator.py", "诊断编排（诊断规则入口）"),
    ("app/tasks/arma.py", "ARMA 辨识任务"),
]
PROTECTED_DIRS: list[tuple[str, str]] = [
    ("app/services/metric_calculator", "全部性能指标计算器"),
    ("app/services/tuning_identification", "辨识/整定算法主体"),
]


def collect_protected() -> dict[str, str]:
    """收集 {相对路径: sha256}（目录展开为全部 .py 文件，按路径排序）."""
    entries: dict[str, str] = {}
    for rel, _reason in PROTECTED_FILES:
        path = BACKEND_ROOT / rel
        entries[rel] = _sha256(path)
    for rel, _reason in PROTECTED_DIRS:
        for path in sorted((BACKEND_ROOT / rel).rglob("*.py")):
            entries[str(path.relative_to(BACKEND_ROOT))] = _sha256(path)
    return entries


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def manifest_with_meta() -> dict[str, object]:
    import subprocess

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=BACKEND_ROOT,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — git 不可用时保留占位（测试只比对文件哈希）
        head = "unknown"
    return {
        "baseline_head": head,
        "files": collect_protected(),
    }
