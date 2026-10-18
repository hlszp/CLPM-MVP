"""Offline Redis URL DB-precedence counterexample; never opens a connection."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlsplit, urlunsplit


def denied(*args, **kwargs):
    raise AssertionError("network/process operations are forbidden")


def run(source, output):
    socket.socket.connect = denied
    socket.create_connection = denied
    subprocess.run = denied
    from redis.asyncio import Redis

    spec = importlib.util.spec_from_file_location("reviewed_binding_tool", source)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    cfg = {
        "POSTGRES_DB": tool.DATABASE,
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "17102",
        "POSTGRES_USER": "clpm",
        "REDIS_URL": "redis://127.0.0.1:17123/0?db=1",
    }
    refused = False
    try:
        tool.private_config(cfg)
    except ValueError:
        refused = True
    urls = urlsplit(cfg["REDIS_URL"])
    actual = []
    for db in range(3):
        client = Redis.from_url(urlunsplit(urls._replace(path=f"/{db}")), decode_responses=True)
        actual.append({"requestedDb": db, "actualConnectionDb": client.connection_pool.connection_kwargs["db"]})
    result = {
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "requiredToRejectAmbiguousDbQuery": True,
        "refused": refused,
        "realRedisClientConfiguration": actual,
        "networkOrProcessOperations": 0,
        "actualNativeEnvUsesSeparateFieldsAndDoesNotTriggerThisBranch": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))
    assert refused, "db query overrides all 0/1/2 URL paths; private_config must refuse"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.out)
