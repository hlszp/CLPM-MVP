# Collector v4 非作者复验

结论：采证时点与无损保全的离线工具范围 PASS，不签真实九条件或600s。

冻结collector SHA d0b78c19a8c285e0bf34eb8cf22d140548f44e900e3e0cd592536393f7db031c。独立核8源manifest SHA一致并保全旧c8与新源，前后字节不变。

自编 `test_independent_first_full.py` 复用作者的无网络PG/clock数据夹具，但独立编写时点/事务/原始字节断言：完整首轮、partial999→下一轮完整两种真实main流程，旧c8两例失败rc1，v4两例通过rc0。核firstTerminal/firstComplete直接属于各自poll，同RR snapshot/PG起止完整一致；Redis taskObservedAt另列，不能伪称PG同事务。四数组gzip/raw SHA可恢复，partial999保留，NULL/0保留，完整1000/latest1000；post30 final属于更晚独立事务；同轮双触发只执行四次SELECT *。故障终态checkpoint照存，COLLECTED不冒称业务PASS。

原7 helper及完整资源分支、四个原full SQL谓词/排序由独立AST比较；作者27纯fixture由非作者另跑27 passed、rc0。见check、AST-check、命令/原始stdout/stderr。工具没有根据状态/版本替业务gate裁决，最终witness仍由非作者逐项核。

未接真实PG/Redis/TD、队列或产品进程。
