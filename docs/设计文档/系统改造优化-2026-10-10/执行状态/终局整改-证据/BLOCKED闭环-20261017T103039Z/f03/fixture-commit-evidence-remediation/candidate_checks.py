from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import runpy

ROOT = Path(__file__).parent
helpers = runpy.run_path(str(ROOT / "capture.py"))
run, PY, RUFF, PROBE = [helpers[key] for key in ("run", "PY", "RUFF", "PROBE")]
CAND = Path("/Users/zhangping/DEV/clpm-remediation-20261017")
sources = [str(CAND / "e2e/scripts" / name) for name in ("prepare-critical-fixture.py", "test_critical_fixture.py")]
jobs = [
    ("07-candidate-seventeen", [PY, sources[1], "-v"], CAND),
    ("08-candidate-lint", [RUFF, "check", *sources], CAND),
    ("09-candidate-format", [RUFF, "format", "--check", "--line-length", "88", *sources], CAND),
    ("10-candidate-type", ["pnpm", "exec", "tsc", "--noEmit"], CAND / "e2e"),
    ("11-candidate-collect", ["pnpm", "exec", "playwright", "test", "--config", "playwright.critical.config.ts", "--list"], CAND / "e2e"),
    ("12-candidate-diffcheck", ["git", "diff", "--check"], CAND),
    ("13-candidate-independent-probe", [PY, PROBE, "--source", sources[0], "--out", str(ROOT / "candidate-probe")], CAND),
]
errors = []
with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
    futures = {pool.submit(run, label, argv, 0, cwd): label for label, argv, cwd in jobs}
    for future in as_completed(futures):
        try:
            future.result()
        except Exception as error:
            errors.append((futures[future], repr(error)))
assert not errors, errors
