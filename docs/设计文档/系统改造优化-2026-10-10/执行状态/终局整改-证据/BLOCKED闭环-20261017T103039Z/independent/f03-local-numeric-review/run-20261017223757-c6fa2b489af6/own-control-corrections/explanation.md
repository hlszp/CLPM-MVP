第一次独立归档检查 rc1 来自复验脚本把 Uvicorn 原 Finished 行误写为带句点；原始字节与冻结 driver 均无句点。另在原始查询中 canonical 是 PostgreSQL to_jsonb(t)::text，故按其原字节/SHA 与行值对应验证，不能用 Python 字典排序编码替代。此两处仅修复验脚本格式假设；原产品/工具/日志/期待值均不改，第一次失败原件保留。
