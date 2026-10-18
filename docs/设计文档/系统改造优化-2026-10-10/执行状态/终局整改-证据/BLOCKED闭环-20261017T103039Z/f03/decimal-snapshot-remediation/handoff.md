# F03 Decimal 快照修复移交（作者自验，待非作者）

结论：局部缺陷已修复并冻结，真实四项业务/数值独立验收仍待新版本重新执行。未提交、推送、部署或启动任何服务。四项用户豁免未改。

## 根因与范围

真实失败来自 `local-business-20261017215608-9f356782ef40/.../celery-worker.log` 第137物理行（本目录 `baseline/actual-worker-line-137.json`）。`LoopTypeWeight.weight_a/f/s` 为 `Numeric(3,2)`，真实 `get_loop_type_weights_map` 返回 Decimal，`kpi_calc` 将完整四类型配置纳入 `source.effectiveParams.typeWeights`。旧 canonical 把 Decimal 变字符串，而写入 JSON 未转换，因此 NPZ 已写而 manifest 抛 TypeError；原真实孤立文件未触碰、未补造 manifest。

仅修改 `backend/app/services/calc_snapshot.py` 与新增 `backend/tests/test_calc_snapshot_decimal.py`，精确身份见 `source-manifest.json`，完整原件见 `baseline/`、`final-source/`、`change.patch`。业务源为32新增/6删除，新测试194行，未改算法/数据/门限/原快照测试，其他模块AST相同。共享树中 workflow/新 profile 脏项属于 F02，未触碰。

## 规范与兼容

显式递归将有限 Decimal 转为 float，与既有 result_ledger.canonical_value 和 `_build_weights_map` 的计算语义一致；Decimal NaN/sNaN/正负无穷和浮点溢出拒绝，不使用 default=str。完整规范化 manifest 在任何 DB/NPZ IO 前以 allow_nan=False 预序列化；同一结构参与 hash、写入、读回及重放。原生浮点 NaN/Infinity 的新写入也在前置验证被拒绝；数组中的合法 NaN/mask 不变。旧 canonical 对非 Decimal 的既有规则保留，因此旧包读取和 hash 仍兼容，合法 JSON-native 内容的 canonical 字节/hash 不变。

历史无法成功落盘的 Decimal 类输入，其 hash 从旧字符串规范改为数值规范，是本缺陷的明确修正，不能声称该类 bitwise 等价；有限 Decimal 的任意精度按已有业务 float 语义转换，未宣称无限精度十进制无损。实际 Numeric(3,2) 四类型完整字典已由原始输入核对。

## 原始证据与退出码

- `producer-input-check/{command.json,stdout.log}` rc0：读取本轮原始 input-policy 配置，四类型/十二 Decimal 值与测试真实 ORM+生产函数一致；原始输入 SHA 与查询列类型/精度在案。没有读 KPI 输出制造期待。
- `red-original-v2/` rc1：在候选原实现直接运行真实 producer 反例，1failed，Decimal TypeError。
- `red-original-fixed-test/` rc1：固定当前测试、单独从 baseline 加载精确旧源，仍1failed，同一 TypeError；不回滚候选源码。
- `green-frozen/` rc0：12passed；完整 producer 字典保存→加载→hash→KPI 重放、数值身份、5种非法 Decimal、3种非有限 float/1种不支持对象的写前拒绝、旧 builtin 固定哈希。
- `regression-final/` rc0：六个相关模块159passed/0failed，23.14s。该轮后仅新测试 import 排序，最终12例再跑为 green-frozen。
- `legacy-create-original/` rc0：修前真实旧实现、自己的临时文件和假 DB 会话写出合法 JSON-native 包；`legacy-builtins/` 原件与完整元数据保存于本目录。
- `legacy-load-after/` rc0：修后加载同旧文件，原文件大小/SHA不变，inputHash 144024c8585c54b57ea7f8055ac419be9af563e6cf0e4d1e2da60c13b2e5cc7f 不变；完整 replayContext/effectiveParams 和既有离线 KPI 重放一致。不冒充真实服务计算。
- `ruff-frozen/`、`format-frozen/` rc0；原首次 import/长行问题与两次工具 namespace/系统 Python 不兼容失败保留，非业务通过证据。

每次执行目录均有真实 argv/cwd/环境覆写/起时/耗时/rc 与 stdout/stderr 哈希。所有文件仅为本轮自己的控制测试输出，不执行真实 PG/TD/Redis IO，不启动 Worker/Beat。

## 非作者复验标准

请 F04 独立验证 SHA，使用固定测试对封存旧源重现红、对冻结新源跑绿；另独立构造 Decimal/非有限/overflow/嵌套及 builtin 边界，确认写前拒绝不新增文件或 DB 调用、完整原始配置保真及旧包原 hash/读取/重放。局部通过不代替新 H 下真实四业务用例、可恢复输入和独立数值复算；这些仍需 Keeper 受控执行。
