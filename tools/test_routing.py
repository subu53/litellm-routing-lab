"""Query LiteLLM's real classifier instead of guessing at its internals.

An earlier version of this file reimplemented the heuristic scorer from the
documentation and predicted 0.800 for a prompt the real router scored at 0.175.
The shipped keyword lists and dimension scoring are not published, so a replica
is misleading. This tool asks the running proxy instead.

It calls POST /auto_router/test_routing, which classifies without spending a
completion, and reports the actual score, tier, signals, and chosen model.

Usage:
  export LITELLM_BASE_URL="http://localhost:4000"
  export LITELLM_API_KEY="$LITELLM_MASTER_KEY"
  python3 tools/test_routing.py            # shipped default boundaries
  python3 tools/test_routing.py --tuned    # compare a candidate boundary set
"""
import json
import os
import sys
import urllib.error
import urllib.request

CASES = [
    ("simple", "SIMPLE", "Reply with only the word blue."),
    ("short_explanation", "MEDIUM", "Explain in two sentences why HTTPS is safer than HTTP."),
    ("architecture", "COMPLEX", "Design a resilient event-driven order pipeline. Include idempotency, retries, and observability in four bullets."),
    ("tradeoff", "REASONING", "Reason step by step about when a team should choose queues over synchronous APIs, considering latency, failure isolation, ordering, and operations."),
]

TIERS = {"SIMPLE": "gemini-flash-lite", "MEDIUM": "gemini-flash-lite",
         "COMPLEX": "gemini-flash", "REASONING": "gemini-flash"}

DEFAULT_BOUNDARIES = {"simple_medium": 0.15, "medium_complex": 0.35, "complex_reasoning": 0.60}
TUNED_BOUNDARIES = {"simple_medium": 0.10, "medium_complex": 0.25, "complex_reasoning": 0.45}


def classify(base_url, api_key, prompt, boundaries):
    payload = json.dumps({
        "model": "jev-router",
        "messages": [{"role": "user", "content": prompt}],
        "complexity_router_config": {
            "tiers": TIERS,
            "classifier_type": "heuristic",
            "return_raw_model_name": True,
            "tier_boundaries": boundaries,
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


def report(base_url, api_key, label, boundaries):
    print(f"\n{label}")
    print(f"  boundaries: {boundaries}")
    print(f"  {'case':18} {'score':>7}  {'tier':10} {'routed model':22} result")
    agree = 0
    for name, expected, prompt in CASES:
        body = classify(base_url, api_key, prompt, boundaries)
        decision = body.get("routing_decision", {})
        score = decision.get("score")
        tier = decision.get("tier")
        model = body.get("routed_model") or decision.get("routed_model")
        ok = tier == expected
        agree += ok
        signals = ", ".join(decision.get("signals", [])) or "none"
        print(f"  {name:18} {score!s:>7}  {tier!s:10} {model!s:22} "
              f"{'OK' if ok else 'misrouted'} [{signals}]")
    print(f"  tier accuracy: {agree}/{len(CASES)}")
    return agree


def main():
    base_url = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("Set LITELLM_BASE_URL and LITELLM_API_KEY first.")

    print("Real classifier via POST /auto_router/test_routing")
    print("(spends no completion requests)")
    default_agree = report(base_url, api_key, "SHIPPED DEFAULT boundaries", DEFAULT_BOUNDARIES)
    if "--tuned" in sys.argv:
        tuned_agree = report(base_url, api_key, "TUNED boundaries (measured worse)", TUNED_BOUNDARIES)
        print(f"\ndefaults {default_agree}/4 vs tuned {tuned_agree}/4")
    else:
        print("\nRun with --tuned to compare the boundary set that measured worse.")


if __name__ == "__main__":
    main()
