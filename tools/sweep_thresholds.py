"""Sweep every possible routing threshold offline from precomputed scores.

A prompt's classifier score does not depend on the tier boundaries, so one scoring
pass over the prompt set is enough to evaluate every threshold. This replaces the
grid sweep, which tested six points and, worse, only tested thresholds at or above
0.20 -- above both clusters where the misroutes live (0.0 and 0.175).

The router can only choose between two models, so the single number that matters is
the score at which a prompt moves from the cheap model to the strong one. Call it t:
score >= t goes to Flash. Only t changes a decision. In the config that means:

    simple_medium     = t - 0.05
    medium_complex    = t
    complex_reasoning = max(0.60, t + 0.10)

simple_medium and medium_complex both map to the cheap model, and complex_reasoning
sits above both clusters, so the two outer values move labels only. They are kept
strictly increasing so the config stays valid.

Usage:
  python3 tools/sweep_thresholds.py scores.csv
  python3 tools/sweep_thresholds.py scores.csv --split held_out
"""
import csv
import sys


def load(path):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for r in rows:
        r["score"] = float(r["score"])
    return rows


def candidates(rows):
    """Every midpoint between distinct observed scores, plus the two ends.

    Midpoints rather than the scores themselves, so whether the router applies
    >= or > at the boundary cannot change which prompts move.
    """
    scores = sorted({r["score"] for r in rows})
    if not scores:
        raise SystemExit("no rows to sweep: check the CSV path and the --split value")
    points = [round(scores[0] - 0.01, 4)]
    points += [round((a + b) / 2, 4) for a, b in zip(scores, scores[1:])]
    points.append(round(scores[-1] + 0.01, 4))
    return points


def curve(rows, label=""):
    hard = [r for r in rows if r["label"] == "hard"]
    easy = [r for r in rows if r["label"] == "easy"]
    print(f"\n{label}")
    print(f"  {len(rows)} prompts: {len(hard)} hard (want Flash), "
          f"{len(easy)} easy (want cheap)")
    print(f"\n  {'threshold':>9} {'to Flash':>10} {'under':>11} {'over':>11} "
          f"{'sum':>5}")
    best = None
    for t in candidates(rows):
        under = sum(1 for r in hard if r["score"] < t)
        over = sum(1 for r in easy if r["score"] >= t)
        to_flash = sum(1 for r in rows if r["score"] >= t)
        total = under + over
        if best is None or total < best[1]:
            best = (t, total, under, over, to_flash)
        print(f"  {t:9.3f} {to_flash:>10} "
              f"{f'{under}/{len(hard)}':>11} {f'{over}/{len(easy)}':>11} {total:>5}")
    print(f"\n  fewest total misroutes: threshold={best[0]} "
          f"(under={best[2]}, over={best[3]}, to Flash={best[4]})")
    return best


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python3 tools/sweep_thresholds.py scores.csv "
                         "[--split tuning|held_out]")
    rows = load(sys.argv[1])
    split = None
    if "--split" in sys.argv:
        split = sys.argv[sys.argv.index("--split") + 1]

    if split:
        curve([r for r in rows if r["split"] == split], f"split = {split}")
    else:
        for name in ("tuning", "held_out"):
            curve([r for r in rows if r["split"] == name], f"split = {name}")
        curve(rows, "POOLED (all prompts)")

    # How much of the easy set a low threshold would cost, since that is the
    # actual tradeoff the earlier grid could not show.
    easy = [r for r in rows if r["label"] == "easy"]
    hard = [r for r in rows if r["label"] == "hard"]
    print("\n  What a low threshold costs, over all prompts:")
    for t in (0.0, 0.05, 0.10, 0.15, 0.175, 0.20):
        under = sum(1 for r in hard if r["score"] < t)
        over = sum(1 for r in easy if r["score"] >= t)
        print(f"    t={t:<6} rescues {len(hard) - under:>2}/{len(hard)} hard prompts, "
              f"misroutes {over:>2}/{len(easy)} easy prompts")


if __name__ == "__main__":
    main()
