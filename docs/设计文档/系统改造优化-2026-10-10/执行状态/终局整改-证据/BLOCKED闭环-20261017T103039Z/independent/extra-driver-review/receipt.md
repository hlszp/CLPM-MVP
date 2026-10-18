# Extra driver v2 非作者静态与错误路径复验

结论：局部工具范围 PASS，尚未运行driver或任何真实extra任务。

冻结源 SHA c34175b0ac0534dd688b5530a4ade54d2d3cd3269e70acb5600d473b0ea9bfb2。独立读全源，实际M/configDigest/env SHA、normal20complete、20/4分母、600s、persistent父FAILURE/TaskFAILED负例、原validator、65/30采样与PENDING不冒PASS等11项静态核成立；前后字节不变。

自编测试从旧/新实际源码提取未改的arm/except/finally AST片段，在纯fake policy、observer和handle上执行；没有运行driver其余操作或任何网络。旧eb37在arm观察失败、DISARM观察失败两种情形失败（2failed+1passed、rc1）；v2三个场景全部passed、rc0。arm失败仍尝试DISARM；DISARM失败仍关闭自有observer/handle；原异常仍抛；成功控制也收尾。见old/new-finally原始日志与源副本。format/lint rc0。

这不是完整运行流程的动态验收。最终非作者请求必须逐件固定M/config/env/24input/协议/全部实际工具引用，B11热请求必须显式recoverySeconds=300（该源码对字段为条件执行，不能遗漏）；root只执行该冻结请求。原20失败保留且不能择优拼绿，额外四批不计normal20。
