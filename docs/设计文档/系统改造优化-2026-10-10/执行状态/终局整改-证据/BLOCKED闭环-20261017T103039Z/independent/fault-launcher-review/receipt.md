# 受控故障启动器非作者静态复核

结论：静态范围 PASS，未运行启动器、未接实际端口或启停产品进程。

`start_capacity_api_fault.py` SHA-256 `cf49f4a7cb0a708dc7702453bdc56ab16e4593baadb9cc93c48287e655463f5d` 与已独立冻结 formal 启动器 `50662e8a…` 的差异，仅 TDENGINE_PORT 从 17104 变为 17114（实际 REST 对应 17125）及私有 proxy 的投前 DISARMED / 同运行 SHA / 同专库 / 无 inFlight / 原 policy 字节 SHA 绑定。删去此明确增量并恢复端口后与原 formal 源逐字节相等。

原专库PG/Redis/TD地址、broker静止门、实际代码SHA/树、TRACE0/五线程1、唯一API/子Beat处理均原样保留；proxy健康门在任何Popen前。AST语法仅编译未执行。完整源码、精确diff、11项检查与身份在本目录。实际 proxy 作用域/故障恢复/持续失败/父子持久化与600s容量属于另行真实验收，不能由此静态PASS替代。
