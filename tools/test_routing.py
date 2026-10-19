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
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def report(base_url, api_key, label, boundaries, extra=None):
    print(f"\n{label}")
    if extra:
        print(f"  extra config: {extra}")
    print(f"  {'case':18} {'score':>7}  {'tier':10} {'routed model':22} "
          f"{'tier match':10} model")
    # Two different questions, deliberately reported separately:
    #   tier match  - does the tier equal the hand-written expected label?
    #   model right - did the prompt reach the model a human would choose?
    # The expected labels are opinions and one of them (short_explanation as
    # MEDIUM) is arguably wrong, so tier-match accuracy alone can hide real
    # improvement. Model-choice accuracy is the outcome the project cares about.
    tiers_ok = 0
    models_ok = 0
    for name, expected, prompt in CASES:
        body = classify(base_url, api_key, prompt, boundaries, extra)
        decision = body.get("routing_decision", {})
        score = decision.get("score")
        tier = decision.get("tier")
        model = body.get("routed_model") or decision.get("routed_model")
        tier_match = tier == expected
        model_ok = MODEL_OK[name] == model
        tiers_ok += tier_match
        models_ok += model_ok
        signals = ", ".join(decision.get("signals", [])) or "none"
        print(f"  {name:18} {score!s:>7}  {tier!s:10} {model!s:22} "
              f"{'yes' if tier_match else 'no':10} {'OK' if model_ok else 'wrong'}"
              f" [{signals}]")
    print(f"  tier-label matches: {tiers_ok}/{len(CASES)}"
          f"   model choices correct: {models_ok}/{len(CASES)}")
    return models_ok


# The model a human would pick for each case, independent of tier naming.
# short_explanation is deliberately Flash-Lite: it is a one-sentence factual
# question, so the cheap model is the correct destination even though the tier
# label says MEDIUM.
MODEL_OK = {
    "simple": "gemini-flash-lite",
    "short_explanation": "gemini-flash-lite",
    "architecture": "gemini-flash",
    "tradeoff": "gemini-flash",
}


# Vocabulary the shipped technical list was measured not to cover.
DOMAIN_KEYWORDS = [
    "event-driven", "idempotency", "idempotent", "observability", "pipeline",
    "retries", "ordering", "failure isolation", "synchronous", "latency",
    "trade-off", "tradeoff",
]


def main():
    base_url = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not base_url or not api_key:
        raise SystemExit("Set LITELLM_BASE_URL and LITELLM_API_KEY first.")

    print("Real classifier via POST /auto_router/test_routing")
    print("(spends no completion requests)")

    results = {}
    results["defaults"] = report(
        base_url, api_key, "A. SHIPPED DEFAULT boundaries", DEFAULT_BOUNDARIES)

    if "--tuned" in sys.argv:
        results["tuned"] = report(
            base_url, api_key, "B. TUNED boundaries (lowered medium_complex)",
            TUNED_BOUNDARIES)

    if "--keywords" in sys.argv:
        results["keywords"] = report(
            base_url, api_key, "C. DEFAULT boundaries + custom_technical_keywords",
            DEFAULT_BOUNDARIES, {"custom_technical_keywords": DOMAIN_KEYWORDS})

    print("\nsummary (model-choice accuracy):",
          "  ".join(f"{k}={v}/4" for k, v in results.items()))
    if "keywords" in results and results["keywords"] > results["defaults"]:
        print("custom_technical_keywords improved model-choice accuracy; keep them.")
    elif "keywords" in results:
        print("custom_technical_keywords did NOT improve model-choice accuracy; "
              "do not claim it did.")

    print("\nnote: 'tier-label matches' answers a different question than"
          " 'model choices correct'.")
    print("      The expected tier labels are hand-written opinions; the model"
          " choice is the")
    print("      outcome that affects cost and latency. Read model-choice accuracy"
          " as the result.")


if __name__ == "__main__":
    main()

