"""Local pytest guard: reject DNS and all Internet socket operations before collection."""
import json
import os
import socket
from pathlib import Path

os.environ.setdefault("CLPM_TEST_MODE", "1")
os.environ.setdefault("CLPM_SKIP_EXIT_HOOKS", "1")
os.environ.setdefault("JWT_SECRET_KEY", "ci-test-secret-key-at-least-32-characters-long!!!")
os.environ.setdefault("DEBUG", "true")
attempts = []


def denied(operation):
    attempts.append(operation)
    raise AssertionError("non-author probe forbids actual network: " + operation)


for name in ("connect", "connect_ex", "sendto", "sendmsg"):
    if hasattr(socket.socket, name):
        original = getattr(socket.socket, name)

        def guarded(sock, *args, _name=name, _original=original, **kwargs):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                return denied(_name)
            return _original(sock, *args, **kwargs)

        setattr(socket.socket, name, guarded)
socket.create_connection = lambda *args, **kwargs: denied("create_connection")
socket.getaddrinfo = lambda *args, **kwargs: denied("getaddrinfo")


def pytest_configure(config):
    import logging
    from app.core.logging import _DebugFormatter

    handler = logging.FileHandler("/private/tmp/g5-trace-nonauthor-events-20261014.log", mode="w", encoding="utf-8")
    handler.setFormatter(_DebugFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logging.getLogger("app.core.g5_trace").addHandler(handler)
    config._g5_nonauthor_handler = handler


def pytest_sessionfinish(session, exitstatus):
    import logging
    handler = session.config._g5_nonauthor_handler
    logging.getLogger("app.core.g5_trace").removeHandler(handler)
    handler.close()
    Path("/private/tmp/g5-trace-nonauthor-network-20261014.json").write_text(json.dumps({
        "exitStatus": int(exitstatus), "blockedInternetOrDnsAttempts": attempts,
        "networkGuard": "AF_INET/AF_INET6 connect/connect_ex/sendto/sendmsg, create_connection, getaddrinfo denied; AF_UNIX loop sockets permitted"
    }, indent=2) + "\n")
