g3_out="$RUNNER_TEMP/g3-numerical-originals"
mkdir "$g3_out"
cp "$GITHUB_WORKSPACE/.github/workflows/ci.yml" "$g3_out/workflow.yml"
date -u +%Y-%m-%dT%H:%M:%SZ > "$g3_out/ci-started-at.txt"
g3_rc=0
timeout --signal=TERM 60s "$GITHUB_WORKSPACE/backend/.venv/bin/python" \
  "$GITHUB_WORKSPACE/backend/scripts/profile_identification_numerics.py" \
  "$g3_out/observation" "$GITHUB_WORKSPACE" \
  > "$g3_out/ci-stdout.log" 2> "$g3_out/ci-stderr.log" || g3_rc=$?
printf '%s\n' "$g3_rc" > "$g3_out/ci-exit-code.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$g3_out/ci-finished-at.txt"
find "$g3_out" -type f ! -name ci-sha256.txt -print0 | sort -z | \
  xargs -0 sha256sum > "$g3_out/ci-sha256.txt"
cat "$g3_out/ci-stdout.log"
cat "$g3_out/ci-stderr.log" >&2
exit "$g3_rc"
