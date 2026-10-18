# F03 TD 读回 SQL 非作者增量复核

判定：**PASS（仅保留字段引用修复及实测原始响应）**。冻结源0e1fc40d…→431e8df1…完整原字节只给SELECT列value增加两个反引号，生成器、INSERT、样本值、QC、时窗、容差和业务四例无变化。

独立重算Keeper五个真实私库SELECT请求/HTTP200原body的字节/SHA，与原command/summary逐项相同：旧unquoted LIMIT0/1均TD code9728；新quoted LIMIT0/1为code0和0/1行；quoted完整窗口为code0/3600行。证据为同名*.raw.json、*.command.json与independent-wire-check.json。没有向服务发请求。

独立不调用产品validator，从原raw body逐行解析实际timestamp/point/value/quality，与原fixture INPUT的3600个PV样本逐一比较：UTC时间精确、浮点hex精确、两QC与点UUID全同，零容差。只证本次已写PV读回，不声称七角色完整seed或真实CALC/NPZ/四业务已通过。其它资格/两状态证据另见fixture局部回执。
