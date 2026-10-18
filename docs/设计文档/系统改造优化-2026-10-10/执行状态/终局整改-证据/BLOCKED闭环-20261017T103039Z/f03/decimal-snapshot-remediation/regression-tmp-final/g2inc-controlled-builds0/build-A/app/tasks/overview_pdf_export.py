"""管理总览 PDF 导出（IA 优化 P3，三阶段自适应）。

设计原则：
- 不做三套独立模板：单套模板按 stage 控制 section 显隐
- 黑白灰主色 + 深蓝（#1e3a8a）强调色；A4 纵向；表格紧凑
- 基于 reportlab Platypus（与 report_generator.py 现有方案一致，不引新依赖）
- 数据来源：复用 report_stats.get_overview() + get_diagnosis_statistics() + get_benefit()
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.report_stats import (
    default_report_window,
    get_benefit,
    get_diagnosis_statistics,
    get_overview,
)

logger = logging.getLogger(__name__)

# 色板（工业风：黑白灰 + 深蓝强调）
C_PRIMARY = "#1e3a8a"  # 深蓝：标题、强调
C_SECONDARY = "#475569"  # slate-600：副标题、说明
C_BORDER = "#cbd5e1"  # slate-300：表格边框
C_BG_HEAD = "#f1f5f9"  # slate-100：表头背景
C_NEUTRAL = "#0f172a"  # slate-900：正文
C_OK = "#047857"  # emerald-700
C_WARN = "#b45309"  # amber-700
C_ERR = "#b91c1c"  # red-700


async def run_overview_pdf_export(
    db: AsyncSession,
    requested_stage: str | None,
    start_date_iso: str | None,
    end_date_iso: str | None,
    plant_node_id: str | None,
    operator: str | None,
) -> tuple[bytes, str]:
    """生成管理总览 PDF（三阶段自适应）。

    Returns: (pdf_bytes, suggested_file_name)
    """
    # 1) 解析时间
    start = datetime.fromisoformat(start_date_iso) if start_date_iso else None
    end = datetime.fromisoformat(end_date_iso) if end_date_iso else None
    if not start or not end:
        start, end = default_report_window()

    req_stage = requested_stage or "S1"
    if req_stage not in ("S1", "S2", "S3"):
        req_stage = "S1"

    # 2) 拉聚合数据（overview 内部处理锁定/自动判定）
    overview = await get_overview(
        db,
        stage=req_stage,
        start_date=start,
        end_date=end,
        plant_node_id=plant_node_id,
    )
    effective_stage = overview["stage"]

    diagnosis_stats = None
    benefit_data = None
    # 阶段≥S2才拉诊断统计（节省DB）
    stage_order = {"S1": 1, "S2": 2, "S3": 3}
    if stage_order.get(effective_stage, 1) >= 2:
        try:
            diagnosis_stats = await get_diagnosis_statistics(
                db, start_date=start, end_date=end, plant_node_id=plant_node_id
            )
        except Exception as exc:  # pragma: no cover - 容错
            logger.warning("PDF 诊断统计拉取失败: %s", exc)
            diagnosis_stats = None
    if stage_order.get(effective_stage, 1) >= 3:
        try:
            benefit_data = await get_benefit(
                db, start_date=start, end_date=end, plant_node_id=plant_node_id
            )
        except Exception as exc:  # pragma: no cover - 容错
            logger.warning("PDF 收益数据拉取失败: %s", exc)
            benefit_data = None

    # 3) 组装模板上下文并渲染
    file_bytes = _render_pdf(
        overview=overview,
        diagnosis_stats=diagnosis_stats,
        benefit_data=benefit_data,
        effective_stage=effective_stage,
        time_range=(start, end),
        operator=operator,
    )
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    file_name = f"CLPM管理总览_{effective_stage}_{stamp}.pdf"
    return file_bytes, file_name


# ---------------------------------------------------------------------------
# reportlab Platypus 渲染（单一模板按阶段控制 section 显隐）
# ---------------------------------------------------------------------------


def _render_pdf(
    *,
    overview: dict[str, Any],
    diagnosis_stats: dict[str, Any] | None,
    benefit_data: dict[str, Any] | None,
    effective_stage: str,
    time_range: tuple[datetime, datetime],
    operator: str | None,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm, mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"CLPM 控制回路绩效治理平台 管理总览报告 ({effective_stage})",
    )
    styles = getSampleStyleSheet()

    # ---- 自定义样式（黑白灰 + 深蓝强调）----
    s_cover_title = ParagraphStyle(
        "cover_title",
        parent=styles["Title"],
        fontSize=26,
        leading=32,
        textColor=colors.HexColor(C_PRIMARY),
        alignment=TA_LEFT,
        spaceAfter=6 * mm,
    )
    s_cover_sub = ParagraphStyle(
        "cover_sub",
        parent=styles["Normal"],
        fontSize=12,
        leading=18,
        textColor=colors.HexColor(C_SECONDARY),
        spaceAfter=3 * mm,
    )
    s_h1 = ParagraphStyle(
        "h1",
        parent=styles["Heading1"],
        fontSize=16,
        leading=22,
        textColor=colors.HexColor(C_PRIMARY),
        spaceBefore=5 * mm,
        spaceAfter=3 * mm,
        borderWidth=0,
        borderPadding=0,
    )
    s_h2 = ParagraphStyle(
        "h2",
        parent=styles["Heading2"],
        fontSize=13,
        leading=18,
        textColor=colors.HexColor(C_NEUTRAL),
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    s_body = ParagraphStyle(
        "body",
        parent=styles["Normal"],
        fontSize=10,
        leading=15,
        textColor=colors.HexColor(C_NEUTRAL),
    )
    s_caption = ParagraphStyle(
        "caption",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor(C_SECONDARY),
    )
    s_center = ParagraphStyle(
        "center",
        parent=s_body,
        alignment=TA_CENTER,
    )

    # ---- 工具函数 ----
    def _p(text: str, style=s_body) -> Paragraph:
        return Paragraph(text or "", style)

    def _sp(h_mm: float = 2) -> Spacer:
        return Spacer(1, h_mm * mm)

    def _table_header_style() -> TableStyle:
        return TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(C_BG_HEAD)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(C_NEUTRAL)),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(C_BORDER)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )

    def _kpi_table(kpis: list[dict]) -> Table:
        """紧凑 KPI 4 列表格。"""
        rows: list[list] = [["指标", "数值", "单位", "上下文"]]
        for k in kpis:
            val = k.get("value")
            rows.append(
                [
                    k.get("label") or "",
                    "—" if val is None else str(val),
                    k.get("unit") or "",
                    k.get("context") or "",
                ]
            )
        col_widths = [3.5 * cm, 2.5 * cm, 1.8 * cm, 9.3 * cm]
        t = Table(rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(_table_header_style())
        return t

    def _section_visible(min_stage: str) -> bool:
        return stage_order.get(effective_stage, 1) >= stage_order.get(min_stage, 1)

    stage_order = {"S1": 1, "S2": 2, "S3": 3}
    start_dt, end_dt = time_range
    time_range_str = f"{start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')}"
    stage_label_map = {
        "S1": "S1 基础可视",
        "S2": "S2 闭环管理",
        "S3": "S3 持续优化",
    }
    is_locked = overview.get("isLocked", False)

    # ==================================================================
    # 组装 story（单一模板，按阶段控制 section 显隐）
    # ==================================================================
    story: list = []

    # ---- 封面（所有阶段）----
    story.append(_sp(25))
    story.append(_p("CLPM 控制回路绩效治理平台", s_cover_title))
    story.append(
        _p(
            f"管理总览报告 · {stage_label_map.get(effective_stage, effective_stage)}",
            ParagraphStyle(
                "st2",
                parent=s_cover_sub,
                fontSize=18,
                leading=24,
                textColor=colors.HexColor(C_SECONDARY),
            ),
        )
    )
    story.append(_sp(10))
    meta_lines = [
        f"<b>时间范围</b>：{time_range_str}",
        f"<b>阶段来源</b>：{'管理员锁定' if is_locked else '自动判定'}",
        f"<b>生成时间</b>：{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        f"<b>生成人</b>：{operator or 'system'}",
    ]
    for line in meta_lines:
        story.append(_p(line, s_body))
        story.append(_sp(1))
    story.append(_sp(20))
    story.append(
        _p(
            "本报告由 CLPM 系统基于管理成熟度自动生成，内容覆盖健康评估、"
            "闭环处置、持续优化等不同阶段维度。数据以系统实际记录为准。",
            s_caption,
        )
    )
    story.append(PageBreak())

    # ---- 1. 总览指标（S1~S3 共用，阶段越高越多）----
    story.append(_p("1. 总览指标", s_h1))
    kpis = overview.get("kpis") or []
    if kpis:
        story.append(_kpi_table(kpis))
    else:
        story.append(_p("该时段暂无总览指标数据。", s_caption))
    story.append(_sp(4))

    # ---- 2. 健康趋势（所有阶段）----
    story.append(_p("2. 健康趋势（按天）", s_h2))
    ht = overview.get("healthTrend") or []
    if ht:
        rows_ht = [["日期", "平均评分", "参评回路数"]]
        for p in ht:
            rows_ht.append(
                [
                    str(p.get("date", "")),
                    "—" if p.get("score") is None else f"{p['score']:.1f}",
                    str(p.get("loopCount") or 0),
                ]
            )
        tw = Table(rows_ht, colWidths=[4 * cm, 4 * cm, 4 * cm], repeatRows=1)
        tw.setStyle(_table_header_style())
        story.append(tw)
    else:
        story.append(_p("该时段暂无健康趋势数据。", s_caption))
    story.append(_sp(4))

    # ---- 3. TOP 问题回路（所有阶段，S2+处置状态列、S3+收益列）----
    story.append(_p("3. TOP 问题回路（评分最低前 10 条）", s_h2))
    tpl = overview.get("topProblemLoops") or []
    if tpl:
        rows_tpl = [["回路位号", "装置/单元", "最新评分", "诊断分类", "严重度"]]
        if _section_visible("S2"):
            rows_tpl[0].append("处置状态")
        if _section_visible("S3"):
            rows_tpl[0].append("评分改善")
        sev_cn = {"HIGH": "高", "MEDIUM": "中", "LOW": "低"}
        hs_cn = {
            "PENDING": "待执行",
            "EXECUTING": "执行中",
            "VERIFYING": "验证中",
            "CLOSED": "已闭环",
            "REOPENED": "已重开",
            "CANCELLED": "已作废",
        }
        for tl in tpl:
            row = [
                tl.get("loopTagName") or "",
                tl.get("unitPath") or "—",
                "—" if tl.get("latestScore") is None else f"{float(tl['latestScore']):.1f}",
                tl.get("primaryCategoryLabel") or "未诊断",
                sev_cn.get(tl.get("severity") or "", tl.get("severity") or "—"),
            ]
            if _section_visible("S2"):
                hs = tl.get("handlingStatus")
                row.append(hs_cn.get(hs, hs or "无工单"))
            if _section_visible("S3"):
                be = tl.get("benefitEstimate")
                row.append("—" if be is None else f"{float(be):+.1f}")
            rows_tpl.append(row)
        # 自适应列宽
        ncols = len(rows_tpl[0])
        width_map = {
            5: [3.5, 3, 2, 3.8, 1.7],
            6: [3, 2.5, 1.8, 3.2, 1.5, 2],
            7: [2.8, 2.2, 1.6, 2.8, 1.3, 1.8, 1.8],
        }
        cw = width_map.get(ncols, [17.0 / ncols] * ncols)
        t = Table(rows_tpl, colWidths=[w * cm for w in cw], repeatRows=1)
        t.setStyle(_table_header_style())
        story.append(t)
    else:
        story.append(_p("该时段暂无问题回路。", s_caption))

    # ==================================================================
    # S2+ 章节（显隐控制）
    # ==================================================================
    if _section_visible("S2"):
        story.append(PageBreak())
        story.append(_p("4. 闭环管理指标", s_h1))

        # 4.1 闭环统计摘要 KPI 4 行
        story.append(_p("4.1 处置关键指标", s_h2))
        # 从 kpis 中提取 S2 4 条单独展示（更清晰）
        s2_keys = ["closedLoopRate", "avgCycleHours", "closedThisMonth", "ineffectiveRate"]
        s2_kpis = [k for k in (overview.get("kpis") or []) if k.get("key") in s2_keys]
        if s2_kpis:
            story.append(_kpi_table(s2_kpis))
        story.append(_sp(3))

        # 4.2 处置闭环趋势
        story.append(_p("4.2 处置闭环趋势（近 6 个月）", s_h2))
        clt = overview.get("closedLoopTrend") or []
        if clt:
            rows_clt = [["月份", "新建工单", "闭环工单", "闭环率(%)"]]
            for m in clt:
                rows_clt.append(
                    [
                        m.get("month") or "",
                        str(m.get("total") or 0),
                        str(m.get("closed") or 0),
                        "—" if m.get("closedRate") is None else f"{float(m['closedRate']):.1f}",
                    ]
                )
            t = Table(rows_clt, colWidths=[3, 3, 3, 3], repeatRows=1)
            t.setStyle(_table_header_style())
            story.append(t)
        else:
            story.append(_p("暂无处置趋势数据。", s_caption))
        story.append(_sp(3))

        # 4.3 异常类型分布变化（诊断分类，近30天 vs 上一周期）
        story.append(_p("4.3 异常类型分布变化（近 30 天 vs 上一周期）", s_h2))
        adc = overview.get("anomalyDistributionChange") or []
        if adc:
            rows_adc = [["分类", "当前计数", "上周期", "占比变化(pp)"]]
            for c in adc:
                cur = c.get("currentRatio") or 0
                prev = c.get("previousRatio") or 0
                delta_pp = round((cur - prev) * 100, 1)
                rows_adc.append(
                    [
                        c.get("label") or c.get("category") or "",
                        str(c.get("currentCount") or 0),
                        str(c.get("previousCount") or 0),
                        f"{delta_pp:+.1f}",
                    ]
                )
            t = Table(rows_adc, colWidths=[5.5 * cm, 2.5 * cm, 2.5 * cm, 3 * cm], repeatRows=1)
            t.setStyle(_table_header_style())
            story.append(t)
        else:
            story.append(_p("暂无异常分布对比数据。", s_caption))
        story.append(_sp(3))

        # 4.4 诊断分类占比（P3 诊断报告同步补充口径）
        if diagnosis_stats:
            story.append(_p("4.4 诊断分类占比", s_h2))
            cd = diagnosis_stats.get("categoryDistribution") or []
            total_n = diagnosis_stats.get("total") or 0
            if cd:
                rows_cd = [["分类", "数量", "占比(%)"]]
                for c in cd:
                    pct = (c.get("ratio") or 0) * 100
                    rows_cd.append(
                        [
                            c.get("label") or c.get("category") or "",
                            str(c.get("count") or 0),
                            f"{pct:.1f}",
                        ]
                    )
                t = Table(rows_cd, colWidths=[6.5 * cm, 3 * cm, 3 * cm], repeatRows=1)
                t.setStyle(_table_header_style())
                story.append(t)
                story.append(_sp(1))
                story.append(_p(f"（诊断总数：{total_n}）", s_caption))
            else:
                story.append(_p("该时段暂无诊断分类数据。", s_caption))

            # 4.5 置信度分布
            story.append(_p("4.5 诊断置信度分布", s_h2))
            cfd = diagnosis_stats.get("confidenceDistribution") or []
            if cfd:
                rows_cfd = [["区间", "数量", "占比(%)"]]
                for c in cfd:
                    pct = (c.get("ratio") or 0) * 100
                    rows_cfd.append(
                        [
                            c.get("label") or c.get("range") or "",
                            str(c.get("count") or 0),
                            f"{pct:.1f}",
                        ]
                    )
                t = Table(rows_cfd, colWidths=[6.5 * cm, 3 * cm, 3 * cm], repeatRows=1)
                t.setStyle(_table_header_style())
                story.append(t)

    # ==================================================================
    # S3+ 章节（显隐控制）
    # ==================================================================
    if _section_visible("S3"):
        story.append(PageBreak())
        story.append(_p("5. 持续优化指标", s_h1))

        # 5.1 整定/处置前后 KPI 对比
        story.append(_p("5.1 整定/处置前后 KPI 对比", s_h2))
        cmp_data = []
        if benefit_data:
            cmp_data = benefit_data.get("kpiComparison") or []
        if cmp_data:
            rows_cmp = [["指标", "前", "后", "差值", "单位"]]
            for c in cmp_data:
                delta = c.get("delta")
                delta_str = "—" if delta is None else (f"{float(delta):+.1f}")
                rows_cmp.append(
                    [
                        c.get("label") or c.get("metric") or "",
                        "—" if c.get("before") is None else f"{float(c['before']):.1f}",
                        "—" if c.get("after") is None else f"{float(c['after']):.1f}",
                        delta_str,
                        c.get("unit") or "",
                    ]
                )
            t = Table(rows_cmp, colWidths=[3.5, 2.2, 2.2, 2.2, 1.8], repeatRows=1)
            t.setStyle(_table_header_style())
            story.append(t)
        else:
            story.append(_p("暂无前后 KPI 对比数据。", s_caption))
        story.append(_sp(3))

        # 5.2 自控率提升曲线（近 90 天 benefitTrend）
        story.append(_p("5.2 自控率与综合评分趋势（近 90 天）", s_h2))
        bt = overview.get("benefitTrend") or []
        if bt:
            # 按 10 条抽样，避免过宽
            sample = bt
            if len(bt) > 20:
                step = len(bt) // 15
                sample = bt[::step][:20]
            rows_bt = [["日期", "自控率(%)", "综合评分"]]
            for p in sample:
                rows_bt.append(
                    [
                        str(p.get("date") or ""),
                        "—" if p.get("autoRate") is None else f"{float(p['autoRate']):.1f}",
                        "—" if p.get("score") is None else f"{float(p['score']):.1f}",
                    ]
                )
            t = Table(rows_bt, colWidths=[4 * cm, 3.5 * cm, 3.5 * cm], repeatRows=1)
            t.setStyle(_table_header_style())
            story.append(t)
        else:
            story.append(_p("暂无自控率提升趋势数据。", s_caption))
        story.append(_sp(3))

        # 5.3 装置标杆对比
        story.append(_p("5.3 装置标杆对比", s_h2))
        bench = []
        if benefit_data:
            bench = benefit_data.get("benchmark") or []
        if bench:
            rows_bm = [["装置", "回路数", "均分", "自控率(%)", "改善均值(分)"]]
            for b in bench:
                rows_bm.append(
                    [
                        b.get("unitName") or "",
                        str(b.get("loopCount") or 0),
                        "—" if b.get("avgScore") is None else f"{float(b['avgScore']):.1f}",
                        "—" if b.get("avgAutoRate") is None else f"{float(b['avgAutoRate']):.1f}",
                        "—" if b.get("avgDelta") is None else f"{float(b['avgDelta']):+.1f}",
                    ]
                )
            t = Table(rows_bm, colWidths=[4.5, 1.8, 2, 2.5, 2.5], repeatRows=1)
            t.setStyle(_table_header_style())
            story.append(t)
        else:
            story.append(_p("暂无装置标杆对比数据。", s_caption))
        story.append(_sp(3))

        # 5.4 收益摘要（纯技术口径：闭环处置前后评分改善，不做经济换算）
        story.append(_p("5.4 收益摘要（技术口径）", s_h2))
        si = next(
            (k for k in (overview.get("kpis") or []) if k.get("key") == "scoreImprovement"),
            None,
        )
        if si and si.get("value") is not None:
            story.append(
                _p(
                    f"平均评分改善：<b>{float(si['value']):+.1f} 分</b>"
                    "（闭环回路处置前后评分差值均值，纯技术口径）。",
                    s_body,
                )
            )
        else:
            story.append(
                _p(
                    "平均评分改善：<b>—</b>（技术口径：闭环处置前后评分差值，"
                    "当前时段暂无闭环数据）。",
                    s_body,
                )
            )

    # ---- 页脚（所有阶段）----
    story.append(_sp(15))
    story.append(_p("— 报告结束 —", s_center))

    # ---- 构建 PDF ----
    def _on_page(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(C_SECONDARY))
        # 页眉细线
        canvas.setStrokeColor(colors.HexColor(C_BORDER))
        canvas.setLineWidth(0.4)
        canvas.line(18 * mm, 282 * mm, 192 * mm, 282 * mm)
        canvas.drawString(18 * mm, 284 * mm, "CLPM 控制回路绩效治理平台 · 管理总览报告")
        # 页脚
        canvas.line(18 * mm, 12 * mm, 192 * mm, 12 * mm)
        canvas.drawString(
            18 * mm,
            8 * mm,
            f"Stage: {stage_label_map.get(effective_stage, effective_stage)}"
            f"{' (锁定)' if is_locked else ''}",
        )
        canvas.drawRightString(192 * mm, 8 * mm, f"第 {doc_obj.page} 页")
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buffer.getvalue()
