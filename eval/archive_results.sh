#!/usr/bin/env bash
# Archive the evaluation evidence next to the claims it supports.
#
# The README quotes numbers that otherwise exist only in terminal scrollback. This
# writes them, plus the versions needed to reproduce them, into
# eval/results/<date>-<short-sha>/ and leaves a README there describing the run.
#
# Usage, from the repository root, with the proxy already running:
#   bash eval/archive_results.sh
#
# Costs nothing beyond test_routing calls. The optional LLM baseline, if present,
# is copied in rather than re-run.

set -euo pipefail

DATE="$(date +%Y-%m-%d)"
SHA="$(git rev-parse --short HEAD)"
OUT="eval/results/${DATE}-${SHA}"

mkdir -p "$OUT"
echo "Archiving to $OUT"

# --- environment -----------------------------------------------------------------
{
  echo "date: $DATE"
  echo "commit: $(git rev-parse HEAD)"
  echo "branch: $(git rev-parse --abbrev-ref HEAD)"
  echo "config_sha256: $(sha256sum config.gemini.yaml | cut -d' ' -f1)"
  echo "python: $(python3 --version 2>&1)"
  echo
  echo "## pip show litellm"
  pip show litellm 2>/dev/null || echo "litellm not found in this environment"
  echo
  echo "## pip freeze (relevant packages)"
  pip freeze 2>/dev/null | grep -Ei 'litellm|openai|httpx|pydantic|fastapi|uvicorn' || true
} > "$OUT/environment.txt"
echo "  wrote environment.txt"

# --- the config actually under test ----------------------------------------------
cp config.gemini.yaml "$OUT/config.gemini.yaml"
echo "  wrote config.gemini.yaml"

# --- scores ----------------------------------------------------------------------
python3 tools/evaluate_router.py --dump-scores > "$OUT/dump-scores.txt" 2>&1
cp scores.csv "$OUT/scores.csv"
echo "  wrote scores.csv and dump-scores.txt"

# --- thresholds ------------------------------------------------------------------
python3 tools/sweep_thresholds.py "$OUT/scores.csv" > "$OUT/sweep-thresholds.txt" 2>&1
echo "  wrote sweep-thresholds.txt"

# --- length confound -------------------------------------------------------------
python3 tools/compare_length.py "$OUT/scores.csv" > "$OUT/compare-length.txt" 2>&1
echo "  wrote compare-length.txt"

python3 tools/test_length_confound.py > "$OUT/2x2-confounds.txt" 2>&1
echo "  wrote 2x2-confounds.txt"

# --- optional LLM baseline, if it has been run -----------------------------------
if [ -f "llm_baseline.csv" ]; then
  cp llm_baseline.csv "$OUT/llm_baseline.csv"
  echo "  wrote llm_baseline.csv"
fi

# --- index -----------------------------------------------------------------------
{
  echo "# Evaluation evidence, $DATE (commit $SHA)"
  echo
  echo "Everything here was produced by the scripts in tools/ against the config in"
  echo "this directory. The README quotes these numbers."
  echo
  echo "| File | Produced by | What it shows |"
  echo "|---|---|---|"
  echo "| environment.txt | pip show litellm, git, sha256sum | the versions to reproduce with |"
  echo "| config.gemini.yaml | cp | the exact config under test |"
  echo "| scores.csv | evaluate_router.py --dump-scores | one classifier score per prompt |"
  echo "| dump-scores.txt | same run | its console summary |"
  echo "| sweep-thresholds.txt | sweep_thresholds.py | under/over routing at every threshold |"
  echo "| compare-length.txt | compare_length.py | score AUC vs word-count AUC |"
  echo "| 2x2-confounds.txt | test_length_confound.py | the pre-registered four-cell test |"
  if [ -f "llm_baseline.csv" ]; then
    echo "| llm_baseline.csv | llm_baseline.py | an LLM classifier baseline, not Jev |"
  fi
  echo
  echo "To reproduce: start the proxy with config.gemini.yaml, then run"
  echo "\`bash eval/archive_results.sh\` from the repository root."
} > "$OUT/README.md"

echo
echo "Wrote $OUT with $(ls -1 "$OUT" | wc -l) files."
echo "Commit it: git add eval/results && git commit -m 'Archive evaluation evidence'"
