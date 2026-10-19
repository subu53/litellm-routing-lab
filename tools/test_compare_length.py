"""Regression tests for the length-vs-score verdict logic.

The verdict must compare the two AUCs DIRECTLY. An earlier version printed
"score clearly out-ranks length" for a score of 0.725 against a word-count AUC of
1.000, which contradicted its own printed numbers. These tests pin the direction.

Numbers below are the real measured AUCs from this project's runs.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from compare_length import verdict_for

results = []


def case(label, a_score, a_words, expect_substring):
    text = verdict_for(a_score, a_words)
    ok = expect_substring.lower() in text.lower()
    results.append((ok, label, text, expect_substring))


print("verdict direction")
print("=================")

# Real measurements from the 60-prompt run.
case("tuning: score 0.929 vs words 1.000", 0.929, 1.000,
     "word count separates the classes better")
case("held-out: score 0.725 vs words 1.000", 0.725, 1.000,
     "word count separates the classes better")
case("pooled: score 0.836 vs words 0.999", 0.836, 0.999,
     "word count separates the classes better")

# The regression that motivated this file: previously printed the opposite.
case("regression: 0.725 vs 1.000 must NOT claim score wins", 0.725, 1.000,
     "word count separates")

# Genuine score win.
case("score wins: 0.95 vs 0.55", 0.95, 0.55, "score separates the classes better")

# Near-ties are called ties, either direction.
case("tie: 0.80 vs 0.82", 0.80, 0.82, "rank almost identically")
case("tie: 0.82 vs 0.80", 0.82, 0.80, "rank almost identically")

# Exact boundary of the tolerance.
case("just inside tolerance: 0.85 vs 0.89", 0.85, 0.89, "rank almost identically")
case("just outside tolerance: 0.85 vs 0.95", 0.85, 0.95,
     "word count separates the classes better")

# A score below 0.5 is worse than chance; word count should still win.
case("below chance score: 0.40 vs 0.70", 0.40, 0.70,
     "word count separates the classes better")


def numbers_interpolate():
    """The verdict must PRINT the values it was given, not hardcoded literals.

    Substring checks above cannot catch a message that always says "1.000 vs
    0.725". This feeds values that share no digits with the measured case and
    requires both to appear in the formatted output.
    """
    problems = []
    for a_score, a_words in [(0.9, 1.0), (0.123, 0.456), (0.611, 0.612)]:
        text = verdict_for(a_score, a_words)
        for value in (a_score, a_words):
            rendered = f"{value:.3f}"
            if rendered not in text:
                problems.append(f"verdict_for({a_score}, {a_words}) omits "
                                f"{rendered!r}: {text!r}")
    return problems


print("\nnumber interpolation")
print("===================")
problems = numbers_interpolate()
if problems:
    for p in problems:
        print("FAIL ", p)
    results.append((False, "verdict interpolates its numbers", "; ".join(problems), ""))
else:
    print("PASS  0.9 vs 1.0 renders '0.900' and '1.000'")
    print("PASS  0.123 vs 0.456 renders both values")
    print("PASS  0.611 vs 0.612 renders both values")
    results.append((True, "verdict interpolates its numbers", "", ""))

failed = [r for r in results if not r[0]]
for ok, label, text, expected in results:
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"      got     : {text}")
        print(f"      expected: {expected!r}")
print()
if failed:
    print(f"{len(failed)}/{len(results)} failed")
    sys.exit(1)
print(f"All {len(results)} verdict tests passed.")
