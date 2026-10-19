"""Evaluate the complexity router on a labelled set, with a tuning/held-out split.

Why this exists: an earlier version judged routing by comparing the classifier's
tier to a hand-written expected tier. That over-counted failures, because SIMPLE
and MEDIUM both map to the cheap model and COMPLEX and REASONING both map to the
strong one - the router really makes a binary choice. It also encouraged picking
keywords and boundaries on the same four prompts used to report results, which is
training on the test set.

This tool scores the MODEL the router chose, reports under-routing and
over-routing separately because they cost different things, and can only report
one tuning result and one held-out result from the same configuration.

Uses POST /auto_router/test_routing, which spends no completion requests.

Usage:
  export LITELLM_BASE_URL="http://localhost:4000"
  export LITELLM_API_KEY="$LITELLM_MASTER_KEY"

  python3 tools/evaluate_router.py                    # score current config
  python3 tools/evaluate_router.py --sweep            # sweep medium_complex
  python3 tools/evaluate_router.py --keywords         # include custom keywords
"""
import csv
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROMPTS_PATH = os.path.join(ROOT, "eval", "prompts.yaml")

# The router is binary at the model level: these two tiers are cheap, these two
# are strong. Under-routing and over-routing are defined against that.
TIERS = {
    "SIMPLE": "gemini-flash-lite",
    "MEDIUM": "gemini-flash-lite",
    "COMPLEX": "gemini-flash",
    "REASONING": "gemini-flash",
}
STRONG = "gemini-flash"

DEFAULT_BOUNDARIES = {"simple_medium": 0.15, "medium_complex": 0.35,
                      "complex_reasoning": 0.60}

DOMAIN_KEYWORDS = [
    "event-driven", "idempotency", "idempotent", "observability", "pipeline",
    "retries", "ordering", "failure isolation", "synchronous", "latency",
    "trade-off", "tradeoff",
]

MIN_KEYWORD_SUPPORT = 3


def keyword_ok(kw, tuning_prompts, heldout_prompts, min_support=MIN_KEYWORD_SUPPORT):
    """A keyword is usable only with support in tuning and no held-out presence."""
    kw = kw.lower()
    support = sum(kw in p["text"].lower() for p in tuning_prompts)
    leaked = any(kw in p["text"].lower() for p in heldout_prompts)
    return support >= min_support and not leaked


def surviving_keywords(tuning_prompts, heldout_prompts):
    return [kw for kw in DOMAIN_KEYWORDS
            if keyword_ok(kw, tuning_prompts, heldout_prompts)]


# ------------------------------------------------------------------ statistics
def wilson_interval(k, n, z=1.96):
    """95% Wilson score interval for a proportion, without external dependencies.

    Wilson rather than normal approximation because counts here are small and can
    be zero, where the normal interval collapses to a point.
    """
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def format_interval(k, n):
    low, high = wilson_interval(k, n)
    return f"{k}/{n} (95% CI {low * 100:.0f}%-{high * 100:.0f}%)"



# ------------------------------------------------------------------ prompts
def load_prompts(path=PROMPTS_PATH):
    """Parse the deliberately simple prompts.yaml without a YAML dependency.

    Shape: two top-level keys, each a list of '- tier: X' / '  text: ...' pairs.
    """
    sections = {"tuning": [], "held_out": []}
    current = None
    entry = None
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line.startswith(" "):
                key = line.rstrip(":").strip()
                if key not in sections:
                    raise SystemExit(f"{path}:{lineno} unexpected section {key!r}")
                current = key
                continue
            stripped = line.strip()
            if stripped.startswith("- tier:"):
                entry = {"tier": stripped.split(":", 1)[1].strip()}
                sections[current].append(entry)
            elif stripped.startswith("text:"):
                if entry is None:
                    raise SystemExit(f"{path}:{lineno} text before tier")
                value = stripped.split(":", 1)[1].strip()
                # Drop an explanatory trailing comment outside quotes.
                if " #" in value:
                    value = value.split(" #", 1)[0].strip()
                entry["text"] = value.strip('"')
            else:
                raise SystemExit(f"{path}:{lineno} unrecognised line {stripped!r}")
    for name, items in sections.items():
        for i, item in enumerate(items):
            if "text" not in item:
                raise SystemExit(f"{name}[{i}] has no text")
            if item["tier"] not in TIERS:
                raise SystemExit(f"{name}[{i}] bad tier {item['tier']!r}")
    return sections


# ------------------------------------------------------------------ router call
def classify(base_url, api_key, prompt, boundaries, extra=None):
    config = {
        "tiers": TIERS,
        "classifier_type": "heuristic",
        "return_raw_model_name": True,
        "tier_boundaries": boundaries,
    }
    if extra:
        config.update(extra)
    payload = json.dumps({
        "model": "jev-router",
        "messages": [{"role": "user", "content": prompt}],
        "complexity_router_config": config,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/auto_router/test_routing",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        raise SystemExit(f"test_routing failed with HTTP {error.code}: {detail}")


def run_set(base_url, api_key, items, boundaries, extra=None):
    """Classify every prompt and return per-prompt results."""
    results = []
    for item in items:
        body = classify(base_url, api_key, item["text"], boundaries, extra)
        decision = body.get("routing_decision", {})
        results.append({
            "tier_expected": item["tier"],
            "text": item["text"],
            "score": decision.get("score"),
            "tier_got": decision.get("tier"),
            "model_got": body.get("routed_model") or decision.get("routed_model"),
            "signals": decision.get("signals", []),
        })
    return results


# ------------------------------------------------------------------ scoring
def score(results):
    """Score by the model chosen, separating the two failure directions.

    under_routed: a hard prompt sent to the cheap model -> quality risk
    over_routed:  an easy prompt sent to the strong model -> wasted spend
    """
    ok = under = over = 0
    for r in results:
        want = TIERS[r["tier_expected"]]
        got = r["model_got"]
        if got == want:
            ok += 1
        elif want == STRONG:
            under += 1
        else:
            over += 1
    n = len(results)
    return {"accuracy": f"{ok}/{n}", "correct": ok, "n": n,
            "under_routed": under, "over_routed": over}


def print_detail(results, limit=None):
    print(f"    {'tier expected':13} {'score':>7} {'tier got':10} "
          f"{'model':21} verdict")
    for r in results[:limit]:
        want = TIERS[r["tier_expected"]]
        verdict = "ok" if r["model_got"] == want else (
            "UNDER" if want == STRONG else "over")
        print(f"    {r['tier_expected']:13} {r['score']!s:>7} "
              f"{r['tier_got']!s:10} {r['model_got']!s:21} {verdict}")


def summarise(label, results):
    s = score(results)
    print(f"  {label}: {s['accuracy']} correct  "
          f"under-routed={s['under_routed']}  over-routed={s['over_routed']}")
    return s


# ------------------------------------------------------------------ modes
def evaluate(base_url, api_key, extra, boundaries, detail=False):
    prompts = load_prompts()
    print(f"\nBoundaries: {boundaries}")
    if extra:
        print(f"Extra config: {extra}")
    tune = run_set(base_url, api_key, prompts["tuning"], boundaries, extra)
    print("\n  TUNING half (selection data)")
    t = summarise("tuning", tune)
    if detail:
        print_detail(tune)
    held = run_set(base_url, api_key, prompts["held_out"], boundaries, extra)
    print("\n  HELD-OUT half (report only this)")
    h = summarise("held-out", held)
    if detail:
        print_detail(held)
    return t, h


def preflight(base_url, api_key):
    """Refuse to run against a dead proxy.

    A sweep of 360 calls that all fail would previously still print a result
    table, because every prompt scored as a routing outcome rather than an error.
    """
    body = classify(base_url, api_key, "Reply with only the word ok.",
                    DEFAULT_BOUNDARIES)
    if "routing_decision" not in body:
        raise SystemExit(f"proxy responded without a routing_decision: {body}")
    print(f"preflight OK: {base_url} is classifying requests")


def dump_scores(base_url, api_key, path):
    """Score every prompt once and write CSV, including length and signals.

    A prompt's score does not depend on the boundaries, so one pass is enough to
    evaluate every possible threshold offline. label is 'hard' when the expected
    model is Flash and 'easy' otherwise - the only distinction the router can act
    on, since SIMPLE/MEDIUM share a model and COMPLEX/REASONING share a model.

    `words` and `signals` are recorded so the score can be compared against prompt
    length. If length predicts the label as well as the score does, the router is
    adding nothing beyond a length gate on this set.
    """
    prompts = load_prompts()
    rows = []
    for split in ("tuning", "held_out"):
        for index, item in enumerate(prompts[split], 1):
            body = classify(base_url, api_key, item["text"], DEFAULT_BOUNDARIES)
            decision = body.get("routing_decision", {})
            score = decision.get("score")
            if score is None:
                raise SystemExit(f"no score returned for {item['text'][:50]}")
            label = "hard" if TIERS[item["tier"]] == STRONG else "easy"
            rows.append({
                "id": f"{split}-{index:02d}",
                "split": split,
                "label": label,
                "score": score,
                "words": len(item["text"].split()),
                "signals": "; ".join(decision.get("signals", [])),
                "tier_expected": item["tier"],
                "text": item["text"],
            })
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "split", "label", "score",
                                                    "words", "signals",
                                                    "tier_expected", "text"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {path}: {len(rows)} prompts")
    print(f"  scores: min={min(float(r['score']) for r in rows)} "
          f"max={max(float(r['score']) for r in rows)}")
    for split in ("tuning", "held_out"):
        sub = [r for r in rows if r["split"] == split]
        hard = sum(1 for r in sub if r["label"] == "hard")
        print(f"  {split}: {len(sub)} prompts, {hard} hard / {len(sub) - hard} easy")

    # Report the routing outcome for EVERY threshold candidate, not one assumed
    # default. An earlier version reported only the DEFAULT_BOUNDARIES cut, which
    # printed "0/60 to Flash" while the running config used -0.075 and sent 40/60.
    scores = sorted({float(r["score"]) for r in rows})
    midpoints = [scores[0] - 0.01] + [(a + b) / 2 for a, b in zip(scores, scores[1:])]
    print("\n  prompts sent to Flash, by threshold:")
    for t in sorted(set([DEFAULT_BOUNDARIES["medium_complex"]] + midpoints)):
        to_flash = sum(1 for r in rows if float(r["score"]) >= t)
        note = "  <- shipped default" if abs(t - DEFAULT_BOUNDARIES["medium_complex"]) < 1e-9 else ""
        print(f"    t={t:7.3f}  {to_flash:>2}/{len(rows)} to Flash{note}")
    return rows


def sweep(base_url, api_key):
    """Sweep medium_complex on the tuning half, confirm the pick on held-out.

    Only medium_complex is varied. SIMPLE and MEDIUM both map to the cheap model
    and COMPLEX and REASONING both map to the strong one, so the other two
    boundaries move the tier LABEL without ever changing the chosen model.
    Holding them fixed keeps them from looking tuned.

    Selection rule: fewest under-routed (quality) then fewest over-routed (spend),
    and among ties take the middle of the plateau rather than the first hit, so
    the chosen boundary has equal-scoring neighbours on both sides.
    """
    prompts = load_prompts()
    tuning, heldout = prompts["tuning"], prompts["held_out"]

    keywords = surviving_keywords(tuning, heldout)
    print(f"\nKeyword guard (support >= {MIN_KEYWORD_SUPPORT} in tuning, "
          f"absent from held-out):")
    print(f"  {len(keywords)} of {len(DOMAIN_KEYWORDS)} candidates survive: {keywords}")
    if not keywords:
        print("  no keyword survives, so the keyword arm of the sweep is skipped")

    print(f"\nSweeping medium_complex over the TUNING half "
          f"({len(tuning)} prompts); {len(heldout)} prompts stay held out.")
    print("Fixed: simple_medium=0.15, complex_reasoning=0.60 "
          "(they cannot change the chosen model).")
    print("Selection: fewest under-routed, then fewest over-routed, "
          "middle of any tied plateau.\n")
    print(f"  {'medium_complex':15} {'kw':4} {'acc':7} {'under':>6} {'over':>5}")

    rows = []
    arms = [("no", None)]
    if keywords:
        arms.append(("yes", {"custom_technical_keywords": keywords}))
    for label, extra in arms:
        for step in range(2, 8):          # 0.10 .. 0.35 in 0.05 steps
            boundary = round(0.10 + 0.05 * step, 2)
            bounds = dict(DEFAULT_BOUNDARIES)
            bounds["medium_complex"] = boundary
            results = run_set(base_url, api_key, tuning, bounds, extra)
            s = score(results)
            rows.append({"medium_complex": boundary, "kw": label, "s": s,
                         "bounds": bounds, "extra": extra})
            print(f"  {boundary:<15} {label:4} {s['accuracy']:7} "
                  f"{s['under_routed']:>6} {s['over_routed']:>5}")

    rows.sort(key=lambda r: (r["s"]["under_routed"], r["s"]["over_routed"]))
    best_key = (rows[0]["s"]["under_routed"], rows[0]["s"]["over_routed"])
    plateau = [r for r in rows
               if (r["s"]["under_routed"], r["s"]["over_routed"]) == best_key]
    plateau.sort(key=lambda r: r["medium_complex"])
    chosen = plateau[len(plateau) // 2]      # middle of the plateau
    print(f"\nBest score on tuning: under={best_key[0]}, over={best_key[1]}")
    print(f"  tied configs ({len(plateau)}): "
          f"{[(r['medium_complex'], r['kw']) for r in plateau]}")
    print(f"  chosen (middle of plateau): medium_complex={chosen['medium_complex']}, "
          f"keywords={chosen['kw']}")

    print("\nHELD-OUT confirmation (single use, no further adjustment):")
    held = run_set(base_url, api_key, heldout, chosen["bounds"], chosen["extra"])
    h = score(held)
    print(f"  under-routed: {format_interval(h['under_routed'], h['n'])}   "
          f"over-routed: {format_interval(h['over_routed'], h['n'])}   "
          f"correct: {h['accuracy']}")

    print("\nShipped defaults on the same held-out half, for comparison:")
    base = score(run_set(base_url, api_key, heldout, DEFAULT_BOUNDARIES))
    print(f"  under-routed: {format_interval(base['under_routed'], base['n'])}   "
          f"over-routed: {format_interval(base['over_routed'], base['n'])}   "
          f"correct: {base['accuracy']}")

    print("\nUnder-routed examples from held-out (quality risk):")
    shown = 0
    for r in held:
        if TIERS[r["tier_expected"]] == STRONG and r["model_got"] != STRONG:
            print(f"  score={r['score']!s:>6} {r['tier_got']:10} {r['text'][:70]}")
            shown += 1
    if not shown:
        print("  none")
    return chosen, h, base


def main():
    base_url = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("Set LITELLM_BASE_URL and LITELLM_API_KEY first.")

    preflight(base_url, api_key)
    if "--dump-scores" in sys.argv:
        out = os.path.join(ROOT, "scores.csv")
        dump_scores(base_url, api_key, out)
        return
    if "--sweep" in sys.argv:
        sweep(base_url, api_key)
    else:
        prompts = load_prompts()
        extra = ({"custom_technical_keywords":
                  surviving_keywords(prompts["tuning"], prompts["held_out"])}
                 if "--keywords" in sys.argv else None)
        if "--keywords" in sys.argv and extra and not extra["custom_technical_keywords"]:
            raise SystemExit("No keyword survives the leakage guard; run without --keywords.")
        evaluate(base_url, api_key, extra, DEFAULT_BOUNDARIES,
                 detail="--detail" in sys.argv)


if __name__ == "__main__":
    main()
