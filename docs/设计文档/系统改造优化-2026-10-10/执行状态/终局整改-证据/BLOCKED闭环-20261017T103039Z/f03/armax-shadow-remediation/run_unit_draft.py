"""Run the proposed pure regression file against one immutable shadow module."""

import argparse
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent
CANDIDATE = Path("/Users/zhangping/DEV/clpm-remediation-20261017")
sys.path.insert(0, str(CANDIDATE / "backend"))
parser = argparse.ArgumentParser()
parser.add_argument("variant", choices=["original", "prototype"])
args = parser.parse_args()
name = "armax_unit_draft_" + args.variant
spec = importlib.util.spec_from_file_location(name, ROOT / f"armax.{args.variant}.py")
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
from app.services.tuning_identification import armax as product_module
product_module.identify_armax = module.identify_armax
raise SystemExit(
    pytest.main(
        [
            "-q",
            "--noconftest",
            "-c",
            str(CANDIDATE / "backend/pyproject.toml"),
            str(ROOT / "test_armax_vectorized_equivalence.py"),
        ]
    )
)
