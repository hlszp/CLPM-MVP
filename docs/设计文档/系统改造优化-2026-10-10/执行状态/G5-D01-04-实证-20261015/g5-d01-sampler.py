#!/usr/bin/env python3
"""D01-A1 资源采样器：进程 CPU/RSS + 容器 + PG 连接 + Redis 队列，每 5s 一行 JSONL。

用法: python3 g5-d01-sampler.py <out.jsonl> [duration_s]
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

OUT = sys.argv[1]
DUR = int(sys.argv[2]) if len(sys.argv) > 2 else 1800


def sh(cmd: str) -> str:
    try:
        return subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def worker_stats() -> dict:
    out = sh(
        "ps -o pid,pcpu,rss,command -ax | grep 'clpm-g5-runtime.*celery.*worker' "
        "| grep -v grep | head -6"
    )
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            rows.append({"pid": parts[0], "pcpu": float(parts[1]), "rss_mb": round(int(parts[2]) / 1024, 1)})
    return {"workers": rows}


def container_stats() -> dict:
    out = sh(
        "docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' "
        "clpm-mvp-postgres clpm-mvp-tdengine clpm-mvp-redis 2>/dev/null"
    )
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            rows.append({"name": parts[0], "cpu": parts[1], "mem": parts[2]})
    return {"containers": rows}


def pg_connections() -> int | None:
    out = sh(
        "docker exec clpm-mvp-postgres psql -U clpm -d clpm -t -c "
        '"SELECT count(*) FROM pg_stat_activity" 2>/dev/null'
    )
    return int(out) if out.isdigit() else None


def queue_depth() -> dict:
    out = sh(
        "cd /Users/zhangping/DEV/clpm-g5-runtime-20261015/backend && .venv/bin/python -c \""
        "from app.tasks.celery_app import celery_app\n"
        "import redis\n"
        "r = redis.from_url(celery_app.conf.broker_url)\n"
        "print(r.llen('default'), r.hlen('unacked'))\" 2>/dev/null"
    )
    parts = out.split()
    if len(parts) == 2:
        return {"default": int(parts[0]), "unacked": int(parts[1])}
    return {}


start = time.time()
with open(OUT, "a") as f:
    while time.time() - start < DUR:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            **worker_stats(),
            **container_stats(),
            "pgConns": pg_connections(),
            **queue_depth(),
        }
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        time.sleep(5)
print("sampler done")
