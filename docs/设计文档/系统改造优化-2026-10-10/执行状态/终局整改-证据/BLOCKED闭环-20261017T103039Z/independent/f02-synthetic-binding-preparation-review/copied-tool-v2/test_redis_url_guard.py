"""No network: URL options cannot override the reviewed private Redis identity."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "binding_url_guard_v2", Path(__file__).with_name("prepare_bindings.py")
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.mark.parametrize(
    "suffix", ["?db=1", "?db=0", "?host=foreign", "?port=6379", "#db=1", "?db=1#ignored"]
)
def test_ambiguous_url_options_rejected(suffix):
    cfg = {
        "POSTGRES_DB": tool.DATABASE,
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "17102",
        "POSTGRES_USER": "clpm",
        "REDIS_URL": "redis://127.0.0.1:17123/0",
    }
    tool.private_config(cfg)
    cfg["REDIS_URL"] += suffix
    with pytest.raises(ValueError):
        tool.private_config(cfg)
