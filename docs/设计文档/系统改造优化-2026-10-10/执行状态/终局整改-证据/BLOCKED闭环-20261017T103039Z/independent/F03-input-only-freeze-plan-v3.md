# F03 新 H 输入盲验接续 v3

本次对应候选 H `c6fa2b489af66da392ea72fa387629f1607ba613`、runId `20261017223757`，输出目录 `/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z/f03/local-business-20261017223757-c6fa2b489af6`。本计划冻结时只读实测候选 HEAD 与该 H 相同，Git tracked 文件数为 4046；实际运行仍须另以原进程、源码 catalog、构建环境及完整输入证明，不将计划标签当运行事实。旧 8a、9f 计划与各次失败原件保持不变。

数学输入、公式、支持范围、原始数据血缘、held 两键、fresh 进程与参数稳定要求、先封 expected/readSet 后读 actual 的顺序、容差及闭端分母，继续完整引用原 `F03-input-only-freeze-plan.md`（SHA `914bc901ba0d872fae09b18966f84a62ce19ee2b2a88808de713cc7b1b5209a5`）。两 SQL 原字节不变：pre-gate `d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294`；dataset-row `9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff`。独立 oracle 保持 `3e7f74a511db0abac0b1c49dc8e1f9c9ee7b04011774f6dfc283fcba4ff4b42a`。不调用产品计算器，不预填 GOOD/AUTO 或 3600 分母。

本轮接受已独立审核的不可变输入清单工具 `freeze_local_f03_input_manifest_root.py`（SHA `0de792ae43818fb041915c5fc3a5cd8aa181230de230b1a71165460d085ff985`）。在任何 gate/CALC 发布前，将环境与启动状态分别复制为 `input-only-before-gate/environment-before-gate.json`、`input-only-before-gate/started-state.json` 后纳入 21 项 SHA 清单；必须声明 `mutablePhaseFilesCopiedBeforeHashing=true`。之后 phase 文件变化不能覆盖这两份副本。原 9f 清单缺失旧环境字节的事实不因本轮改进而改判。

Root 是唯一 Keeper：仅在本轮 fresh seed、七角色绑定提交和全量 TD 严格读回合格后，使用本轮实际 loop UUID 执行原 RR/READ ONLY/UTC pre-gate SQL；保存完整 SQL、起止、rc、原 stdout/stderr 与 SHA，并在 CALC 后同 SQL 重核配置稳定。新 run-config、input-policy helper、driver、wrapper、实际 H 与各 SHA 在配套 JSON 中冻结。此冻结不授权复验人操作服务、数据库、队列或任务。

真实输入包产生后，仅先提供本轮原 NPZ、manifest、实际 dataset 原行及输入来源。完整输入与 effectiveParams/held/window 确定后，复验人先计算并封存 expected 与 readSet SHA，再接收实际 KPI/结果附件。没有原 manifest 或 dataset 行仍如实记 BLOCKED，不用孤立 NPZ 补造。冻结时复验人未读本轮或此前真实 KPI 输出；业务四例、CI、0 skip/retry/flaky、20×1000 容量及版本部署另验，不由此接续扩签。
