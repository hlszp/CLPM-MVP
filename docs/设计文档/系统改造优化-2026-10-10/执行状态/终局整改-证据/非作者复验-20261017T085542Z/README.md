# 本次非作者复验证据说明

本目录是 2026-10-17 独立复验生成的新证据，原作者的 evidence-manifest.json 及原审计未改写。最终判定以同级执行状态目录的《终局整改-非作者复验回执.md》为准。

- setup.json：原审计基线、候选/文档 SHA、临时 archive 身份、执行前工作树和原审计哈希。
- source-review.json：F01 提交祖先关系和 7 个文件内容对比，F03 脚本 SHA-256。
- f01-*.json/stdout.log/stderr.log/junit.xml：独立测试的命令、cwd、环境边界、时间、真实退出码、原始输出。两份 patch 记录仅在临时副本实施的反向变异；f01-mutation-restoration.json 记录恢复哈希。
- f02-independent-matrix.json：从 13 份原始 report 重新提取的九批矩阵和作废尝试；保留原始绝对路径、taskId、分母、终点、违约、哈希。
- f03-*.json/stdout.log/stderr.log：候选真实收集、干净目录依赖检查、受控正常/不可达/失败/全跳过的结果；合成用例只验证门禁，不代表产品业务链通过。
- f05-f06-document-snapshot.json：现行文档的行号/内容和哈希。
- f06-live-runtime.json：观测时进程父子关系、cwd、Git、health、只读 DB 查询。
- f07-frontend-tree-equivalence.json：三候选 frontend 内容身份一致；f07-matching-tree-* 是后续有效复跑。f07-targeted-follow.* 与 f07-full-vitest.* 是首次 archive/workspace 依赖隔离夹具失败的完整日志，不作为产品失败。
- f08-manifest-independent-check.json：作者 manifest 全部 24 项重算；f08-restore-sample.json：5 项从当前本地源恢复及重算哈希，明确不代表持久备份。
- final-hygiene.json：跟踪工作树、原审计及原 manifest 未变化，临时变异恢复、临时目录清理。
- independent-evidence-manifest.json：本目录全部证据和回执的 SHA-256/大小索引；manifest 本身不纳入自己的文件列表。

复现时应使用新证据目录和新临时目录，避免重写本轮原始输出。run_f01_independent.py、run_f03_independent.py、run_f07_matching_tree.py 保留了本次实际运行构造。其绝对目录指向本次已清理的临时目录，复现者须复制脚本并仅调整新输出/临时路径；先用 git archive 从 dbb1de314ae50e7373c6559ab746b690909bf1b0 重新提取独立副本，保持候选工作树不变。F03 需使用同一候选原样脚本和已安装的 Playwright 依赖，保留 fixture-only 标记。不得把这些脚本误当运行环境部署步骤。

本目录原始输出未提交/推送。后续保全由证据保管负责人按项目授权接续；本次临时复制不解决 F08 的三份大文件持久备份缺口。

