evidence_root="$RUNNER_TEMP/critical-e2e/input-evidence"
mkdir -p "$evidence_root"
unit_rc=0
"$GITHUB_WORKSPACE/backend/.venv/bin/python" e2e/scripts/test_critical_input_evidence.py \
  > "$evidence_root/before-tests.stdout.log" 2> "$evidence_root/before-tests.stderr.log" || unit_rc=$?
printf '%s\n' "$unit_rc" > "$evidence_root/before-tests.exit-code.txt"
cat "$evidence_root/before-tests.stdout.log"
cat "$evidence_root/before-tests.stderr.log" >&2
if [ "$unit_rc" -ne 0 ]; then exit "$unit_rc"; fi
capture_rc=0
"$GITHUB_WORKSPACE/backend/.venv/bin/python" e2e/scripts/capture-critical-input-evidence.py \
  --phase before --output "$evidence_root/before" \
  > "$evidence_root/before-launch.stdout.log" 2> "$evidence_root/before-launch.stderr.log" || capture_rc=$?
printf '%s\n' "$capture_rc" > "$evidence_root/before-launch.exit-code.txt"
cat "$evidence_root/before-launch.stdout.log"
cat "$evidence_root/before-launch.stderr.log" >&2
exit "$capture_rc"
