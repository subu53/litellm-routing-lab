#!/usr/bin/env bash
# Test eval/archive_results.sh without a real proxy or a Gemini key.
#
# Starts tools/mock_proxy_for_tests.py, which answers POST /auto_router/test_routing
# the way LiteLLM does, then checks that the archive script:
#   1. refuses to run with no LITELLM_BASE_URL / LITELLM_API_KEY, and creates nothing
#   2. refuses to run when the proxy is dead, and still creates nothing
#   3. succeeds against a live endpoint and writes every expected file non-empty
#   4. cleans up its output directory when a step fails partway
#
# The archive it produces is MOCK DATA and is deleted at the end. It must never be
# committed, which is exactly why this test exists: a real run and a mock run are
# otherwise indistinguishable in the output directory.
#
# Usage, from the repository root:  bash eval/test_archive_script.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || { echo "FAIL: no usable python"; exit 1; }

PORT=4789
PASS=0
FAIL=0

check() {                       # check <label> <condition-exit-code>
  if [ "$2" -eq 0 ]; then
    echo "PASS  $1"; PASS=$((PASS + 1))
  else
    echo "FAIL  $1"; FAIL=$((FAIL + 1))
  fi
}

cleanup() {
  [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null || true
  rm -rf eval/results scores.csv
}
trap cleanup EXIT

# --- 1. no credentials ------------------------------------------------------------
rm -rf eval/results scores.csv
set +e
env -u LITELLM_BASE_URL -u LITELLM_API_KEY bash eval/archive_results.sh >/dev/null 2>&1
code=$?
set -e
check "refuses to run without credentials" "$([ "$code" -ne 0 ] && echo 0 || echo 1)"
check "creates no directory without credentials" "$([ ! -d eval/results ] && echo 0 || echo 1)"

# --- 2. dead proxy ----------------------------------------------------------------
rm -rf eval/results scores.csv
set +e
LITELLM_BASE_URL="http://127.0.0.1:9" LITELLM_API_KEY="x" \
  bash eval/archive_results.sh >/dev/null 2>&1
code=$?
set -e
check "refuses to run against a dead proxy" "$([ "$code" -ne 0 ] && echo 0 || echo 1)"
check "creates no directory against a dead proxy" "$([ ! -d eval/results ] && echo 0 || echo 1)"

# --- 3. happy path against the mock ----------------------------------------------
"$PY" tools/mock_proxy_for_tests.py "$PORT" >/dev/null 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 30); do
  if "$PY" -c "
import socket,sys
s=socket.socket(); s.settimeout(0.2)
sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" 2>/dev/null; then break; fi
  sleep 0.2
done

rm -rf eval/results scores.csv
set +e
LITELLM_BASE_URL="http://127.0.0.1:$PORT" LITELLM_API_KEY="mock" \
  bash eval/archive_results.sh >/dev/null 2>&1
code=$?
set -e
check "succeeds against a live endpoint" "$([ "$code" -eq 0 ] && echo 0 || echo 1)"

OUT="$(ls -d eval/results/*/ 2>/dev/null | head -1)"
check "wrote an archive directory" "$([ -n "$OUT" ] && echo 0 || echo 1)"

if [ -n "$OUT" ]; then
  for f in environment.txt config.gemini.yaml scores.csv dump-scores.txt \
           sweep-thresholds.txt compare-length.txt 2x2-confounds.txt README.md; do
    check "  $f is present and non-empty" \
      "$([ -s "${OUT}${f}" ] && echo 0 || echo 1)"
  done
  rows=$(($(wc -l < "${OUT}scores.csv") - 1))
  check "  scores.csv has 60 data rows (got $rows)" "$([ "$rows" -eq 60 ] && echo 0 || echo 1)"
  check "  environment.txt records the config hash" \
    "$(grep -q '^config_sha256: [0-9a-f]\{64\}$' "${OUT}environment.txt" && echo 0 || echo 1)"
  check "  dump-scores.txt reports prompts sent to Flash" \
    "$(grep -qi 'to Flash' "${OUT}dump-scores.txt" && echo 0 || echo 1)"
  check "  2x2-confounds.txt ends in a verdict or a refusal" \
    "$(grep -qE 'VERDICT|REFUSING' "${OUT}2x2-confounds.txt" && echo 0 || echo 1)"
fi

# --- 4. partial failure leaves no directory --------------------------------------
kill "$MOCK_PID" 2>/dev/null || true
wait "$MOCK_PID" 2>/dev/null || true
MOCK_PID=""
rm -rf eval/results scores.csv
set +e
LITELLM_BASE_URL="http://127.0.0.1:$PORT" LITELLM_API_KEY="mock" \
  bash eval/archive_results.sh >/dev/null 2>&1
code=$?
set -e
check "fails after the endpoint disappears" "$([ "$code" -ne 0 ] && echo 0 || echo 1)"
check "removes the partial directory" "$([ ! -d eval/results ] && echo 0 || echo 1)"

echo
echo "passed: $PASS   failed: $FAIL"
echo "note: any archive produced here was MOCK DATA and has been deleted."
[ "$FAIL" -eq 0 ] || exit 1
