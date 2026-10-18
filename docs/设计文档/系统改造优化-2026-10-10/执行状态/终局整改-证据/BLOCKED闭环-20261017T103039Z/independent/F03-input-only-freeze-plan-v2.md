# F03 新 H 输入冻结接续 v2

本次对应候选 H `9f356782ef4076dabb91a86f56e054e514e341ea`、runId `20261017215608`，实际输出目录 `/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f03/local-business-20261017215608-9f356782ef40`。旧8a计划和旧失败运行原件保留，不改写、移贴或复用其运行标签。

完整数学输入要求、公式、支持范围、原始输入血缘、held快慢两键、fresh进程/配置稳定、先封expected及readSet再读actual、容差与闭端分母，继续逐字引用原 `F03-input-only-freeze-plan.md` SHA `914bc901ba0d872fae09b18966f84a62ce19ee2b2a88808de713cc7b1b5209a5`，没有新增或放宽条件。原两SQL不变：pre-gate `d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294`；dataset-row `9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff`。独立oracle仍为 `3e7f74a511db0abac0b1c49dc8e1f9c9ee7b04011774f6dfc283fcba4ff4b42a`。

Root唯一Keeper，在此H实际fresh seed/绑定提交与TD全量读回合格后、任何CALC发布前，使用本轮fixture的loop UUID执行原RR READ ONLY SQL并保存原输出/rc/来源；CALC后再重复核参数未变。实际git/runtime H必须观测，不以该计划自报身份替代。输入包NPZ/manifest出现前仍无数值expected；实际业务输出不提前提供。本接续冻结时复验人未读任何实际KPI结果。
