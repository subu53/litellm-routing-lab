"""Prove the retry wrapper behaves correctly, without any network.

run_case is monkeypatched to raise chosen HTTP errors, so this tests the retry
policy exactly: which codes retry, which fail fast, and that backoff happens.
"""
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import benchmark

CALLS = []


def make_raiser(code, succeed_on=None):
    def fake(base_url, api_key, requested_model, case_name, expected_tier, prompt):
        CALLS.append(code)
        if succeed_on is not None and len(CALLS) >= succeed_on:
            return {"case": case_name, "expected_tier": expected_tier,
                    "requested_model": requested_model, "returned_model": "fake-model",
                    "latency_ms": 1.0, "classifier_cost_header": ""}
        raise urllib.error.HTTPError("url", code, "err", {}, None)
    return fake


def run(label, code, succeed_on, attempts=3):
    global CALLS
    CALLS = []
    original = benchmark.run_case
    original_sleep = time.sleep
    slept = []
    time.sleep = lambda s: slept.append(s)
    benchmark.run_case = make_raiser(code, succeed_on)
    try:
        try:
            row = benchmark.run_case_with_retry(
                "http://x", "k", "m", "case", "SIMPLE", "p", attempts=attempts)
            outcome = f"returned {row['returned_model']}"
        except urllib.error.HTTPError as error:
            outcome = f"raised {error.code}"
    finally:
        benchmark.run_case = original
        time.sleep = original_sleep
    print(f"{label:38} calls={len(CALLS)} sleeps={slept} -> {outcome}")


print("retry policy")
print("============")
run("503 always fails (3 attempts)", 503, None)
run("503 then succeeds", 503, 2)
run("429 then succeeds", 429, 2)
run("400 fails fast (no retry)", 400, None)
run("401 fails fast (no retry)", 401, None)
run("500 retried", 500, None)
run("unlisted code (999) not retried", 999, None)

# A connection-level failure is not an HTTPError, so it must escape untouched.
original = benchmark.run_case


def raise_urlerror(*args, **kwargs):
    raise urllib.error.URLError("connection refused")


benchmark.run_case = raise_urlerror
try:
    benchmark.run_case_with_retry("http://x", "k", "m", "case", "SIMPLE", "p")
    print("URLError: WRONGLY SWALLOWED")
except urllib.error.URLError:
    print(f"{'URLError propagates (not retried)':38} -> OK")
finally:
    benchmark.run_case = original
