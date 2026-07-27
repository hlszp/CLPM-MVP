"""Diagnosis report generation service (SVC-12 + SVC-13).

SVC-12: 诊断建议书 PDF 生成
- generate_diagnosis_report(loop_id, snapshot_data, recommendations) → PDF bytes
- 使用 reportlab 库生成 PDF
- 内容：回路信息 + 诊断结果 + 性能指标 + 可能原因 + 解决方案推荐 + 生成时间

SVC-13: 诊断统计报表 CSV 导出
- export_diagnosis_statistics(start_date, end_date, plant_node_id) → CSV bytes
- 统计各标签数量、分布、趋势
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diagnosis import DiagnosisResult
from app.models.loop import LoopLedger
from app.models.plant_node import PlantNode
from app.services.diagnosis import DIAG_LABEL_NAMES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SVC-12: 诊断建议书 PDF 生成
# ---------------------------------------------------------------------------


def generate_diagnosis_report(
    loop_id: str,
    snapshot_data: dict[str, Any],
    recommendations: dict[str, Any],
) -> bytes:
    """生成诊断建议书 PDF。

    Args:
        loop_id: 回路 ID
        snapshot_data: 诊断快照数据，包含：
            - tagName: 回路位号
            - unitName: 装置名称
            - compositeScore: 综合评分
            - diagnosisLabels: [{label, labelName, confidence, evidence, algorithm}]
            - featureValues: dict
            - evidenceChain: {reasoning, ...}
            - diagnosedAt: 诊断时间
            - algorithmVersion: 算法版本
        recommendations: get_recommendations() 返回的推荐数据

    Returns:
        PDF 文件 bytes
    """
    # 延迟导入，避免在模块加载时引入 reportlab
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    # 注册中文 CID 字体：Helvetica 不含中文字符，直接使用会导致 PDF 中文乱码。
    # STSong-Light 是 reportlab 内置的 Adobe CID 字体，无需额外 TTF 文件，
    # 支持简体中文。CID 字体无独立 bold 变体，标题层级靠 fontSize + textColor 区分。
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="诊断建议书",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ChTitle",
        parent=styles["Title"],
        fontName="STSong-Light",
        fontSize=20,
        leading=26,
        alignment=1,  # center
        spaceAfter=10,
    )
    heading_style = ParagraphStyle(
        "ChHeading",
        parent=styles["Heading2"],
        fontName="STSong-Light",
        fontSize=14,
        leading=18,
        spaceBefore=12,
        spaceAfter=6,
        textColor=colors.HexColor("#1890ff"),
    )
    body_style = ParagraphStyle(
        "ChBody",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=10,
        leading=15,
        spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        "ChCell",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=9,
        leading=12,
    )
    cell_header_style = ParagraphStyle(
        "ChCellHeader",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=9,
        leading=12,
        textColor=colors.white,
    )

    elements: list[Any] = []

    # 标题
    elements.append(Paragraph("控制回路诊断建议书", title_style))
    elements.append(
        Paragraph(
            f"生成时间：{datetime.now(UTC).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')} UTC",
            body_style,
        )
    )
    elements.append(Spacer(1, 8 * mm))

    # 一、回路信息
    elements.append(Paragraph("一、回路信息", heading_style))
    tag_name = snapshot_data.get("tagName", "—")
    unit_name = snapshot_data.get("unitName", "—")
    composite_score = snapshot_data.get("compositeScore")
    score_str = f"{float(composite_score):.2f}" if composite_score is not None else "—"
    diagnosed_at = snapshot_data.get("diagnosedAt", "—")
    algorithm_version = snapshot_data.get("algorithmVersion", "—")

    loop_info_data = [
        [Paragraph("回路 ID", cell_header_style), Paragraph(str(loop_id), cell_style)],
        [Paragraph("回路位号", cell_header_style), Paragraph(str(tag_name), cell_style)],
        [Paragraph("所属装置", cell_header_style), Paragraph(str(unit_name), cell_style)],
        [Paragraph("综合评分", cell_header_style), Paragraph(score_str, cell_style)],
        [Paragraph("诊断时间", cell_header_style), Paragraph(str(diagnosed_at), cell_style)],
        [Paragraph("算法版本", cell_header_style), Paragraph(str(algorithm_version), cell_style)],
    ]
    loop_info_table = Table(loop_info_data, colWidths=[40 * mm, 120 * mm])
    loop_info_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1890ff")),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9d9d9")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    elements.append(loop_info_table)
    elements.append(Spacer(1, 4 * mm))

    # 二、诊断结果
    elements.append(Paragraph("二、诊断结果", heading_style))
    diag_labels = snapshot_data.get("diagnosisLabels", [])
    if diag_labels:
        diag_header = [
            Paragraph("标签", cell_header_style),
            Paragraph("中文名", cell_header_style),
            Paragraph("置信度", cell_header_style),
            Paragraph("算法", cell_header_style),
        ]
        diag_rows = [diag_header]
        for item in diag_labels:
            confidence = item.get("confidence")
            conf_str = f"{float(confidence):.2f}" if confidence is not None else "—"
            diag_rows.append(
                [
                    Paragraph(str(item.get("label", "—")), cell_style),
                    Paragraph(str(item.get("labelName", "—")), cell_style),
                    Paragraph(conf_str, cell_style),
                    Paragraph(str(item.get("algorithm", "—")), cell_style),
                ]
            )
        diag_table = Table(diag_rows, colWidths=[40 * mm, 40 * mm, 30 * mm, 50 * mm])
        diag_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1890ff")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9d9d9")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f5f5f5")],
                    ),
                ]
            )
        )
        elements.append(diag_table)
    else:
        elements.append(Paragraph("暂无诊断结果", body_style))
    elements.append(Spacer(1, 4 * mm))

    # 三、性能指标
    elements.append(Paragraph("三、性能指标", heading_style))
    feature_values = snapshot_data.get("featureValues", {})
    if feature_values:
        feat_header = [
            Paragraph("指标名", cell_header_style),
            Paragraph("值", cell_header_style),
        ]
        feat_rows = [feat_header]
        for k, v in feature_values.items():
            if isinstance(v, (list, tuple)):
                # 数组类指标（如 fft_frequencies/amplitudes）只显示长度，
                # 避免 str(list) 撑爆表格 cell 触发 reportlab LayoutError
                v_str = f"[数组，长度 {len(v)}]"
            else:
                try:
                    v_str = f"{float(v):.4f}"
                except (TypeError, ValueError):
                    v_str = str(v)
            # 截断超长字符串（如 evidence JSON），保护 PDF 布局
            if len(v_str) > 200:
                v_str = f"{v_str[:200]}..."
            feat_rows.append([Paragraph(str(k), cell_style), Paragraph(v_str, cell_style)])
        feat_table = Table(feat_rows, colWidths=[60 * mm, 100 * mm])
        feat_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1890ff")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9d9d9")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f5f5f5")],
                    ),
                ]
            )
        )
        elements.append(feat_table)
    else:
        elements.append(Paragraph("暂无性能指标", body_style))
    elements.append(Spacer(1, 4 * mm))

    # 四、可能原因
    elements.append(Paragraph("四、可能原因", heading_style))
    evidence_chain = snapshot_data.get("evidenceChain", {}) or {}
    reasoning = evidence_chain.get("reasoning") if isinstance(evidence_chain, dict) else None
    if reasoning:
        elements.append(Paragraph(str(reasoning), body_style))
    else:
        elements.append(Paragraph("暂无推理过程", body_style))
    elements.append(Spacer(1, 4 * mm))

    # 五、解决方案推荐
    elements.append(Paragraph("五、解决方案推荐", heading_style))
    rec_list = recommendations.get("recommendations", []) if recommendations else []
    if rec_list:
        rec_header = [
            Paragraph("优先级", cell_header_style),
            Paragraph("标签", cell_header_style),
            Paragraph("行动项", cell_header_style),
            Paragraph("详细描述", cell_header_style),
            Paragraph("目标模块", cell_header_style),
        ]
        rec_rows = [rec_header]
        priority_names = {1: "高", 2: "中", 3: "低"}
        for rec in rec_list:
            rec_rows.append(
                [
                    Paragraph(
                        priority_names.get(rec.get("priority"), str(rec.get("priority"))),
                        cell_style,
                    ),
                    Paragraph(str(rec.get("labelName", rec.get("label", "—"))), cell_style),
                    Paragraph(str(rec.get("action", "—")), cell_style),
                    Paragraph(str(rec.get("description", "—")), cell_style),
                    Paragraph(str(rec.get("targetModule", "—")), cell_style),
                ]
            )
        rec_table = Table(
            rec_rows,
            colWidths=[15 * mm, 25 * mm, 35 * mm, 65 * mm, 20 * mm],
        )
        rec_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1890ff")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9d9d9")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f5f5f5")],
                    ),
                ]
            )
        )
        elements.append(rec_table)
    else:
        elements.append(Paragraph("暂无解决方案推荐", body_style))
    elements.append(Spacer(1, 6 * mm))

    # 页脚
    elements.append(
        Paragraph(
            f"本建议书由 CLPM 系统自动生成 · 回路 ID: {loop_id}",
            ParagraphStyle(
                "Footer",
                parent=body_style,
                fontSize=8,
                textColor=colors.grey,
                alignment=1,
            ),
        )
    )

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes


# ---------------------------------------------------------------------------
# SVC-13: 诊断统计报表 CSV 导出
# ---------------------------------------------------------------------------


async def export_diagnosis_statistics(
    db: AsyncSession,
    start_date: str,
    end_date: str,
    plant_node_id: str | None = None,
) -> bytes:
    """导出诊断统计 CSV。

    统计各标签数量、分布、趋势。

    Args:
        db: 异步数据库会话
        start_date: 开始日期（ISO 8601）
        end_date: 结束日期（ISO 8601）
        plant_node_id: 可选装置节点 ID 筛选

    Returns:
        CSV 文件 bytes（UTF-8 with BOM，便于 Excel 直接打开）
    """
    # 解析时间
    start_dt = _parse_iso_datetime(start_date)
    end_dt = _parse_iso_datetime(end_date)

    # 查询时间窗内的诊断结果
    stmt = (
        select(
            DiagnosisResult.diag_label,
            func.count(DiagnosisResult.id).label("count"),
        )
        .where(DiagnosisResult.diagnosed_at >= start_dt)
        .where(DiagnosisResult.diagnosed_at <= end_dt)
        .where(DiagnosisResult.diag_label.is_not(None))
        .group_by(DiagnosisResult.diag_label)
        .order_by(func.count(DiagnosisResult.id).desc())
    )

    if plant_node_id:
        stmt = stmt.join(LoopLedger, DiagnosisResult.loop_id == LoopLedger.id).where(
            LoopLedger.unit_id == plant_node_id
        )

    result = await db.execute(stmt)
    label_counts = result.all()

    # 查询装置名（如果指定了 plant_node_id）
    plant_name = "全部装置"
    if plant_node_id:
        node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
        node = node_result.scalar_one_or_none()
        if node:
            plant_name = node.name

    # 按天聚合趋势
    trend_stmt = (
        select(
            func.date_trunc("day", DiagnosisResult.diagnosed_at).label("day"),
            DiagnosisResult.diag_label,
            func.count(DiagnosisResult.id).label("count"),
        )
        .where(DiagnosisResult.diagnosed_at >= start_dt)
        .where(DiagnosisResult.diagnosed_at <= end_dt)
        .where(DiagnosisResult.diag_label.is_not(None))
        .group_by("day", DiagnosisResult.diag_label)
        .order_by("day", DiagnosisResult.diag_label)
    )
    if plant_node_id:
        trend_stmt = trend_stmt.join(LoopLedger, DiagnosisResult.loop_id == LoopLedger.id).where(
            LoopLedger.unit_id == plant_node_id
        )

    trend_result = await db.execute(trend_stmt)
    trend_rows = trend_result.all()

    # 生成 CSV
    buffer = io.StringIO()
    # UTF-8 with BOM，便于 Excel 直接打开中文
    buffer.write("\ufeff")
    writer = csv.writer(buffer)

    # 标题区
    writer.writerow(["CLPM 诊断统计报表"])
    writer.writerow(["导出时间", datetime.now(UTC).replace(tzinfo=None).isoformat()])
    writer.writerow(["时间范围", f"{start_date} ~ {end_date}"])
    writer.writerow(["装置范围", plant_name])
    writer.writerow([])

    # 一、标签分布汇总
    writer.writerow(["一、标签分布汇总"])
    writer.writerow(["标签代码", "标签名称", "数量", "占比(%)"])
    total = sum(row[1] for row in label_counts) or 1
    for label, count in label_counts:
        label_name = DIAG_LABEL_NAMES.get(label, label)
        ratio = (count / total) * 100
        writer.writerow([label, label_name, count, f"{ratio:.2f}"])
    writer.writerow(["合计", "", total, "100.00"])
    writer.writerow([])

    # 二、按天趋势
    writer.writerow(["二、按天趋势"])
    writer.writerow(["日期", "标签代码", "标签名称", "数量"])
    for day, label, count in trend_rows:
        day_str = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
        label_name = DIAG_LABEL_NAMES.get(label, label)
        writer.writerow([day_str, label, label_name, count])
    writer.writerow([])

    # 三、分布统计
    writer.writerow(["三、分布统计"])
    writer.writerow(["指标", "值"])
    writer.writerow(["标签种类数", len(label_counts)])
    writer.writerow(["诊断结果总数", total])
    if label_counts:
        top_label, top_count = label_counts[0]
        top_label_name = DIAG_LABEL_NAMES.get(top_label, top_label)
        writer.writerow(["最多标签", f"{top_label_name} ({top_label})"])
        writer.writerow(["最多标签数量", top_count])
        writer.writerow(["最多标签占比(%)", f"{(top_count / total) * 100:.2f}"])
    writer.writerow([])

    csv_str = buffer.getvalue()
    buffer.close()
    return csv_str.encode("utf-8")


def _parse_iso_datetime(s: str) -> datetime:
    """解析 ISO 8601 时间字符串为 naive datetime。

    前端 dayjs().toISOString() 产出带 ``Z`` 的 UTC 字符串，解析后为 offset-aware datetime；
    但 DiagnosisResult.diagnosed_at 列为 ``TIMESTAMP WITHOUT TIME ZONE``（naive），
    asyncpg 绑定 aware datetime 到 naive 列会抛
    ``can't subtract offset-naive and offset-aware datetimes``。
    故在此统一去 tzinfo，与 DB 列口径及 tracker.py 的 _parse_iso_dt 保持一致。
    """
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


__all__ = [
    "export_diagnosis_statistics",
    "generate_diagnosis_report",
]
