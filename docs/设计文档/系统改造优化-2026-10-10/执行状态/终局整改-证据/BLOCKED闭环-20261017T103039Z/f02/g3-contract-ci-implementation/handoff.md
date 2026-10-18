# G3 同 Job 诊断工具移交

已完成诊断脚本、CI 接入和网禁反例返修，并按 root 明确授权只落相同字节到候选。独立复验范围是工具源码/网禁/序列化/CI 结构；G3 数值问题的根因仍待真实 Linux 原件，不作通过签字。

- 最终脚本：`/Users/zhangping/DEV/clpm-remediation-20261017/backend/scripts/profile_identification_numerics.py`，437 行 / 17277 B，SHA-256 **c2eba163d4af6881cf83726311015064ec017c931528ae804a72a29d29c3b858**。
- workflow：`/Users/zhangping/DEV/clpm-remediation-20261017/.github/workflows/ci.yml`，17027 B，SHA-256 **575d2d19d0a9ac59d987e0df6cbb1659d6a106b91c9f1edfa3a900570d9d6622**；网禁返修不触此文件。
- 两文件相对 `9f356782ef4076dabb91a86f56e054e514e341ea` 共 478 行变更（脚本 437 加，workflow 40 加/1 删）；精确 `review-v2.patch` SHA-256 **18c2db89c19eb634368f467e5f08de43fd2b372e102d8a3de9871b08810a674b**。新增 guard 本身仅 9 加/1 删。
- 初版 `e2711c…` 和原 6d9ffd 源、独立旧红反例没有覆盖；root 后续提交在实际源采样时已到 `c6fa2b489af66da392ea72fa387629f1607ba613`，见 `source-identity-v2-final.json`。本代理没有 commit/push 或操作运行环境。

验证：本地 14 项序列化/AST/YAML/shell 控制真实 rc0；ruff/真实 backend 100 列 format/check rc0。F04 原固定反例 probe SHA `6216987…` 不变，原6d9ffd真实 rc1、最终c2eba真实 rc0；额外 reverse DNS `getnameinfo` 同样拒绝。F04 另核完整 1200 数值/类型序列化、替回 guard 后全部 AST 相同、原 pytest/env/其他 jobs 不变、真实 rc 无吞掉及 always artifact，局部工具 PASS。独立原始记录路径及 SHA 见 `source-identity-v2-final.json`。

初次在 E 目录误用默认 88 列 formatter 的 491 行中间字节保存为 `network-guard-v2/format-default88-intermediate.py`（42438afd…），初始 command/identity 保留；它从未落候选。最终源码使用实际 backend pyproject 100 列配置，恢复为仅 guard 的文本差异。

`算法契约与Linux同Job诊断移交.md` 给原契约八份全文/行号、`converged` 语义边界、可证伪的修复选择与取证步骤。重点：ARMAX 原 API 不含 converged、到预算仍返回；`iterations==200` 不替代实际 A/B 更新停止量；全局“到预算即 raise”会破坏 `max_iter=1` 原返回等价测试。不能改 200/1e-6、R9/Occam、原三条 joint/SNR/roundtrip 断言、保护 hash 或通过 blanket 拒绝取绿。

后续仅 root：正常完整 hooks/push 后读取真实 Linux 原 pytest 及同 Job 新进程诊断，封存 `backend-g3-numerics-<run_id>-<attempt>` 整个 artifact。先核 source/input/u-y 原件，再定位候选/迭代第一分歧；诊断不含每次 LS 全矩阵轨迹，缺项不得写成已证因果。SIMD 仅为已观察关联，不称根因；诊断 rc0 只表示采集完成，不修改测试结果或 G3/F03/F02 结论。

本任务未再运行本机辨识算法，未查写共享 DB/TD/Redis、未启停服务/投递任务；四项用户豁免保持。目录 manifest 固定全部本任务原件，旧历史/冻结协议/正式六工具未改。
