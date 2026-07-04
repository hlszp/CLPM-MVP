#!/usr/bin/env python3
"""5 级性能定级修改前后对比报告生成器。

背景：
    原定级逻辑（3 级）：
        score >= 80 → GOOD, score >= 60 → WARNING, score < 60 → POOR
    新定级逻辑（5 级，对齐 GB/T 44693.2-2024 §6.3 性能分级）：
        score >= 90 → EXCELLENT, 80-89 → GOOD, 70-79 → FAIR,
        60-69 → WARNING, < 60 → POOR

本脚本：
    1. 读取测试数据 tests/fixtures/kpi_test_data.json（7 个场景）
    2. 对每个场景运行 KPI 计算（_compute_kpis + _compute_composite_score）
    3. 对综合评分分别用旧逻辑（3 级）和新逻辑（5 级）定级
    4. 生成 Markdown 对比报告 → scripts/grading_comparison_report.md

用法::

    cd /Users/zhangping/DEV/CLPM/backend && uv run python scripts/generate_grading_report.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

# 日志级别设为 WARNING，避免 DEBUG 输出干扰报告
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from app.tasks.kpi_calc import _compute_composite_score, _compute_kpis  # noqa: E402

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURE_PATH = BACKEND_DIR / "tests" / "fixtures" / "kpi_test_data.json"
REPORT_PATH = BACKEND_DIR / "scripts" / "grading_comparison_report.md"

# 7 个场景（固定顺序）
SCENARIOS = [
    "fast_response",
    "slow_response",
    "oscillation",
    "op_saturation",
    "normal",
    "manual_mode",
    "pure_ar2",
]


# ---------------------------------------------------------------------------
# 定级函数
# ---------------------------------------------------------------------------


def grade_old(score: Decimal | float | None) -> str:
    """旧定级逻辑（3 级）。

    - score >= 80 → GOOD
    - score >= 60 → WARNING
    - score < 60  → POOR
    - None        → INCONCLUSIVE
    """
    if score is None:
        return "INCONCLUSIVE"
    s = float(score)
    if s >= 80:
        return "GOOD"
    if s >= 60:
        return "WARNING"
    return "POOR"


def grade_new(score: Decimal | float | None) -> str:
    """新定级逻辑（5 级，对齐 GB/T 44693.2-2024 §6.3）。

    - score >= 90 → EXCELLENT
    - 80 ~ 89     → GOOD
    - 70 ~ 79     → FAIR
    - 60 ~ 69     → WARNING
    - < 60        → POOR
    - None        → INCONCLUSIVE
    """
    if score is None:
        return "INCONCLUSIVE"
    s = float(score)
    if s >= 90:
        return "EXCELLENT"
    if s >= 80:
        return "GOOD"
    if s >= 70:
        return "FAIR"
    if s >= 60:
        return "WARNING"
    return "POOR"


# ---------------------------------------------------------------------------
# 指标配置
# ---------------------------------------------------------------------------


def make_metric_configs() -> dict[str, SimpleNamespace]:
    """构造国标 4 分项评分指标配置。

    权重对齐 GB/T 44693.2-2024 综合评分公式：
        P = (λA·A + λF·F + λS·S + λR·R) / (λA + λF + λS + λR)
        - A = accuracy_rate（准确率）       权重 30
        - F = fast_rate（快速率）  权重 20
        - S = steady_rate（平稳率）         权重 30
        - R = effective_auto_rate（有效自控率） 权重 20
    """
    return {
        "accuracy_rate": SimpleNamespace(
            metric_code="accuracy_rate", weight=Decimal("30"), is_enabled=True
        ),
        "fast_rate": SimpleNamespace(
            metric_code="fast_rate", weight=Decimal("20"), is_enabled=True
        ),
        "steady_rate": SimpleNamespace(
            metric_code="steady_rate", weight=Decimal("30"), is_enabled=True
        ),
        "effective_auto_rate": SimpleNamespace(
            metric_code="effective_auto_rate", weight=Decimal("20"), is_enabled=True
        ),
    }


# ---------------------------------------------------------------------------
# 数据加载与计算
# ---------------------------------------------------------------------------


def load_scenario(name: str) -> dict:
    """加载指定场景的测试数据。"""
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    if name not in data:
        raise KeyError(f"场景 {name} 不存在于 {FIXTURE_PATH}")
    return data[name]


def compute_scenario(name: str) -> dict:
    """计算单个场景的 KPI、综合评分及新旧定级。

    Returns:
        {
            "scenario": str,
            "description": str,
            "score": Decimal,
            "old_grade": str,
            "new_grade": str,
            "changed": bool,
            "kpis": dict,
        }
    """
    scenario_data = load_scenario(name)
    aligned = scenario_data["data"]
    description = scenario_data.get("description", "")

    configs = make_metric_configs()

    # 好值率设为 100.0（按任务要求）
    good_value_rate = Decimal("100.0")
    kpi_values = _compute_kpis(aligned, configs, good_value_rate=good_value_rate)

    # 综合评分
    score = _compute_composite_score(kpi_values, configs)

    old_grade = grade_old(score)
    new_grade = grade_new(score)
    changed = old_grade != new_grade

    return {
        "scenario": name,
        "description": description,
        "score": score,
        "old_grade": old_grade,
        "new_grade": new_grade,
        "changed": changed,
        "kpis": kpi_values,
    }


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


def generate_report(results: list[dict]) -> str:
    """生成 Markdown 格式的对比报告。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed_count = sum(1 for r in results if r["changed"])
    unchanged_count = len(results) - changed_count

    lines: list[str] = []

    # ── 标题 ──
    lines.append("# 5 级性能定级修改前后对比报告")
    lines.append("")
    lines.append(f"> 生成时间：{now_str}")
    lines.append("> 算法版本：KPI_CALC_v1.0")
    lines.append(f"> 测试场景数：{len(results)}")
    lines.append("")

    # ── 1. 修改说明 ──
    lines.append("## 1. 修改说明")
    lines.append("")
    lines.append(
        "本次修改将控制回路性能定级由 **3 级** 调整为 **5 级**，"
        "对齐国家标准 **GB/T 44693.2-2024《控制系统性能评价 第 2 部分：控制回路》"
        "§6.3 性能分级** 要求，实现更细粒度的性能区分。"
    )
    lines.append("")
    lines.append("**修改前（3 级）：**")
    lines.append("")
    lines.append("| 评分区间 | 定级 |")
    lines.append("|----------|------|")
    lines.append("| score >= 80 | GOOD（良）|")
    lines.append("| 60 <= score < 80 | WARNING（差）|")
    lines.append("| score < 60 | POOR（劣）|")
    lines.append("| None | INCONCLUSIVE（不确定）|")
    lines.append("")
    lines.append("**修改后（5 级）：**")
    lines.append("")
    lines.append("| 评分区间 | 定级 |")
    lines.append("|----------|------|")
    lines.append("| score >= 90 | EXCELLENT（优）|")
    lines.append("| 80 <= score < 90 | GOOD（良）|")
    lines.append("| 70 <= score < 80 | FAIR（中）|")
    lines.append("| 60 <= score < 70 | WARNING（差）|")
    lines.append("| score < 60 | POOR（劣）|")
    lines.append("| None | INCONCLUSIVE（不确定）|")
    lines.append("")
    lines.append(
        "新逻辑实现在 `app/services/performance.py` 的 `_score_to_status` 函数中，"
        "原 3 级 `WARNING` 区间（60~79）被拆分为 `FAIR`（70~79）和 `WARNING`（60~69），"
        "原 `GOOD` 区间（>=80）被拆分为 `EXCELLENT`（>=90）和 `GOOD`（80~89）。"
    )
    lines.append("")

    # ── 2. 定级变化表格 ──
    lines.append("## 2. 7 个场景定级变化对比")
    lines.append("")
    lines.append(
        "对 `tests/fixtures/kpi_test_data.json` 中的 7 个场景运行 KPI 计算"
        "（`_compute_kpis` + `_compute_composite_score`），"
        "综合评分采用国标 4 分项加权：准确率(30) + 快速率(20) + 平稳率(30) + 有效自控率(20)。"
    )
    lines.append("")
    lines.append("| 场景名 | 描述 | 综合评分 | 旧定级（3级） | 新定级（5级） | 是否变化 |")
    lines.append("|--------|------|----------|---------------|---------------|----------|")
    for r in results:
        score_str = f"{float(r['score']):.2f}" if r["score"] is not None else "N/A"
        changed_str = "✅ 是" if r["changed"] else "—"
        lines.append(
            f"| {r['scenario']} | {r['description']} | {score_str} | "
            f"{r['old_grade']} | {r['new_grade']} | {changed_str} |"
        )
    lines.append("")
    lines.append(
        f"**统计：** 共 {len(results)} 个场景，定级发生变化 {changed_count} 个，"
        f"未变化 {unchanged_count} 个。"
    )
    lines.append("")

    # ── 3. 定级变化分析 ──
    lines.append("## 3. 定级变化分析")
    lines.append("")

    changed_results = [r for r in results if r["changed"]]
    if changed_results:
        lines.append("### 3.1 定级发生变化的场景")
        lines.append("")
        for r in changed_results:
            score_val = float(r["score"]) if r["score"] is not None else None
            lines.append(f"#### {r['scenario']}（{r['description']}）")
            lines.append("")
            lines.append(
                f"- 综合评分：**{score_val:.2f}**" if score_val is not None else "- 综合评分：N/A"
            )
            lines.append(f"- 旧定级：`{r['old_grade']}` → 新定级：`{r['new_grade']}`")
            reason = _explain_change(r["old_grade"], r["new_grade"], score_val)
            lines.append(f"- 变化原因：{reason}")
            lines.append("")
    else:
        lines.append("### 3.1 定级发生变化的场景")
        lines.append("")
        lines.append("所有场景定级均未发生变化。")
        lines.append("")

    unchanged_results = [r for r in results if not r["changed"]]
    if unchanged_results:
        lines.append("### 3.2 定级未发生变化的场景")
        lines.append("")
        lines.append("| 场景名 | 综合评分 | 定级（新旧一致） | 说明 |")
        lines.append("|--------|----------|------------------|------|")
        for r in unchanged_results:
            score_str = f"{float(r['score']):.2f}" if r["score"] is not None else "N/A"
            note = _explain_unchanged(
                r["old_grade"], float(r["score"]) if r["score"] is not None else None
            )
            lines.append(f"| {r['scenario']} | {score_str} | {r['old_grade']} | {note} |")
        lines.append("")

    # ── 4. 5 级定级标准说明 ──
    lines.append("## 4. 5 级定级标准说明")
    lines.append("")
    lines.append(
        "新 5 级定级标准对齐 **GB/T 44693.2-2024 §6.3 性能分级**，"
        "将控制回路性能划分为 5 个等级，覆盖从优秀到低劣的完整性能谱系："
    )
    lines.append("")
    lines.append("| 等级 | 英文标识 | 评分区间 | 含义 |")
    lines.append("|------|----------|----------|------|")
    lines.append("| 优 | EXCELLENT | score >= 90 | 性能优秀，控制回路各项指标卓越，无需干预 |")
    lines.append("| 良 | GOOD | 80 <= score < 90 | 性能良好，控制回路运行正常，建议持续监测 |")
    lines.append("| 中 | FAIR | 70 <= score < 80 | 性能一般，存在改进空间，建议关注关键指标 |")
    lines.append("| 差 | WARNING | 60 <= score < 70 | 性能较差，需排查问题并制定优化方案 |")
    lines.append("| 劣 | POOR | score < 60 | 性能低劣，控制回路失效风险高，需立即处理 |")
    lines.append("| 不确定 | INCONCLUSIVE | None | 数据不足，无法计算综合评分 |")
    lines.append("")
    lines.append("**与旧 3 级标准的对应关系：**")
    lines.append("")
    lines.append("- 旧 `GOOD`（>=80）拆分为 `EXCELLENT`（>=90）和 `GOOD`（80~89）")
    lines.append("- 旧 `WARNING`（60~79）拆分为 `FAIR`（70~79）和 `WARNING`（60~69）")
    lines.append("- 旧 `POOR`（<60）保持为 `POOR`（<60）")
    lines.append("- `INCONCLUSIVE` 含义不变")
    lines.append("")
    lines.append("**5 级定级的优势：**")
    lines.append("")
    lines.append(
        "1. **更精细的性能区分**：原 3 级将 60~79 分统归为 WARNING，"
        "无法区分「中等」与「较差」；5 级新增 FAIR 等级，准确反映 70~79 分的「一般」状态。"
    )
    lines.append(
        "2. **对齐国标要求**：GB/T 44693.2-2024 明确规定 5 级性能分级，"
        "本次修改确保系统定级标准与国标一致。"
    )
    lines.append(
        "3. **优化运维决策**：EXCELLENT 与 GOOD 的区分有助于识别标杆回路，"
        "FAIR 与 WARNING 的区分有助于差异化制定优化策略。"
    )
    lines.append("")

    # ── 附录：各场景 KPI 明细 ──
    lines.append("## 附录：各场景 KPI 计算明细")
    lines.append("")
    lines.append(
        "| 场景名 | 准确率 | 快速率 | 平稳率 | 有效自控率 | 振荡率 | 饱和率 | 自控率 | 好值率 |"
    )
    lines.append(
        "|--------|--------|--------|--------|------------|--------|--------|--------|--------|"
    )
    kpi_codes = [
        "accuracy_rate",
        "fast_rate",
        "steady_rate",
        "effective_auto_rate",
        "oscillation_rate",
        "saturation_rate",
        "auto_mode_rate",
        "good_value_rate",
    ]
    for r in results:
        kpis = r["kpis"]
        row_vals = []
        for code in kpi_codes:
            v = kpis.get(code)
            row_vals.append(f"{float(v):.2f}" if v is not None else "N/A")
        lines.append(f"| {r['scenario']} | " + " | ".join(row_vals) + " |")
    lines.append("")

    return "\n".join(lines)


def _explain_change(old: str, new: str, score: float | None) -> str:
    """解释定级变化原因。"""
    if score is None:
        return "综合评分为 None，新旧定级均为 INCONCLUSIVE（不应触发变化分支）。"
    reasons = {
        ("GOOD", "EXCELLENT"): "评分 >= 90，新标准新增 EXCELLENT 等级，原 GOOD 升级为 EXCELLENT。",
        ("GOOD", "FAIR"): "评分位于 70~79，新标准将原 WARNING 区间拆分，70~79 归入新增 FAIR 等级。",
        ("WARNING", "FAIR"): "评分位于 70~79，新标准将原 WARNING（60~79）拆分，70~79 升级为 FAIR。",
        (
            "WARNING",
            "GOOD",
        ): "评分位于 80~89，新旧标准均判定为 GOOD（此情况理论上不应出现，请核查）。",
        (
            "WARNING",
            "EXCELLENT",
        ): "评分 >= 90，新标准新增 EXCELLENT 等级（此情况理论上不应出现，请核查）。",
        (
            "POOR",
            "WARNING",
        ): "评分位于 60~69，新旧标准均判定为 WARNING（此情况理论上不应出现，请核查）。",
        ("POOR", "FAIR"): "评分位于 70~79，新标准拆分后归入 FAIR（此情况理论上不应出现，请核查）。",
    }
    key = (old, new)
    if key in reasons:
        return reasons[key]
    return f"评分 {score:.2f} 由旧标准 {old} 变更为新标准 {new}。"


def _explain_unchanged(grade: str, score: float | None) -> str:
    """解释定级未变化原因。"""
    if score is None:
        return "综合评分为 None，新旧定级均为 INCONCLUSIVE"
    if grade == "GOOD":
        return f"评分 {score:.2f} 位于 80~89，新旧标准均判定为 GOOD"
    if grade == "WARNING":
        return f"评分 {score:.2f} 位于 60~69，新旧标准均判定为 WARNING"
    if grade == "POOR":
        return f"评分 {score:.2f} < 60，新旧标准均判定为 POOR"
    if grade == "EXCELLENT":
        return f"评分 {score:.2f} >= 90，新旧标准均判定为 EXCELLENT/GOOD（新标准更细）"
    if grade == "FAIR":
        return f"评分 {score:.2f} 位于 70~79，新旧标准均判定为 FAIR/WARNING（新标准更细）"
    return f"评分 {score:.2f}，定级 {grade} 未变化"


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


def main() -> None:
    """主入口：计算所有场景并生成对比报告。"""
    if not FIXTURE_PATH.exists():
        print(f"错误: 测试数据文件不存在: {FIXTURE_PATH}", file=sys.stderr)
        print("请先运行: uv run python scripts/generate_kpi_test_data.py", file=sys.stderr)
        sys.exit(1)

    print(f"加载测试数据: {FIXTURE_PATH}", file=sys.stderr)
    print(f"场景数量: {len(SCENARIOS)}", file=sys.stderr)

    results: list[dict] = []
    for name in SCENARIOS:
        print(f"计算场景 [{name}]...", file=sys.stderr)
        try:
            result = compute_scenario(name)
            score_val = float(result["score"]) if result["score"] is not None else None
            print(
                f"  → 综合评分={score_val:.2f}" if score_val is not None else "  → 综合评分=N/A",
                f"旧定级={result['old_grade']}, 新定级={result['new_grade']}, "
                f"变化={'是' if result['changed'] else '否'}",
                file=sys.stderr,
            )
            results.append(result)
        except Exception as e:
            print(f"  ✗ 场景 {name} 计算失败: {e}", file=sys.stderr)
            import traceback

            traceback.print_exc()
            sys.exit(1)

    # 生成报告
    print("生成 Markdown 报告...", file=sys.stderr)
    report = generate_report(results)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    print(f"\n报告已生成: {REPORT_PATH}", file=sys.stderr)
    print(f"文件大小: {REPORT_PATH.stat().st_size / 1024:.1f} KB", file=sys.stderr)

    # 打印汇总
    changed_count = sum(1 for r in results if r["changed"])
    print(f"\n汇总: 共 {len(results)} 个场景, 定级变化 {changed_count} 个", file=sys.stderr)


if __name__ == "__main__":
    main()
