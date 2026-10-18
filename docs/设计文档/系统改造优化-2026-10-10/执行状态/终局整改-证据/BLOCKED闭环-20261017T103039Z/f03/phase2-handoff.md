# F03 第二批冻结与数值复验原件

仅修改`.github/workflows/ci.yml`两行job env：

- `CLPM_CALC_SNAPSHOT_DIR=${{ runner.temp }}/critical-e2e/calc-snapshots`，由已有always artifact整目录上传；
- `CLPM_BUILD_CODE_SHA=${{ github.sha }}`，实际执行PR merge SHA进入不可变包构建身份，不把PR head和merge SHA混同。

冻结：2026-10-17T10:41:18.302185Z。当前workflow SHA=`c8b8158d7124fe8d1b2b89fa3eb86bb21264a3396db2df2f361920af0948c109`；完整13文件身份在`phase2-source-manifest.json`，其他12文件与第一批不变。未新增产品API/业务写脚本。

包结构按实际源码核对：`calc-snapshots/YYYYMM/{datasetSnapshotId}.npz`与`.manifest.json`。manifest含实际window、source的assessmentProfile/releaseGeneration/configRevision/effectiveParams（展开值与来源）、replayContext的controlType/idealSettlingTime/weights/ranges、blocks的qualitySummary/seriesContext/grid/mask；NPZ含绝对epoch-ns时间轴、signals/nullmask/validity。已有result-record附件的datasetSnapshotId直接关联包文件。第一批fixture.inputs.json另存七角色输入offset/value/qualityClass/qualityRaw与SHA。

无写入验证：`phase2-workflow-preflight.*` rc0，静态核该目录落在已上传artifact根、实际源码SHA注入、生命周期前配置、三服务、无生产部署/continue-on-error；`immutable-package-probe.*` rc0，既有不可变包真实临时写入/读回hash与质量mask身份测试2/2（仅fake DB+独占temp，不启动业务）。第一批严格success反向1→正向0、5保护例、8门禁契约和typecheck通过继续有效；本批无TS或门禁逻辑变化，不重复冒充新增业务通过。

F04非作者已在看结果前冻结独立方法。AUTO模式集合包含1/2/3/4；实际custom窗口有end+1s语义，1小时输入3600点不保证评价域也是3600s。原始请求/fixture.windowStart/End、任务附件、manifest.window与seriesContext/NPZ必须保留；不能先写GOOD/AUTO=100，也不能修改输入/分母迎合结果。STEADY按固定profile/config/grid/masks独立算，两位数容差±0.005百分点。只有取得真实产物后非作者签数值结论。

作者此后不改冻结13文件，等待主协调PR实跑和原始run/job/step/artifact；远端失败由作者据可复现反证最小修复，F04独立复验。没有启停共享服务/容器、写共享DB/投递任务、提交推送或触发GitHub运行。
