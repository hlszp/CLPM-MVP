# G5 smoke 方案文字勘误

原冻结`/private/tmp/g5-real-smoke-plan-20261014.md`表格manual行的“MODE=3”错误，正确为**MODE=0**（固定候选generate_capacity_dataset.synth_series manual分支）。原文件及hash保留，不覆盖历史证据。

冻结smoke脚本未写死MODE=3，其raw hash直接取真实generator输出、预期manual auto_mode_rate=0/score=0均正确；该文字勘误不改变输入、case身份、预先冻结业务status、score类别或生产门禁。新B1000 manifest亦按实际MODE=0记录。
