# Import standard-library tools for HTTP calls and CSV reporting.
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

# Cover requests with different complexity levels.
CASES = [
    ("simple", "SIMPLE", "Reply with only the word blue."),
    ("short_explanation", "MEDIUM", "Explain in two sentences why HTTPS is safer than HTTP."),
    ("architecture", "COMPLEX", "Design a resilient event-driven order pipeline. Include idempotency, retries, and observability in four bullets."),
    ("tradeoff", "REASONING", "Reason step by step about when a team should choose queues over synchronous APIs, considering latency, failure isolation, ordering, and operations."),
]
MODELS = {"baseline": "gemini-flash", "routed": "jev-router"}

def run_case(base_url, api_key, requested_model, case_name, expected_tier, prompt):
    # Build one OpenAI-compatible chat request for the gateway.
    payload = json.dumps({
        "model": requested_model,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    # Measure the end-to-end request and capture routing evidence.
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8"))
        return {
            "case": case_name,
            "expected_tier": expected_tier,
            "requested_model": requested_model,
            "returned_model": body.get("model", "unknown"),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "classifier_cost_header": response.headers.get("x-litellm-classifier-cost", ""),
        }

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in MODELS:
        raise SystemExit("Usage: python3 benchmark.py baseline|routed")
    base_url = os.environ["LITELLM_BASE_URL"].rstrip("/")
    api_key = os.environ["LITELLM_API_KEY"]
    mode = sys.argv[1]
    rows = []
    # Run every case and print its routing result immediately.
    try:
        for case_name, expected_tier, prompt in CASES:
            row = run_case(base_url, api_key, MODELS[mode], case_name, expected_tier, prompt)
            rows.append(row)
            print(f"{case_name:18} expected={expected_tier:9} returned={row['returned_model']:28} latency_ms={row['latency_ms']:8} classifier_cost={row['classifier_cost_header'] or 'n/a'}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        print(f"HTTP {error.code}: {detail}", file=sys.stderr)
        raise SystemExit(1) from error
    output_path = f"benchmark-{mode}.csv"
    # Save a reusable report and print the aggregate result.
    with open(output_path, "w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(row["returned_model"] for row in rows)
    average_latency = round(sum(row["latency_ms"] for row in rows) / len(rows), 1)
    print(f"\nWrote {output_path}")
    print(f"Model counts: {dict(counts)}")
    print(f"Average latency: {average_latency} ms")
if __name__ == "__main__":
    main()
