# 本轮直接整改与复验证据

实际代码为 fix/final-audit-remediation@dbb1de31 加候选补丁；未提交/合入/推送/部署。候选内容见 candidate/source-manifest.json 与 candidate.patch，36/36 patch恢复验证在 patch-restore-check.json。

最终结论见上两层《终局整改-整改后复验报告.md》；F02真实容量和F03真实业务/远端CI仍BLOCKED。新四项用户豁免保留，未把它们转成技术已验。

- verification/：最终后端6909/0、7431收集与前端1021/0、lint/type/build/schema的命令/rc/原始输出；初次失败也保留。
- f01/：19例、指定撤销/恢复和旧取消行为反向；变异仅临时副本。
- f02/：历史根因/原日志/失败资源处理；non-author和non-author-v3是非实现作者交叉复验。
- f03/：阻塞执行器与CI契约；non-author含真实不可达/失败/跳过等反例，不当成业务四例通过。
- f04/：作者实现、独立1000回路真实PG/API、主协调实际UI251及换绑/恢复、最后稳定行键；ui-receipt解释机械错误及最终证据。
- f07/：原真实换号与409反例、作者修复、独立真实UI/API与安全边界；phase1/phase2资源已清理。
- f05/与f06/与f08/：旧件、运行只读来源、持久大文件保全；docs-non-author及docs-final-review为非作者核验。
- final-hygiene.json：原审计/旧回执未改、三HEAD、候选36文件哈希、共享父子进程、私有资源清理复核。
- evidence-manifest.json：本轮全部证据和终态文档SHA；排除自身/校验文件以避免循环。evidence-manifest.sha256可用shasum -a 256 -c验证，manifest-verification.json为整清单重算结果。

项目证据目录和单独申明的本机大原件归档为本轮保全位置；不宣称异机或云备份。原始失败与旧版本清单是各自时点证据，不能混同当前源码。
