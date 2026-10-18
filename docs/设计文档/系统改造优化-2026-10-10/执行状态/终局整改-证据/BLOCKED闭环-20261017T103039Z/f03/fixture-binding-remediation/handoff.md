# F03 合成 INPUT 绑定历史整改：作者移交

源身份见source-manifest.json。只改e2e/scripts/prepare-critical-fixture.py及其纯保护测试，不改DataPlanner的“禁止当前绑定冒充未知过去”契约，不改七角色数值/quality、原始synth_series/_td_rows/TD INSERT调用AST、四业务spec/IDs/retries。原spec SHA d8aa48ef...完全不变。

真实前反例在Root/F02原件：当次新建7绑定valid_from13:06:59、声明评价窗11–12不相交，builder正确返回全NaN；不能靠调算法或fallback放行。现在仅受guard的全新synthetic prefix（API import必须inserted7/1、updated0）、实际专库身份、精确新loop描述/创建时间、7角色point UUID/名称/描述、无跨loop当前/历史冲突才允许显式声明自己的合成历史。现有唯一fresh MVP_INIT 7行按ID更新valid_from至声明窗起点、basis=SYNTHETIC_FIXTURE；若导入模式未生成历史则只为这7新points插入7行。旧/重绑/闭合/部分/重复历史一律拒绝。仅INPUT元数据，不构造任何KPI结果。

fixture.bindings.json保留实际PG identity、全部before原行/查询、DML SQL+全部参数+真实UPDATE7或7条INSERT01、事务内after和提交后独立只读readback、显式synthetic有效期/basis/SHA。串行化事务/精确rowcount错误回滚，关闭连接；失败原件先落committed=false。主fixture与inputs声明均引用其文件/SHA。原有业务历史helper不适合将已有当前行追溯，直接受控SQL严格限该新fixture；没有改产品helper。

七个真实TD输入INSERT后，以精确epoch毫秒窗口SELECT全部ts/value/quality/point，保存完整SQL/rawRows/count。对生成器全部样本按顺序/point/值/quality/时间精确比较，输出td-readback文件/SHA。只在fixture资格核验层支持实际TD wire的datetime/ISO/ms表示；没有改变产品时间解析或质量，不把读回成功当成计算通过。

真实纯保护17测试（原12＋5新，含4种fakePG事务模式：7行更新/7插入/错rowcount回滚）、Ruff、E2E typecheck与collect4均rc0；命令stdout/stderr/rc/SHA齐。没有连接实际服务或写共享/专属DB，也没有执行实际4业务。接下来由F04独立静态/纯控制核，Root在正常commit全同H后重新部署专属fresh资源、真实seed/4例/数值复验。实际PG/TD资格/新NPZ/任务闭环未验，不签PASS。Root继续独占提交/推送/部署；F02负责snapshot字符串与CUSTOM进度的独立业务修复。
