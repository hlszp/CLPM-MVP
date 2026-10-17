# G5 B1000 新窗输入制备

脚本 `/private/tmp/g5_b1000_prepare_20261014.py` 固定候选`6c92cf6944d40bc2c92c9296110711933be28ec7`、仅新UTC窗`2026-10-14T05:00:00`–`06:00:00`、复用既有1000 PERF-T/7000点UUID。脚本作者只做本地语法/帮助，不运行PG/TD/Redis/队列；实际prepare/write仅Keeper root执行。

prepare先批量PG核完整三条件/类型250各类/26节点20单元/7000历史绑定全窗覆盖/全窗PG结果空/latest业务终点<=新窗开始/无登记gap/缺省配置，再用**一条TD super-table窗口COUNT GROUP BY point_id、无LIMIT**核全部7000点零行。之后复用真实load_role_points/_load_loop_meta/build_manifest_rows/synth_series，按seed1..1000冻结原始序列/角色hash、QC、类型step（TC5/PC2/LC5/FC1）、held60/7200及minObservedRatio=None、配置/身份/旧声明hash、latestBefore、全局参评与额外真实回路，保存新durable manifest。

write才复用真实write_timeseries，写新窗口；前置再次核身份/config/history/latest/environment hash和全7000点TD零行，并绑定prepare产生的manifest SHA256。输出必须新abs文件，拒绝覆盖；失败receipt标partialTdPossible，不自动重跑。无API导入、元数据/历史绑定/声明/旧manifest修改、旧TD删除、任务投递或服务启动。

独立预期：B=800 normal+100 insufficient+50 manual+50 gap，eligible1000、SUCCESS850（包括50个manual真实0分）、INCONCLUSIVE150、score=NULL150；source-known/QC是预处理有效率的上界，不把raw QC当成动态mask或完整P资格。manual实际MODE=0。新窗future属性在prepare按实时时钟冻结；本次允许future synthetic，并不认证当前真实日期窗口。

原始点数：PV/OP各3465000，SP2900，MODE/PID_P/PID_I/PID_D各1000，共**6936900**。写入时间不属于后续计算屏障计时；脚本本身不认证任何吞吐。用户新晋级要求为四类+949小样及一批完整九条件≤900s；旧600s/十批/连续两真实日期窗及完整性能挂P6，不能被本脚本宣称通过。

Keeper操作示例（目录必须已经存在，文件必须尚不存在）：

```sh
cd /private/tmp/clpm-g5-followup-20261014/backend
G5_B_PY=/Users/zhangping/DEV/CLPM-MVP-p1int/backend/.venv/bin/python
G5_B_ROOT=/private/tmp/clpm-g5-followup-20261014
G5_B_SCRIPT=/private/tmp/g5_b1000_prepare_20261014.py
G5_B_MANIFEST=/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014/g5-b1000-input-manifest-2026101405.json
PYTHONDONTWRITEBYTECODE=1 "$G5_B_PY" "$G5_B_SCRIPT" --source-root "$G5_B_ROOT" --manifest "$G5_B_MANIFEST" --action prepare
```

核prepare完整输出/新manifest与SHA后，使用输出的exact64位hash，不修改manifest：

```sh
G5_B_MANIFEST_SHA=<prepare输出的64位SHA256>
PYTHONDONTWRITEBYTECODE=1 "$G5_B_PY" "$G5_B_SCRIPT" --source-root "$G5_B_ROOT" --manifest "$G5_B_MANIFEST" --action write --expected-manifest-sha256 "$G5_B_MANIFEST_SHA" --receipt /Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014/g5-b1000-write-receipt-2026101405.json
```

`G5_B_MANIFEST_SHA=<...>`是待填示意，执行时复制prepare真实hash。写入失败保留已写数据和receipt，勿对同窗再生成“干净成功”。后续投递由root现有任务/benchmark helper承担，保留trace=1；真实标准小时会扫描全局全部参评，manifest记录extraEligibleLoopIds，不能以1000 prefix屏障隐藏额外真实回路。root另核实际节点子任务SUCCESS和26全树名册/score分母，工具默认20 UNIT与父任务elapsed不能替代完整九条件屏障。

脚本作者自身验收边界：syntax/help only；shared IO NOT_RUN。真实小样/后续batch的正式非作者结果验收由root或另一独立核查者承担，避免将脚本作者自验升格。
