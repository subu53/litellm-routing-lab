"""Check which candidate keywords survive a leakage guard.

A keyword is only usable for tuning if it has support in the tuning half and does
not appear in the held-out half. Keywords that appear only inside the prompts they
are meant to classify are leakage: the measured gain would not generalise.

Usage:
  python3 tools/check_keywords.py

No network and no API key needed.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from evaluate_router import load_prompts, DOMAIN_KEYWORDS, keyword_ok

MIN_SUPPORT = 3


def main():
    prompts = load_prompts()
    tuning = prompts["tuning"]
    held = prompts["held_out"]
    print(f"tuning prompts: {len(tuning)}   held-out prompts: {len(held)}")
    print(f"guard: support >= {MIN_SUPPORT} tuning prompts AND zero held-out hits\n")
    print(f"  {'keyword':22} {'support':>8} {'heldout':>8}  verdict")
    kept, dropped = [], []
    for kw in DOMAIN_KEYWORDS:
        support = sum(kw.lower() in p["text"].lower() for p in tuning)
        leaked = sum(kw.lower() in p["text"].lower() for p in held)
        ok = keyword_ok(kw, tuning, held, MIN_SUPPORT)
        if ok:
            kept.append(kw)
        else:
            dropped.append(kw)
        reason = ""
        if support < MIN_SUPPORT:
            reason = "too little support"
        if leaked:
            reason = "appears in held-out"
        print(f"  {kw:22} {support:>8} {leaked:>8}  "
              f"{'KEEP' if ok else 'DROP'} {reason}")

    print(f"\n{len(kept)} of {len(DOMAIN_KEYWORDS)} keywords survive.")
    if kept:
        print("surviving keywords:", kept)
    else:
        print("No keyword survives the guard. That is a legitimate outcome: the")
        print("candidate list was derived from the benchmark prompts themselves.")
    if dropped:
        print("dropped (would not generalise):", dropped)
    print("\nNote: the held-out column is used only to validate keyword choices.")
    print("Reading held-out COMPLETION results more than once invalidates it as")
    print("a confirmation set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
