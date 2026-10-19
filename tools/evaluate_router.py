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


def sweep(base_url, api_key):
    """Sweep medium_complex on the tuning half, confirm the pick on held-out.

    Selection rule: minimise under-routed first (quality), then over-routed
    (spend). That ordering is a stated policy, not a measurement.
    """
    prompts = load_prompts()
    print(f"\nSweeping medium_complex over the TUNING half "
          f"({len(prompts['tuning'])} prompts), "
          f"{len(prompts['held_out'])} held out.")
    print("Selection: fewest under-routed, then fewest over-routed.\n")
    print(f"  {'medium_complex':15} {'kw':4} {'tuning acc':11} "
          f"{'under':>6} {'over':>5}")

    rows = []
    for with_keywords in (False, True):
        extra = {"custom_technical_keywords": DOMAIN_KEYWORDS} if with_keywords else None
        for step in range(2, 8):          # 0.10 .. 0.35 in 0.05 steps
            boundary = round(0.10 + 0.05 * step, 2)
            bounds = dict(DEFAULT_BOUNDARIES)
            bounds["medium_complex"] = boundary
            results = run_set(base_url, api_key, prompts["tuning"], bounds, extra)
            s = score(results)
            rows.append({"medium_complex": boundary, "keywords": with_keywords,
                         "s": s, "bounds": bounds, "extra": extra})
            print(f"  {boundary:<15} {'yes' if with_keywords else 'no':4} "
                  f"{s['accuracy']:11} {s['under_routed']:>6} {s['over_routed']:>5}")

    rows.sort(key=lambda r: (r["s"]["under_routed"], r["s"]["over_routed"]))
    best = rows[0]
    print(f"\nBest on tuning: medium_complex={best['medium_complex']} "
          f"keywords={'yes' if best['keywords'] else 'no'} "
          f"(under={best['s']['under_routed']}, over={best['s']['over_routed']})")

    print("\nConfirming that single pick on the HELD-OUT half:")
    held = run_set(base_url, api_key, prompts["held_out"], best["bounds"],
                   best["extra"])
    h = score(held)
    print(f"  held-out: {h['accuracy']} correct  "
          f"under-routed={h['under_routed']}  over-routed={h['over_routed']}")

    print("\nFor comparison, the shipped defaults on held-out:")
    base_held = score(run_set(base_url, api_key, prompts["held_out"],
                              DEFAULT_BOUNDARIES))
    print(f"  held-out: {base_held['accuracy']} correct  "
          f"under-routed={base_held['under_routed']}  "
          f"over-routed={base_held['over_routed']}")
    return best, h, base_held


def main():
    base_url = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("Set LITELLM_BASE_URL and LITELLM_API_KEY first.")

    if "--sweep" in sys.argv:
        sweep(base_url, api_key)
    else:
        extra = ({"custom_technical_keywords": DOMAIN_KEYWORDS}
                 if "--keywords" in sys.argv else None)
        evaluate(base_url, api_key, extra, DEFAULT_BOUNDARIES,
                 detail="--detail" in sys.argv)


if __name__ == "__main__":
    main()
