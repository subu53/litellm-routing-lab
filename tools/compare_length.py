"""Compare how well the classifier score predicts hardness against prompt LENGTH.

A confound check. If a hard/easy split is really a long/short split, then the
heuristic's apparent skill is just a length gate, and any threshold that looks good
is detecting sentence count rather than difficulty.

AUC here is the probability that a randomly chosen hard prompt ranks above a
randomly chosen easy prompt on the given key. 0.5 means no discriminating power,
1.0 means perfect separation. Ties count as half a win.

Usage:
  python3 tools/compare_length.py scores.csv
"""
import csv
import sys


def load(path):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for r in rows:
        r["score"] = float(r["score"])
        r["words"] = int(r["words"])
    return rows


def auc(rows, key):
    hard = [r[key] for r in rows if r["label"] == "hard"]
    easy = [r[key] for r in rows if r["label"] == "easy"]
    if not hard or not easy:
        return float("nan")
    wins = sum((h > e) + 0.5 * (h == e) for h in hard for e in easy)
    return wins / (len(hard) * len(easy))


def verdict_for(a_score, a_words, tolerance=0.05):
    """Judge which predictor separates the classes better.

    Compares the two AUCs DIRECTLY. An earlier version tested
    abs(score - words) <= 0.10 and otherwise printed "score clearly out-ranks
    length", which reported a win for the score at 0.725 against 1.000 - the
    opposite of the data. Direction matters, so it is stated explicitly here.
    """
    gap = abs(a_score - a_words)
    if gap <= tolerance:
        return ("score and length rank almost identically - the classifier may be "
                "acting as a length gate")
    if a_words > a_score:
        return ("word count separates the classes better than the score "
                f"({a_words:.3f} vs {a_score:.3f}) - the classifier adds nothing "
                "beyond length on this set")
    return (f"score separates the classes better than word count "
            f"({a_score:.3f} vs {a_words:.3f})")


def summarise(rows, label):
    a_score = auc(rows, "score")
    a_words = auc(rows, "words")
    hard = [r for r in rows if r["label"] == "hard"]
    easy = [r for r in rows if r["label"] == "easy"]
    hw = sorted(r["words"] for r in hard)
    ew = sorted(r["words"] for r in easy)
    median = lambda v: v[len(v) // 2] if v else 0
    print(f"\n{label}  ({len(hard)} hard, {len(easy)} easy)")
    print(f"  AUC by classifier score : {a_score:.3f}")
    print(f"  AUC by word count       : {a_words:.3f}")
    print(f"  median words (hard/easy): {median(hw)} / {median(ew)}")
    overlap = [w for w in hw if w in set(ew)]
    print(f"  word counts shared by both classes: {len(overlap)}/{len(hard)} hard prompts")
    print(f"  -> {verdict_for(a_score, a_words)}")
    return a_score, a_words


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python3 tools/compare_length.py scores.csv")
    rows = load(sys.argv[1])
    print("Does the classifier beat a plain word count at separating hard from easy?")
    for split in ("tuning", "held_out"):
        sub = [r for r in rows if r["split"] == split]
        if sub:
            summarise(sub, f"split = {split}")
    summarise(rows, "POOLED (all prompts)")

    both = [r for r in rows if r["label"] == "hard"]
    print("\n  hard prompts by score bucket:")
    buckets = {}
    for r in both:
        buckets.setdefault(r["score"], []).append(r["words"])
    for score in sorted(buckets):
        words = buckets[score]
        print(f"    score={score:<8} n={len(words):<3} "
              f"median words={sorted(words)[len(words) // 2]}")

    print("\n  If the lowest score bucket holds long prompts too, length alone does")
    print("  not explain the score. If short prompts sit only in the lowest bucket")
    print("  and long ones only in the reasoning bucket, it does.")


if __name__ == "__main__":
    main()
