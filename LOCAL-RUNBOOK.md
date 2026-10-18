# Runbook — zero-cost local run in Cloud Shell

This path needs **no billing account, no card, and no Google Cloud resources**.
It runs the LiteLLM gateway as a local process in Cloud Shell, with the built-in
heuristic complexity router and Groq's free tier as the model backend.

Use this when the paid GCP path in [`RUNBOOK.md`](RUNBOOK.md) is not available.
Nothing here creates a billable resource.

## What you get, and what you don't

**You get:** a live LiteLLM gateway, real complexity routing (SIMPLE/MEDIUM to the
small model, COMPLEX/REASONING to the large one), `benchmark.py` producing real CSV
evidence, and the `$1` virtual-key budget guardrail test.

**You don't get:** Cloud Run, Cloud SQL, Terraform deployment, Secret Manager. The
`main.tf` stack is never exercised. Say so plainly if you present this — it is a
routing and cost-evidence project, not a cloud-deployment project.

## Step 1 — Get a free Groq API key

1. Open <https://console.groq.com/keys>
2. Sign in (Google or GitHub)
3. **Create API Key**, copy it

No card. Groq's free tier covers this workload many times over — the project makes
under 20 requests in total.

**Check which models your account actually has** before starting the proxy. Groq's
catalogue varies by account and changes over time, and `config.free.yaml` assumes
these two exist:

```bash
curl -s https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  | grep -o '"id":"[^"]*"' | cut -d'"' -f4 | sort
```

The config expects `openai/gpt-oss-20b` (cheap tier) and `openai/gpt-oss-120b`
(strong tier). If your catalogue differs, edit only the two `model:` lines in
`config.free.yaml` — the `model_name:` aliases must stay as they are, because the
router tiers reference them.

Store it without putting it in your shell history:

```bash
read -rsp "Groq API key: " GROQ_API_KEY; echo
export GROQ_API_KEY
```

Verify it works before going further — this catches a bad key in seconds instead of
mid-benchmark:

```bash
curl -s https://api.groq.com/openai/v1/models \
  -H "Authorization: Bearer $GROQ_API_KEY" \
  | head -c 200; echo
```

Expect JSON listing models. A `401` means the key is wrong.

## Step 2 — Install LiteLLM

Cloud Shell resets on idle, so re-run this if you come back later.

```bash
cd ~/litellm-routing-lab
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet "litellm[proxy]==1.104.0"
litellm --version
```

The `[proxy]` extra is required — the plain package has no `litellm` server command.

## Step 3 — Start the gateway

Set a master key so the proxy is not wide open. Any string works locally; it is not
a cloud credential:

```bash
export LITELLM_MASTER_KEY="sk-local-$(openssl rand -hex 8)"
echo "Master key: $LITELLM_MASTER_KEY"
```

Start the proxy in the background so your terminal stays usable:

```bash
litellm --config config.free.yaml --port 4000 > litellm.log 2>&1 &
sleep 8
tail -n 20 litellm.log
```

Look for `Uvicorn running on http://0.0.0.0:4000`. If it exited, the log shows why —
usually a YAML indentation error or a missing `GROQ_API_KEY`.

Health check:

```bash
curl -s http://localhost:4000/health/readiness; echo
```

## Step 4 — Point the benchmark at it

```bash
export LITELLM_BASE_URL="http://localhost:4000"
export LITELLM_API_KEY="$LITELLM_MASTER_KEY"
```

The master key works as a virtual key locally, which is why no Admin UI step is
needed. (In the paid runbook you create a separate `benchmark-key`; here the master
key is simpler and equivalent for benchmarking.)

## Step 5 — Baseline, then routed

```bash
python3 benchmark.py baseline
```

Expect four rows, all `returned=openai/gpt-oss-120b` (the `gemini-flash` alias).
Every prompt paid for the big model. That is the waste.

```bash
python3 benchmark.py routed
```

Expect `returned_model` to vary by complexity — simple prompts on
`openai/gpt-oss-20b`, harder ones on `openai/gpt-oss-120b`.

If every routed row still shows the big model, the heuristic scorer put everything
above the MEDIUM boundary. That is a real finding, not necessarily a bug — it means
these four prompts all look non-trivial to the scorer. Record it.

## Step 6 — Read the evidence

```bash
column -s, -t benchmark-baseline.csv
column -s, -t benchmark-routed.csv
```

Compare per case:

- which rows changed model
- routed `latency_ms` minus baseline `latency_ms`
- `classifier_cost_header` — expected **empty**, because the heuristic scorer is local
  and free. That is the headline result: **same routing decisions, zero classifier cost.**

## Step 7 — The budget guardrail

The heuristic router never calls a paid classifier, so the LiteLLM virtual-key budget
cannot be exhausted by classifier spend. To demonstrate the guardrail honestly, cap
the key low and exceed it with completions:

```bash
# create a $0.0001 budget key via the API
curl -s -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key_alias":"guardrail-test","max_budget":0.0001,"models":["gemini-flash"]}'
```

Extract the `key` from the response, then call it until it rejects:

```bash
export TEST_KEY="<paste-key>"
for i in 1 2 3; do
  curl -s -o /dev/null -w "attempt $i: HTTP %{http_code}\n" \
    -X POST http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer $TEST_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"gemini-flash","messages":[{"role":"user","content":"hello"}]}'
done
```

A `422` once the budget is exhausted proves the guardrail. Locally this costs nothing;
on the paid path it is the same mechanism.

## Step 8 — Shut down

```bash
kill %1 2>/dev/null || pkill -f "litellm --config"
deactivate
```

Nothing persists, nothing bills. Your benchmark CSVs stay in the folder and are
gitignored.

## Differences from the paid runbook

| | Paid (`RUNBOOK.md`) | Free (this file) |
|---|---|---|
| Models | Vertex AI Gemini | Groq gpt-oss 20b/120b (free tier) |
| Classifier | Jev (paid, needs TypeSafe) | heuristic scorer (free built-in) |
| State | Cloud SQL Postgres | in-process, no persistence |
| Auth | Secret Manager | shell variable |
| Deployment | Cloud Run via Terraform | local process on port 4000 |
| Cost | ~$5 | **$0** |
| Proves | Cloud deployment + routing | Routing + cost evidence |

Both use the same `benchmark.py` and the same router semantics, so the measurement
method transfers directly if you later run the paid path.
