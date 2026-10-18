"""Typed PG/SQLAlchemy error objects only; no connections or DB operations."""
import json
from pathlib import Path

import asyncpg
from sqlalchemy.exc import OperationalError

from app.tasks.kpi_calc import _is_resource_exhausted

pg = asyncpg.TooManyConnectionsError("sorry, too many clients already")
cases = [
    ("actual_asyncpg_too_many_connections", pg, True),
    ("actual_sqlalchemy_wrapped_pg", OperationalError("SELECT 1", {}, pg), True),
    ("actual_asyncpg_uppercase_message", asyncpg.TooManyConnectionsError("TOO MANY CLIENTS ALREADY"), True),
    ("unknown_network_timeout", TimeoutError("connection timeout"), False),
    ("unknown_cannot_connect_now", asyncpg.CannotConnectNowError("the database system is starting up"), False),
    ("pg_syntax_error", asyncpg.PostgresSyntaxError("syntax error at or near SELECT"), False),
    ("invalid_business_configuration", ValueError("invalid calculation config"), False),
]
results=[]
for name,error,expected in cases:
    observed=_is_resource_exhausted(error)
    results.append(dict(name=name,type=type(error).__name__,message=str(error),expected=expected,actual=observed))
    assert observed is expected,name
path=Path(__file__).with_name('pg-signature-results.json')
path.write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(dict(cases=len(cases),passed=len(cases),failed=0)))
