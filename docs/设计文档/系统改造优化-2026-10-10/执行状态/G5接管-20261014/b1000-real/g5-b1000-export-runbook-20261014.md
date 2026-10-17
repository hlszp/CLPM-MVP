# G5 B1000 只读完整导出

脚本 `/private/tmp/g5_b1000_export_20261014.py` 已冻结，作者仅本地syntax/help；实际查询由root完成。以新abs输出保留全部1030 global LOOP（1000测试+30额外真实scope）、全部26测试NODE、实际source_task_id对应TaskRecord、父真实backend result/children和子真实backend result、15源before/after、已冻结配置/元数据逐section差异、精确7000点TD角色计数。数据库读取使用REPEATABLE READ+READ ONLY；无服务/任务投递、control.inspect消息、DB/Redis写入或数百万TD原始行读取。

root等待父与节点实际完成后执行，输出不得已存在：

```sh
cd /private/tmp/clpm-g5-followup-20261014/backend
PYTHONDONTWRITEBYTECODE=1 /Users/zhangping/DEV/CLPM-MVP-p1int/backend/.venv/bin/python /private/tmp/g5_b1000_export_20261014.py --source-root /private/tmp/clpm-g5-followup-20261014 --manifest /Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014/b1000-real/manifest_b_2026101405.json --parent-task-id d1c3809a-fae9-4ab4-a8cd-9b146bb22c1d --source-freeze /private/tmp/g5-final-candidate-freeze-v2-20261014.json --barrier-report /Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014/b1000-real/barrier-report.json --out /Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014/b1000-real/full-1030-loops-26nodes-export.json
```

有已知节点child UUID可追加`--node-task-id <actual UUID>`；有已知TaskRecord ID可追加`--task-record-id <actual UUID>`。两参数可重复。脚本自动读取parent backend trail、实际PG source_task_id和本地barrier报告的child观察ID，不向worker发送inspect RPC。未知/缺失/多行/PENDING原样导出，不补成SUCCESS，不伪造TaskRecord ID；只导出证据，不判900s PASS。

TD SQL严格`point_id IN (全部7000冻结UUID)`，分别`[START,END]`与`[START,END)` GROUP BY COUNT，实际角色/loop点身份分开保存。writer最大dense offset3599、gap899，理论END无行，两种计数都应6936900、per-role总数匹配冻结值；其它点行不参与。

权重补充：批前`smoke-real/freeze.json`的`metadata.node.weights`已冻结完整1000工厂成员有效重要度权重，preparedAt=2026-10-13T22:13:34.389141Z；B preparedAt=22:29:46.854668Z。两个artifact candidate6c92，1000 UUID集合一致。timing/root应逐1000用Decimal比较该pre-B map与导出`pg.loops[].effectiveImportanceWeight`，并用每节点实际member权重复算score/validWeight及manual0分计入N。不能说B manifest冻结了完整LoopLevelWeight表；30额外真实回路及等级表其它行的批前完整配置未由该pre-B map覆盖，导出保存实际值并保留此边界。

完整验收由非脚本作者承担：所有1030结果逐行；1000组成800normal+100bad+50manual+50gap、850SUCCESS/150INCONCLUSIVE，50manual仍N内0分；26全部节点精确成员/六状态账/各指标分子与权重和/record引用；实际子任务终态；source15稳定、配置差异；900s完整屏障以独立真实时间证据计算。原benchmark九条件20UNIT保留，不能只以父elapsed或20UNIT替代26全树。P6只交提示词，不实施代码。
