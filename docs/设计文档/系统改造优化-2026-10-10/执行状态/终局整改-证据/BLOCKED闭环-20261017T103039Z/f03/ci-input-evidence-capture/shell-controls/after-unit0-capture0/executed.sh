evidence_root="$RUNNER_TEMP/critical-e2e/input-evidence"
mkdir -p "$evidence_root"
capture_rc=0
"$GITHUB_WORKSPACE/backend/.venv/bin/python" e2e/scripts/capture-critical-input-evidence.py \
  --phase after --output "$evidence_root/after" \
  > "$evidence_root/after-launch.stdout.log" 2> "$evidence_root/after-launch.stderr.log" || capture_rc=$?
printf '%s\n' "$capture_rc" > "$evidence_root/after-launch.exit-code.txt"
cat "$evidence_root/after-launch.stdout.log"
cat "$evidence_root/after-launch.stderr.log" >&2
exit "$capture_rc"
