"""An LLM classifier baseline on the 2x2 cells.

This is NOT Jev and must never be described as "the learned classifier". It is a
general-purpose LLM asked to do the classification job in one word, used to answer a
single question: does ANY classifier beat prompt length on these cells, where the
built-in heuristic did not?

It also produces latency and cost numbers for the classification step, which the
built-in heuristic does not have.

Design notes:
  - One word answer, "easy" or "hard", nothing else, to keep output cheap.
  - The prompt never shows the expected label, and never says what the cells are.
  - Uses the same 40 prompts as the pre-registered 2x2 test, and applies the same
    pre-registered criteria, so the two tables are directly comparable.
  - Writes llm_baseline.csv with the raw answers plus latency, then prints the cell
    table and the verdict.

Cost: 40 short completions. On Gemini 3.1 Flash-Lite's free tier (500/day) this is
comfortably inside quota; on Gemini 3 Flash (20/day) it is not.

Usage:
  export GEMINI_API_KEY=...            # direct to Google, no proxy needed
  python3 tools/llm_baseline.py
  python3 tools/llm_baseline.py --model gemini-3-flash
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from test_length_confound import load, WANT  # same prompts, same cells, same criteria

DEFAULT_MODEL = "gemini-3.1-flash-lite"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM = (
    "You classify how much reasoning a request needs. Answer with exactly one word: "
    "hard if answering well requires multi-step reasoning, trade-offs, or design "
    "judgement; easy if it needs a single fact, definition, lookup, or a one-line "
    "answer. Output only the word, nothing else."
)


def classify(api_key, model, text):
    payload = json.dumps({
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {"maxOutputTokens": 8, "temperature": 0},
    }).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=payload,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed = round((time.perf_counter() - started) * 1000, 1)

    # A blocked or safety-filtered response has no candidates at all, and an empty
    # list would raise IndexError; a null body would raise TypeError. Record it as
    # "unparsed" instead of crashing, and let the caller refuse to judge on it.
    candidates = body.get("candidates") or [{}]
    first = candidates[0] or {}
    parts = (first.get("content") or {}).get("parts") or []
    raw = "".join(p.get("text", "") for p in parts).strip().lower()
    blocked = bool((body.get("promptFeedback") or {}).get("blockReason"))
    usage = body.get("usageMetadata", {})
    return {
        "answer_raw": raw[:20],
        "verdict": "unparsed" if not raw or blocked else (
            "hard" if raw.startswith("hard") else "easy"),
        "latency_ms": elapsed,
        "prompt_tokens": usage.get("promptTokenCount", 0),
        "output_tokens": usage.get("candidatesTokenCount", 0),
    }


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    margin = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY first.")
    model = DEFAULT_MODEL
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]

    cells = load()
    rows = []
    print(f"LLM classifier baseline ({model})")
    print("Not Jev. A general-purpose LLM asked for one word.\n")

    for cell, prompts in cells.items():
        want = "hard" if WANT[cell] == "gemini-flash" else "easy"
        for text in prompts:
            try:
                r = classify(api_key, model, text)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")[:200]
                raise SystemExit(f"HTTP {error.code}: {detail}")
            rows.append({"cell": cell, "expected": want, "text": text, **r})

    with open("llm_baseline.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"{'cell':12} {'correct':>9} {'unparsed':>9} {'median ms':>10}")
    for cell in cells:
        sub = [r for r in rows if r["cell"] == cell]
        good = sum(1 for r in sub if r["verdict"] == r["expected"])
        unparsed = sum(1 for r in sub if r["verdict"] == "unparsed")
        latencies = sorted(r["latency_ms"] for r in sub)
        print(f"{cell:12} {f'{good}/{len(sub)}':>9} {unparsed:>9} "
              f"{latencies[len(latencies) // 2]:>10}")

    total_ok = sum(1 for r in rows if r["verdict"] == r["expected"])
    parsed = [r for r in rows if r["verdict"] != "unparsed"]
    lo, hi = wilson(total_ok, len(rows))
    print(f"\noverall: {total_ok}/{len(rows)} "
          f"(95% CI {lo * 100:.0f}%-{hi * 100:.0f}%)")
    tok_in = sum(r["prompt_tokens"] for r in rows)
    tok_out = sum(r["output_tokens"] for r in rows)
    print(f"tokens: {tok_in} in, {tok_out} out for {len(rows)} classifications")

    # An unparsed answer is not evidence about the classifier. Counting it as
    # wrong would turn a total parse failure into a substantive result.
    total_parsed = len(parsed)
    if total_parsed < len(rows) * 0.9:
        print(f"\nREFUSING A VERDICT: only {total_parsed}/{len(rows)} answers parsed. "
              "A parse rate this low says nothing about classification ability.")
        print("Wrote llm_baseline.csv for inspection.")
        return

    # The pre-registered criteria applied to this classifier, for a like-for-like
    # comparison. Unparsed rows are excluded so they cannot be scored as misses.
    short_hard_wrong = sum(1 for r in parsed if r["cell"] == "short_hard"
                           and r["verdict"] != "hard")
    long_easy_wrong = sum(1 for r in parsed if r["cell"] == "long_easy"
                          and r["verdict"] != "easy")
    print(f"\nsame pre-registered reading as the heuristic "
          f"({total_parsed} parsed answers):")
    print(f"  short+hard under-routed : {short_hard_wrong}/10")
    print(f"  long+easy over-routed   : {long_easy_wrong}/10")
    short_ok = sum(1 for r in parsed if r["cell"] == "short_hard")
    long_ok = sum(1 for r in parsed if r["cell"] == "long_easy")
    if short_hard_wrong >= 7 and long_easy_wrong >= 7:
        print("  -> this mode also fails the cells; length is not the only thing it sees,"
              " but it is not classifying difficulty either")
    elif short_hard_wrong <= 4 and long_easy_wrong <= 4:
        print("  -> this classifier does not reproduce the heuristic's cell pattern."
              " It is a candidate for the learned-classifier comparison.")
    else:
        print("  -> mixed result, in the 5-6 band the pre-registered rubric calls"
              " SUPPORTED-WEAK. Report it as mixed rather than as a win or a loss.")
    if short_ok < 10 or long_ok < 10:
        print(f"  note: cell denominators reduced (short_hard {short_ok}, "
              f"long_easy {long_ok}) because of unparsed answers.")
    print("\nWrote llm_baseline.csv. Run eval/archive_results.sh to file it.")


if __name__ == "__main__":
    main()
