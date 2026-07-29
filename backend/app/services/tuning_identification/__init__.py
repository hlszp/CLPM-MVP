"""过程对象辨识算法栈（Phase 2）.

基于历史运行数据辨识过程对象 G_plant(s) = PV(s)/OP(s)，
输入 OP 时序，输出 PV 时序。

模块结构：
- ``types``: 共享数据结构（辨识结果、模型参数）
- ``excitation``: 激励检测与片段筛选
- ``nonparametric``: 非参数粗估（相关分析、Welch 谱）
- ``arx``: ARX 线性最小二乘辨识
- ``armax``: ARMAX 预测误差法辨识
- ``iv``: 早期实验性工具变量原型（不进入生产选模）
- ``order_selection``: 阶次选择（AIC/BIC/Ljung-Box）
- ``discrete_to_continuous``: 离散→连续参数转换
- ``pipeline``: 算法栈编排

与 ``tuning_algorithms.py`` 的关系：
- 本模块承载"历史数据自动辨识"路径（路径 A）
- ``tuning_algorithms.py`` 的 identify_fopdt/sopdt 保留为"阶跃实验"兜底路径（路径 B）
- 两者输出对齐：K/tau/theta 字段名一致，可直接喂给 tune_imc/lambda/zn/cohen_coon/simc
"""

from app.services.tuning_identification.pipeline import identify_from_history
from app.services.tuning_identification.types import (
    CandidateModel,
    ExcitationCheckResult,
    IdentificationResult,
    ModelParams,
    ModelType,
    SegmentInfo,
)

__all__ = [
    "CandidateModel",
    "ExcitationCheckResult",
    "IdentificationResult",
    "ModelParams",
    "ModelType",
    "SegmentInfo",
    "identify_from_history",
]
