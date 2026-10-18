"""回路处置建议标准模板（诊断 8 类 → 标准处置建议）。

供诊断详情"处置建议" Tab 自动带出（§9.4 处置闭环）：
- 分类来源优先级：人工复核结论（已复核时）> 诊断结论（主分类+并存分类）
- 每类 1-3 条，priority 1=最高
- 与旧版 diagnosis_recommendation.RECOMMENDATION_TEMPLATES（按症状标签）
  分域不同：本表按 v2 原因分类（8 类）组织，面向"处置措施"而非"诊断建议"。
后续一级模块"处置"可将其演进为可配置知识库。
"""

from __future__ import annotations

from typing import Any

STANDARD_ACTION_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "TUNING": [
        {
            "priority": 1,
            "action": "重新整定 PID 参数",
            "description": (
                "按当前工况重新计算 P/I/D 参数（建议采用 Lambda 整定法），"
                "抑制振荡或加快响应；可通过回路整定模块完成辨识与参数对比。"
            ),
        },
        {
            "priority": 2,
            "action": "核查参数与工况匹配性",
            "description": (
                "确认参数是否为负荷/物料变化前的旧值；工况切换后需同步更新整定参数组。"
            ),
        },
    ],
    "VALVE": [
        {
            "priority": 1,
            "action": "检查阀门粘滞与非线性",
            "description": (
                "PV-OP 散点呈椭圆回环时优先安排阀门检修：清洁/更换填料函，必要时加装智能定位器。"
            ),
        },
        {
            "priority": 2,
            "action": "核查阀门行程与气源",
            "description": ("检查气源压力、气动管路密封及阀门行程是否达标，排除执行机构响应迟缓。"),
        },
    ],
    "INSTRUMENT": [
        {
            "priority": 1,
            "action": "校验测量仪表",
            "description": "对变送器进行零点/量程校验，检查漂移与噪声；必要时安排检修或更换。",
        },
        {
            "priority": 2,
            "action": "检查信号接线与屏蔽",
            "description": "排查信号电缆接地、屏蔽与端子松动，消除引入测量噪声的干扰源。",
        },
    ],
    "COMMUNICATION": [
        {
            "priority": 1,
            "action": "排查通信链路质量",
            "description": (
                "检查 OPC/数采链路丢包与断流时段（结合证据链断流定位），"
                "重启采集网关或切换冗余链路。"
            ),
        },
        {
            "priority": 2,
            "action": "核查数据源位号配置",
            "description": (
                "确认位号映射、扫描周期与质量码配置正确，避免 Bad/Uncertain 数据混入计算。"
            ),
        },
    ],
    "PROCESS": [
        {
            "priority": 1,
            "action": "排查上游扰动源",
            "description": (
                "检查上游工艺参数变化、负荷波动与环境干扰；"
                "必要时增加前馈控制或平滑扰动的工艺操作规范。"
            ),
        },
        {
            "priority": 2,
            "action": "评估耦合回路解耦",
            "description": "确认是否存在回路间耦合/串级干扰，必要时调整回路关联或解耦参数。",
        },
    ],
    "UTILIZATION": [
        {
            "priority": 1,
            "action": "规范手动/自动切换操作",
            "description": (
                "核查频繁手动操作原因，规范投自动操作规程；"
                "长期手动运行的回路评估是否具备投自动条件。"
            ),
        },
        {
            "priority": 2,
            "action": "复核设定值管理",
            "description": "检查 SP 频繁调整/阶跃幅度过大的操作习惯，必要时引入设定值速率限制。",
        },
    ],
    "DESIGN": [
        {
            "priority": 1,
            "action": "评估控制方案与组态",
            "description": (
                "复核控制策略、量程与回路结构设计是否匹配工艺需求；"
                "必要时提改造申请（如更换控制结构、调整分程/串级组态）。"
            ),
        },
        {
            "priority": 2,
            "action": "核查 DCS 组态参数",
            "description": (
                "检查组态中滤波、死区、限幅等环节设置是否合理，消除组态引入的控制性能劣化。"
            ),
        },
    ],
    "DATA_INSUFFICIENT": [
        {
            "priority": 1,
            "action": "补齐诊断数据后复诊",
            "description": (
                "按断流/缺口时段补采历史数据（历史数据导入），确认数据完整后重新发起诊断。"
            ),
        },
    ],
}
