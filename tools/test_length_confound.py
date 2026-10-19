"""Run the 2x2 length/hardness confound test.

Loads eval/prompts_2x2.yaml, scores every prompt once via the proxy, and reports how
each cell is routed. The point is to separate "the heuristic detects difficulty" from
"the heuristic detects length".

Pass criteria were fixed in the prompt file before running and are printed here with
the result, so the criteria cannot be adjusted to fit the outcome.

Usage:
  export LITELLM_BASE_URL="http://localhost:4000"
  export LITELLM_API_KEY="$LITELLM_MASTER_KEY"
  python3 tools/test_length_confound.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATH = os.path.join(ROOT, "eval", "prompts_2x2.yaml")

# Which model each cell should reach if the router measured difficulty.
WANT = {"short_hard": "gemini-flash", "long_hard": "gemini-flash",
        "short_easy": "gemini-flash-lite", "long_easy": "gemini-flash-lite"}

TIERS = {"SIMPLE": "gemini-flash-lite", "MEDIUM": "gemini-flash",
         "COMPLEX": "gemini-flash", "REASONING": "gemini-flash"}
# The interim threshold t = -0.075 with the tier map in config.gemini.yaml:
# SIMPLE (cheap) is everything below t; MEDIUM and above are the strong model.
THRESHOLD = -0.075


def load(path=PATH):
    cells = {k: [] for k in WANT}
    current = None
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith(" ") and stripped.endswith(":"):
                current = stripped[:-1]
                if current not in cells:
                    raise SystemExit(f"{lineno}: unknown cell {current!r}")
                continue
            if stripped.startswith("- text:"):
                value = stripped.split(":", 1)[1].strip()
                if current is None:
                    raise SystemExit(f"{lineno}: prompt before a cell name")
                cells[current].append(value.strip('"'))
    for name, items in cells.items():
        if not items:
            raise SystemExit(f"cell {name} has no prompts")
    return cells


def classify(base_url, api_key, prompt):
    payload = json.dumps({
        "model": "jev-router",
        "messages": [{"role": "user", "content": prompt}],
        "complexity_router_config": {
            "tiers": TIERS,
            "classifier_type": "heuristic",
            "return_raw_model_name": True,
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/auto_router/test_routing",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    base_url = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("Set LITELLM_BASE_URL and LITELLM_API_KEY first.")

    cells = load()
    print("2x2 length/hardness confound test")
    print(f"interim threshold t={THRESHOLD}: SIMPLE (cheap) below t, strong at or above\n")

    counts = {}
    signals_by_cell = {}
    for name, prompts in cells.items():
        want = WANT[name]
        rows = []
        for text in prompts:
            body = classify(base_url, api_key, text)
            decision = body.get("routing_decision", {})
            score = decision.get("score")
            words = len(text.split())
            signals = "; ".join(decision.get("signals", [])) or "none"
            # Apply the interim policy ourselves: score >= t -> strong model.
            got = "gemini-flash" if (score is not None and score >= THRESHOLD) \
                else "gemini-flash-lite"
            rows.append({"score": score, "words": words, "got": got,
                         "ok": got == want, "signals": signals, "text": text})
        good = sum(r["ok"] for r in rows)
        counts[name] = good
        signals_by_cell[name] = rows
        print(f"{name}: {good}/{len(rows)} routed as difficulty would require")
        for r in rows:
            mark = "ok  " if r["ok"] else "MISS"
            print(f"   {mark} score={r['score']!s:>8} words={r['words']:>3} "
                  f"-> {r['got'].replace('gemini-', ''):<10} [{r['signals']}]")
        print()

    # Which markers fired in each cell. Shows what the heuristic reacts to besides
    # length and a "step by step" phrase.
    print("signals by cell")
    print("---------------")
    for name, rows in signals_by_cell.items():
        tally = {}
        for r in rows:
            for sig in r["signals"].split("; "):
                tally[sig] = tally.get(sig, 0) + 1
        ordered = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
        print(f"  {name:12} " + ", ".join(f"{k} x{v}" for k, v in ordered))
    print()

    short_hard_wrong = len(cells["short_hard"]) - counts["short_hard"]
    long_easy_wrong = len(cells["long_easy"]) - counts["long_easy"]

    print("=" * 62)
    print("PRE-REGISTERED CRITERIA (from eval/prompts_2x2.yaml)")
    print("  SUPPORTED      : >=7/10 short+hard on cheap AND >=7/10 long+easy on strong")
    print("  SUPPORTED-weak : 5-6 in either cell")
    print("  REJECTED       : <=4 in both cells")
    print()
    print(f"RESULT")
    print(f"  short+hard under-routed : {short_hard_wrong}/10 "
          f"(predicted: high)")
    print(f"  long+easy over-routed   : {long_easy_wrong}/10 "
          f"(predicted: high)")

    if short_hard_wrong >= 7 and long_easy_wrong >= 7:
        verdict = "SUPPORTED - the heuristic behaves as a length gate on this set"
    elif short_hard_wrong <= 4 and long_easy_wrong <= 4:
        verdict = "REJECTED - length does not explain the routing"
    else:
        verdict = "SUPPORTED-WEAK - length explains part of the behaviour"
    print(f"\n  VERDICT: {verdict}")
    print("\n  Report whatever this says. The criteria are not adjusted afterwards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
