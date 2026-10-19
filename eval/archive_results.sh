#!/usr/bin/env bash
# Archive the evaluation evidence next to the claims it supports.
#
# The README quotes numbers that otherwise exist only in terminal scrollback. This
# writes them, plus the versions needed to reproduce them, into
# eval/results/<date>-<short-sha>/ so a reader can compare their own run against it.
#
# Usage, from the repository root, with the Proxy already running and its
# LITELLM_BASE_URL / LITELLM_API_KEY exported:
#
#   bash eval/archive_results.sh
#
# Costs no completion requests: every step uses POST /auto_router/test_routing.
#
# Portability notes, because this script is meant to run in Cloud Shell and has been
# checked on Windows too:
#   - Picks python3, falling back to python. On Windows the python3 shim can be a
#     Store stub that does not actually run.
#   - Uses python for the config hash rather than sha256sum, which is absent on macOS
#     and often on Windows.
#   - Fails before creating the output directory if the proxy is not answering, so a
#     dead proxy cannot leave a half-filled archive behind.
#   - Every step is required. With `set -e`, a failing tool aborts the run and the
#     partial directory is removed.

set -euo pipefail

# --- preconditions ---------------------------------------------------------------
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "FAIL: neither python3 nor python is usable on PATH." >&2
  exit 1
fi

if [ ! -f config.gemini.yaml ]; then
  echo "FAIL: config.gemini.yaml not found. Run from the repository root." >&2
  exit 1
fi

if [ -z "${LITELLM_BASE_URL:-}" ] || [ -z "${LITELLM_API_KEY:-}" ]; then
  echo "FAIL: set LITELLM_BASE_URL and LITELLM_API_KEY, and start the proxy first." >&2
  echo '  export LITELLM_BASE_URL="http://localhost:4000"' >&2
  echo '  export LITELLM_API_KEY="$LITELLM_MASTER_KEY"' >&2
  exit 1
fi

DATE="$(date +%Y-%m-%d)"
SHA="$(git rev-parse --short HEAD)"
OUT="eval/results/${DATE}-${SHA}"

cleanup_on_failure() {
  if [ -d "$OUT" ]; then
    echo "FAILED: removing incomplete archive at $OUT" >&2
    rm -rf "$OUT"
  fi
}
trap cleanup_on_failure ERR

# --- preflight: refuse to build an archive from a dead proxy ----------------------
echo "Preflight: checking $LITELLM_BASE_URL ..."
if ! "$PY" tools/evaluate_router.py --dump-scores >/dev/null 2>&1; then
  echo "FAIL: the proxy did not answer --dump-scores." >&2
  echo "Start it and retry: litellm --config config.gemini.yaml --port 4000 &" >&2
  echo "Reason given by the tool:" >&2
  "$PY" tools/evaluate_router.py --dump-scores 2>&1 | tail -n 3 >&2 || true
  exit 1
fi

mkdir -p "$OUT"
echo "Archiving to $OUT"

# --- environment -----------------------------------------------------------------
{
  echo "date: $DATE"
  echo "commit: $(git rev-parse HEAD)"
  echo "branch: $(git rev-parse --abbrev-ref HEAD)"
  echo "config_sha256: $("$PY" -c 'import hashlib,sys;print(hashlib.sha256(open("config.gemini.yaml","rb").read()).hexdigest())')"
  echo "python: $("$PY" --version 2>&1)"
  echo "platform: $(uname -a 2>/dev/null || echo unknown)"
  echo
  echo "## pip show litellm"
  "$PY" -m pip show litellm 2>/dev/null || echo "litellm not found in this environment"
  echo
  echo "## relevant packages"
  "$PY" -m pip freeze 2>/dev/null | grep -Ei 'litellm|openai|httpx|pydantic|fastapi|uvicorn' || true
} > "$OUT/environment.txt"
echo "  wrote environment.txt"

# --- the config actually under test ----------------------------------------------
cp config.gemini.yaml "$OUT/config.gemini.yaml"
echo "  wrote config.gemini.yaml"

# --- scores ----------------------------------------------------------------------
"$PY" tools/evaluate_router.py --dump-scores > "$OUT/dump-scores.txt" 2>&1
if [ ! -f scores.csv ]; then
  echo "FAIL: evaluate_router.py did not produce scores.csv" >&2
  exit 1
fi
cp scores.csv "$OUT/scores.csv"
echo "  wrote scores.csv and dump-scores.txt"

# --- thresholds ------------------------------------------------------------------
"$PY" tools/sweep_thresholds.py "$OUT/scores.csv" > "$OUT/sweep-thresholds.txt" 2>&1
echo "  wrote sweep-thresholds.txt"

# --- length confound -------------------------------------------------------------
"$PY" tools/compare_length.py "$OUT/scores.csv" > "$OUT/compare-length.txt" 2>&1
echo "  wrote compare-length.txt"

"$PY" tools/test_length_confound.py > "$OUT/2x2-confounds.txt" 2>&1
echo "  wrote 2x2-confounds.txt"

# --- optional LLM baseline, if it has been run -----------------------------------
HAS_LLM=0
if [ -f "llm_baseline.csv" ]; then
  cp llm_baseline.csv "$OUT/llm_baseline.csv"
  HAS_LLM=1
  echo "  wrote llm_baseline.csv"
fi

# --- index -----------------------------------------------------------------------
{
  echo "# Evaluation evidence, $DATE (commit $SHA)"
  echo
  echo "Produced by the scripts in tools/ against config.gemini.yaml in this directory."
  echo "The README quotes these numbers; this is the raw output they came from."
  echo
  echo "| File | Produced by | What it shows |"
  echo "|---|---|---|"
  echo "| environment.txt | git, pip, hashlib | commit, config hash, LiteLLM and Python versions |"
  echo "| config.gemini.yaml | cp | the exact config under test |"
  echo "| scores.csv | evaluate_router.py --dump-scores | one classifier score per prompt |"
  echo "| dump-scores.txt | same run | its console summary, including scores to Flash |"
  echo "| sweep-thresholds.txt | sweep_thresholds.py | under- and over-routing at every threshold |"
  echo "| compare-length.txt | compare_length.py | score AUC vs word-count AUC |"
  echo "| 2x2-confounds.txt | test_length_confound.py | the pre-registered four-cell test |"
  if [ "$HAS_LLM" -eq 1 ]; then
    echo "| llm_baseline.csv | llm_baseline.py | an LLM classifier baseline, not Jev |"
  fi
  echo
  echo "Reproduce: start the proxy with config.gemini.yaml, export"
  echo "LITELLM_BASE_URL and LITELLM_API_KEY, then run \`bash eval/archive_results.sh\`"
  echo "from the repository root."
} > "$OUT/README.md"

trap - ERR
echo
echo "Wrote $OUT with $(ls -1 "$OUT" | wc -l) files."
echo "Next: git add eval/results && git commit -m 'Archive evaluation evidence'"
