# LiteLLM Routing Lab

A cost-aware AI gateway that routes each request to a cheaper or stronger model by
predicting how much reasoning it needs — and a measured evaluation of whether that
prediction actually works.

**The evaluation found that it does not.** LiteLLM's free built-in classifier behaves as a
prompt-length gate on this workload, not a complexity classifier. That finding, together
with the controlled test that produced it, is the substance of this repository.

## Status

| Component | State |
|---|---|
| Gateway running locally against free-tier Gemini | **Built and run** |
| Router evaluation, 60 labelled prompts with a held-out split, plus a 40-prompt 2x2 confound test | **Run; results below** |
| Benchmark harness, retry logic, offline checks | **Run** |
| GCP deployment (`main.tf`: Cloud Run, Cloud SQL, Vertex AI) | **Written, never applied** |
| Paid Jev classifier path | **Configured, never run — no TypeSafe key** |

Nothing here has been deployed to Google Cloud, and `main.tf` has never passed
`terraform validate`. The sections below label what is measured, what is only configured,
and what is untested.

## Author

**Sammy S Mutuku**

- GitHub: [@subu53](https://github.com/subu53)
- LinkedIn: [linkedin.com/in/your-profile](https://www.linkedin.com/in/your-profile)
- Kaggle: [sammyssbu](https://www.kaggle.com/sammyssbu)

## The problem

A company ships an AI feature and sends every request to the strongest model. A one-word
lookup ("reply with the word blue") and a design tradeoff ("reason about when to choose
queues over synchronous APIs") cost exactly the same. As traffic grows, so does the waste.

The obvious fix is to classify each request and route cheap work to a cheap model. This
project builds that gateway, then tests whether the classification can be trusted.

## Architecture

Three config files ship; two represent separate deployments. The one that was **evaluated** is
the local free path. The one that is **written but unapplied** is the GCP path.

```
  EVALUATED PATH (local, no billing account)
  ---------------------------------------------------------------

    benchmark.py                 tools/evaluate_router.py
    tools/test_length_confound.py
           |                              |
           v                              v
    +-----------------------------------------------------+
    |  LiteLLM proxy 1.104.0  on localhost:4000           |
    |  config.gemini.yaml  --  model_list aliases:        |
    |    gemini-flash-lite -> Gemini 3.1 Flash-Lite       |
    |    gemini-flash      -> Gemini 3.5 Flash            |
    |    jev-router        -> auto_router/                |
    |                         complexity_router           |
    +-----------------------------------------------------+
             |                              |
             | alias call                   | router call
             v                              v
    +----------------------+      +------------------------+
    | Google AI Studio     |      | built-in heuristic     |
    | Gemini API           |      | scorer, local and free, |
    | (free tier, key in   |      | no network call: scores |
    |  the environment)    |      | 7 weighted dimensions   |
    +----------------------+      | then maps to a tier     |
                                  +------------------------+
                                               |
                               tier -> alias -> model
                               below threshold  -> cheap model
                               at or above      -> strong model
```

```
  UNTESTED PATH (GCP, written but never applied)
  ---------------------------------------------------------------

    benchmark.py --> Cloud Run (public, TLS) --> LiteLLM proxy
                                                     |
                               +---------------------+------------------+
                               |                                        |
                        jev-router                               gemini aliases
                               |                                        |
                        TypeSafe Jev                            Vertex AI Gemini
                        (needs API key)                         (gemini-3.5-flash)
                               |
                     Cloud SQL PostgreSQL 15, mounted at /cloudsql:
                     virtual keys, router config, request logs, spend records
```

**Why two paths.** The GCP path needs a billing account with a card on file, which was not
available during this work. The local path needs only a free Google AI Studio key, so it
was the path that could actually be exercised. Every measured result below comes from the
local path; the GCP path is infrastructure as code that has never been applied.

**The alias `jev-router` is a legacy label.** In the evaluated config the classifier behind
it is `classifier_type: heuristic`, not Jev. The alias is kept because the router tiers and
`benchmark.py` reference it, and renaming would churn three configs without changing
behaviour.

## What makes it interesting

**The routing decision is inspectable.** `return_raw_model_name: true` means a routed
response reports the concrete model that actually served it, not the alias `jev-router`.
That is what made the evaluation possible: every call records which model answered, so the
router's behaviour could be measured instead of inferred.

**The evaluation is adversarial to its own conclusion.** Scores were collected once and
every threshold midpoint was then evaluated offline, a length baseline was computed as a
direct competitor to the classifier, and a 2x2 test was designed to break the finding with
pre-registered criteria. The classifier did not survive it. That is the result.

**The harness is single-classifier as shipped.** Both measuring tools hardcode
`classifier_type: heuristic` in the request body, and `test_length_confound.py` also hardcodes
the threshold, so they measure the built-in heuristic whatever `config.yaml` says. Producing
the same four-cell table for a paid classifier would require removing that override and
sending matching boundaries. That has not been done and is untested.

**Classifier failure degrades safely** *(configured, untested)*. `classifier_fallback:
default_model` sends an undecidable request to the strong model, which fails toward quality
rather than toward cheap. `circuit_breaker_enabled` with a cooldown applies to the LLM and
Jev classifiers; the heuristic scorer runs locally and has no breaker to trip.

**Secrets never enter the image** *(configured, untested)*. On the GCP path the master key,
salt key, and database URL are generated by Terraform, written to Secret Manager, and
injected at runtime, so no credential exists in a file or an image layer.

**Spend controls are documented but not demonstrated.** The paid runbook describes a `$1`
virtual-key budget expected to return HTTP 422 when exhausted. Neither free config sets a
budget, and the `422` behaviour has never been observed against a running key. It is
recorded as a runbook step, not a verified guarantee.

## Two ways to run this

| | Paid path (never run here) | Free path (what was evaluated) |
|---|---|---|
| Guide | [`RUNBOOK.md`](RUNBOOK.md) | [`LOCAL-RUNBOOK.md`](LOCAL-RUNBOOK.md) |
| Models | Vertex AI Gemini (needs GCP billing) | Google AI Studio free tier, no card |
| Classifier | Jev via TypeSafe (paid, needs a key) | built-in heuristic scorer (free, local) |
| State | Cloud SQL Postgres | in-process |
| Deployment | Cloud Run via Terraform | local process on :4000 |
| Cost | ~$5, never incurred | **$0** |
| State of evidence | written, unapplied | **measured results below** |

Both share `benchmark.py` and the same router semantics, so the measurement method
transfers. The free path never adds a payment method. `config.free.yaml` is a third
variant using Groq's free tier; it was written before the Gemini key was available and is
kept for anyone whose Groq catalogue differs.

## Repository layout

| File | Purpose |
|---|---|
| `main.tf` | Cloud Run, Cloud SQL, Artifact Registry, IAM, Secret Manager |
| `variables.tf` | `project_id`, `region`, `vertex_location` |
| `versions.tf` | Pins Terraform 1.16.5, Google provider 8.5.0, Random 3.9.1 |
| `outputs.tf` | `service_url`, `admin_url`, `image_uri` |
| `config.yaml` | Paid path: Gemini aliases + Jev complexity router |
| `config.free.yaml` | Free path: Groq gpt-oss aliases + heuristic complexity router |
| `config.gemini.yaml` | Free path: Google AI Studio Gemini aliases + heuristic router |
| `Dockerfile` | Pins LiteLLM 1.104.0 and bakes in `config.yaml` |
| `benchmark.py` | Four-case baseline vs. routed benchmark, writes CSV evidence |
| `terraform.tfvars.example` | Copy to `terraform.tfvars` and set your project ID |
| `RUNBOOK.md` | Paid deploy in Google Cloud Shell, including cleanup |
| `LOCAL-RUNBOOK.md` | Zero-cost local run in Cloud Shell, no card |
| `tools/check_structure.py` | Offline structural checks (HCL balance, YAML shape, references) |
| `tools/test_checker.py` | Negative tests proving the checker catches real faults |

## Checking it offline

No Terraform, gcloud, Docker, or API key is required for these:

```bash
python tools/check_structure.py      # structural checks across all configs
python tools/test_checker.py         # 15 negative tests: proves the checker bites
python tools/test_retry.py           # 8 retry-policy tests
python tools/test_compare_length.py  # 10 verdict-direction tests
python tools/check_keywords.py       # keyword leakage guard
```

`check_structure.py` verifies balanced HCL delimiters, unterminated strings, project-wide
resource reference resolution, YAML indentation, that all four router tiers point at
declared aliases, that exactly one valid classifier is configured, that a non-Jev config
carries no stale Jev settings, and that `benchmark.py` still exposes `run_case`/`main`.

`test_compare_length.py` exists because the length-versus-score verdict once reported a win
for the classifier at 0.725 against 1.000. Those tests pin the direction of every comparison.

Evaluating the router needs a running proxy and a key, but spends no completion requests:

```bash
python tools/evaluate_router.py --dump-scores   # scores all 60 prompts once
python tools/sweep_thresholds.py scores.csv     # every threshold candidate, offline
python tools/compare_length.py scores.csv       # score AUC vs word-count AUC
python tools/test_length_confound.py            # pre-registered 2x2 test
python tools/test_routing.py --keywords         # four benchmark cases, both configs
```

**It is not `terraform validate`** — provider schema errors can still slip through. It
catches the failure class that matters for hand-assembled config: the typo that costs you
twenty minutes in Cloud Shell.

## Deploy

Full step-by-step instructions are in [`RUNBOOK.md`](RUNBOOK.md) (paid) or
[`LOCAL-RUNBOOK.md`](LOCAL-RUNBOOK.md) (free). Paid short version, run from Google
Cloud Shell:

```bash
cp terraform.tfvars.example terraform.tfvars   # then set your project_id
terraform init && terraform validate
terraform apply -target=google_artifact_registry_repository.images
docker build -t "$IMAGE_URI" . && docker push "$IMAGE_URI"
terraform apply                                 # ~15 min, Cloud SQL dominates
```

Then open `terraform output -raw admin_url`.

## Benchmark

The same four prompts run twice — once pinned to `gemini-flash`, once through
`jev-router` — so the comparison isolates routing as the only variable.

Paid path:

```bash
export LITELLM_BASE_URL="$(terraform output -raw service_url)"
export LITELLM_API_KEY="$(gcloud secrets versions access latest --secret=litellm-api-key)"
python3 benchmark.py baseline   # writes benchmark-baseline.csv
python3 benchmark.py routed     # writes benchmark-routed.csv
```

Free path (`LOCAL-RUNBOOK.md` steps 3-5):

```bash
export LITELLM_BASE_URL="http://localhost:4000"
export LITELLM_API_KEY="$LITELLM_MASTER_KEY"
python3 benchmark.py baseline
python3 benchmark.py routed
```

Each row records `case`, `expected_tier`, `requested_model`, `returned_model`,
`latency_ms`, and `classifier_cost_header`. On the free path
`classifier_cost_header` is always empty — the heuristic scorer is local and free,
which is itself a headline result.

Transient `429`/`503` responses are retried with exponential backoff. A retried case
reports the successful attempt's latency, not the total including backoff. If a case
still fails, the completed rows are kept, the CSV is flagged incomplete, and the run
does not claim to be a valid comparison.

### Measured results (Gemini free path)

Four prompts, one run each, on a free-tier key. `config.gemini.yaml`, so the baseline
tier is `gemini-3.5-flash` and the cheap tier is `gemini-3.1-flash-lite`.

| Case | Expected tier | Baseline model | Baseline ms | Routed model | Routed ms |
|---|---|---|---|---|---|
| simple | SIMPLE | gemini-3.5-flash | 1,525 | gemini-3.1-flash-lite | 7,573 |
| short_explanation | MEDIUM | gemini-3.5-flash | 2,551 | gemini-3.1-flash-lite | 1,632 |
| architecture | COMPLEX | gemini-3.5-flash | 7,101 | gemini-3.1-flash-lite | 2,065 |
| tradeoff | REASONING | gemini-3.5-flash | 15,392 | gemini-3.1-flash-lite | 6,582 |

Average latency 6,642 ms baseline vs 4,463 ms routed. Classifier cost empty on every
routed row.

Note: the baseline `returned_model` column reports the alias (`gemini-flash`), not the
resolved provider ID, because `return_raw_model_name` only affects the router path.

### Router evaluation on 60 labelled prompts

`python3 tools/evaluate_router.py --sweep` scores the **model** the router chooses and
separates the two failure directions: under-routing puts hard work on the cheap model and
risks quality, over-routing puts easy work on the strong model and wastes money.

Boundaries 0.15 and 0.60 were held fixed — they cannot change the chosen model — and only
`medium_complex` was swept, on the 30 tuning prompts:

| medium_complex | correct | under-routed | over-routed |
|---|---|---|---|
| 0.20 | 17/30 | 13 | 0 |
| 0.25 | 17/30 | 13 | 0 |
| 0.30 | 17/30 | 13 | 0 |
| 0.35 (shipped) | 15/30 | 15 | 0 |
| 0.40 | 15/30 | 15 | 0 |
| 0.45 | 15/30 | 15 | 0 |

The plateau 0.20-0.30 is a real improvement on tuning (13 vs 15 under-routed). Following a
middle-of-plateau rule, **0.25** was chosen and confirmed once on the 30 held-out prompts:

| Held-out (30 prompts) | under-routed | over-routed | correct |
|---|---|---|---|
| chosen 0.25 | 16/30 (95% CI 36-70%) | 0/30 (95% CI 0-11%) | 14/30 |
| shipped 0.35 | 16/30 (95% CI 36-70%) | 0/30 (95% CI 0-11%) | 14/30 |

**The tuning gain did not transfer.** On held-out the two configurations are identical.

**Under the shipped defaults the router sent zero prompts to Flash.** The measured maximum
score in the set is 0.325, below the `medium_complex` boundary of 0.35, so on this evaluation
set the shipped router behaved as **always Flash-Lite**. There was no routing decision being
made, and "0/30 over-routed" is arithmetic rather than a quality result.

**The grid itself had a gap.** The first sweep tested `medium_complex` only from 0.20 upward,
so the clusters where nearly every misroute sits (0.0 and 0.175) were below its range.
`simple_medium` and `medium_complex` both map to the cheap model, so moving them together
changes no model choice; the earlier sweep wrongly treated the label-ordering constraint as a
floor on the usable routing threshold. `tools/sweep_thresholds.py` fixes this by scoring each
prompt once and evaluating every midpoint between observed scores offline.

### Offline threshold sweep: no threshold separates the two classes

Scores were collected once for all 60 prompts and every midpoint between observed scores was
evaluated offline. There are only ten such candidates, because the set produces roughly eight
distinct scores:

```
-0.150  -0.125  -0.075  -0.025  0.025  0.100  0.163  0.188  0.210  0.335
```

Held-out results at the useful thresholds:

| Threshold | Hard prompts sent to Flash | Easy prompts over-routed | Sum |
|---|---|---|---|
| -0.160 | 16/16 | 14/14 | 14 |
| -0.075 | 16/16 | 7/14 | 7 |
| 0.025 | 5/16 | 4/14 | 15 |
| 0.163 | 5/16 | 1/14 | 12 |
| 0.210 | 0/16 | 0/14 | 16 |

**No threshold works.** Rescuing the hard prompts requires a threshold at or below -0.075,
which sends 23 of 30 held-out prompts to Flash — that is near-default-strong pricing with
extra steps, not routing. Every threshold that keeps the strong model rare leaves the plain
language hard prompts on the cheap model.

**Why: the two classes are not separated.** Over all 60 prompts, hard scores range from
-0.150 to 0.325 and easy scores from -0.150 to at least 0.163 — one easy prompt clears 0.163,
since the held-out table shows 1/14 over-routed at that threshold. The ranges overlap almost
completely, so a single cut on this score cannot separate them. The score also takes only
about eight discrete values across 60 prompts, which indicates a few surface-form detectors
firing rather than a graded measurement of difficulty.

**The 60-prompt set confounds hardness with length.** Its hard prompts are
multi-sentence scenarios and its easy prompts are one-liners, so a length gate would score
well without measuring difficulty. A plain word count separates those labels essentially
perfectly:

| Split | AUC by classifier score | AUC by word count |
|---|---|---|
| tuning | 0.929 | **1.000** |
| held-out | 0.725 | **1.000** |
| pooled | 0.836 | **0.999** |

Word count ranks the two classes almost perfectly on every split and beats the classifier's
own score pooled (0.999 vs 0.836).

**The 2x2 test controls for it directly.** 40 fresh prompts, 10 per cell, with length and
difficulty crossed on purpose (`eval/prompts_2x2.yaml`, run by
`tools/test_length_confound.py`). The prediction and pass criteria were written into the
prompt file before the run and are printed alongside the result.

| Cell | Median words | Routed as difficulty requires |
|---|---|---|
| short + hard | 7 | **0/10** |
| long + hard | 53 | 10/10 |
| short + easy | 5 | 8/10 |
| long + easy | 54 | **0/10** |

**Verdict: SUPPORTED.** Every short-but-hard request went to the cheap model and every
long-but-easy request went to the strong model.

**These results are not independent, and the 2x2 carries the claim.** The defaults result,
the AUC comparison and the 2x2 all use one scorer, one author's prompts and one LiteLLM
version. Only the 2x2 was built to separate length from difficulty, so it is the evidence;
the other two are consistent with it, not corroboration of it. What is shown is that **the
free heuristic behaved as a length gate on this prompt set** — not that it is a length gate
in general.

**The short cells are the decisive evidence**, because they keep length far below the long
cells — 2-11 words with medians 7 and 5, against 42-63 words — while difficulty varies. All
ten short-hard prompts scored **-0.1**. Eight of the ten short-easy prompts scored -0.1 or
-0.15 and two scored 0.05. With length held far below the hard-prompts' range, the scorer
still puts a terse hard question ("Why do distributed locks fail under clock skew?", -0.1)
**below** an easy one ("Spell Kubernetes", 0.05). It is not detecting difficulty.

The two over-routed short-easy prompts show the same mechanism from the other side:
"What is a race condition?" and "Spell Kubernetes" both scored **0.05** and reached the strong
model, because both contain technical vocabulary. The keyword dimension fires on the wrong
signal.

At the interim threshold anything longer than roughly one line crossed to the strong model,
which is why the 60-prompt set reported 0/31 under-routed. **That number measured length, not
difficulty.**

**What fires besides length: signals per cell.** `test_length_confound.py` prints the
classifier's `signals` list for every prompt and a per-cell tally. The observations recorded
from that run are the only evidence in this project about non-length behaviour, so they are
reported as observations, not as a model of the scorer.

**An LLM classifier baseline is available and deliberately not called "the learned
classifier".** `tools/llm_baseline.py` asks a general-purpose model (Flash-Lite by default)
for a single word, `easy` or `hard`, on the same 40 prompts, and reports the same four cells
plus classification latency and token cost. It is not Jev and its output does not describe
Jev's behaviour. Its purpose is narrower: with the paid path still blocked, it is the only
available check on whether *any* classifier beats prompt length on these cells. Run it with
`GEMINI_API_KEY` set and no proxy required:

```bash
export GEMINI_API_KEY=...
python3 tools/llm_baseline.py                    # Flash-Lite, 40 short calls
python3 tools/llm_baseline.py --model gemini-3-flash
```

**Conclusion.** LiteLLM's built-in heuristic is a surface-form detector, not a complexity
classifier, on this workload. Its signals are prompt length, the presence of a "step by step"
style phrase, and a small technical vocabulary; a hard request written conversationally scores
0.0, the bottom of its scoring range.

**The interim configuration is a length gate, not complexity routing.** At the selected
threshold the rule is effectively "anything longer than one line goes to the strong model".
That is safe in one direction only — a long prompt never reaches the cheap model — while
short hard prompts reach it every time. Under the shipped defaults the opposite holds:
nothing reaches Flash at all, so the scorer performs no better than "always cheap".

**This says nothing about a learned classifier.** Every result above concerns the free
built-in heuristic. Whether a trained classifier separates the four 2x2 cells is untested
here, and that comparison is the experiment this project exists to run. **The harness is not
classifier-agnostic as shipped:** both tools hardcode `classifier_type: heuristic` in the
request body, and `test_length_confound.py` hardcodes the threshold, so they measure the
built-in heuristic regardless of `config.yaml`. Pointing them at the paid Jev path would
require removing that override and sending matching boundaries; that has not been attempted,
and the paid endpoint has not been confirmed to expose the same `POST /auto_router/test_routing`
route.

**Savings were not measured.** The Flash-Lite share at this threshold is the short prompts,
whose input token counts are already small, so the cost benefit is likely thin. Output token
counts were never recorded, so no money figure is claimed.

### Why the boundary cannot help

Every under-routed held-out prompt scored **0.0, -0.05, or 0.175**. The `COMPLEX` tier
begins at 0.35, so **no boundary value between 0.10 and 0.45 can lift a 0.0 score into the
strong tier**. The scores, not the thresholds, are the limiting factor, and on this held-out
sample no prompt falls in the 0.25-0.35 window where the two configurations would differ.

The prompts that scored **0.0** are the ones written in *plain language*:

| Under-routed held-out prompt | Score |
|---|---|
| "We keep hiring and still fall behind. Reason about why adding people is not fixing the problem and what would." | 0.0 |
| "Our deploys work but rollbacks do not. Propose a plan to make rollback routine..." | 0.0 |
| "A team is rewarded for shipping features and penalised for outages. Reason about the behaviour this creates..." | 0.0 |
| "Two teams disagree on where a shared component should live. Outline how you would resolve it technically..." | 0.0 |

These are hard requests that use no technical vocabulary, so the `technicalTerms` dimension
(weight 0.25) never fires and the score collapses to zero — the same score as an empty
prompt. The prompts that did reach 0.175 contain a reasoning marker such as "reason step by
step".

**This is the central finding of the project.** A keyword-and-score heuristic measures
whether a prompt *sounds* technical, not whether it *is* hard. Six of the held-out prompts
were written deliberately without jargon, and the router under-routed all of them. That is
the concrete case for a learned classifier — which is what the paid Jev path in
`config.yaml` exists to provide, and it is why this project now has a measured reason to
use one rather than an assumed one.

**Cost consequence, stated plainly.** Under-routing is not free savings: 16 of 30 non-trivial
held-out requests were answered by the cheaper model. The project's own latency numbers show
why that matters — the baseline spread between easy and hard prompts on the same model was
10x (1,525 ms vs 15,392 ms). Halving cost by sending hard work to the small model may simply
be buying a lower quality answer.

**What this shows.** Without routing, the same model served a one-word prompt and a
reasoning prompt whose measured latency differed by **10×** (1,525 ms vs 15,392 ms).
That spread is the waste this project targets, and it is measured rather than assumed.

**What this does not show.** The heuristic scorer sent **all four prompts to the cheap
tier**, so `gemini-3.5-flash` was never selected and has no routed-path latency here.
The 33% average latency drop is therefore "a smaller model was used for everything",
not evidence of correct complexity routing.

**Why it happened — measured, not guessed.** `POST /auto_router/test_routing` reports the
classifier's actual decision without spending a completion. Two configurations were
measured: the shipped defaults, and defaults plus domain vocabulary appended via
`custom_technical_keywords`.

| Case | Score (defaults) | Score (+keywords) | Signals after keywords | Model (defaults) | Model (+keywords) |
|---|---|---|---|---|---|
| simple | -0.1 | -0.1 | short (7 tokens) | flash-lite | flash-lite |
| short_explanation | -0.1 | -0.1 | short (13 tokens) | flash-lite | flash-lite |
| architecture | 0.0 | **0.25** | technical (event-driven, idempotency, observability) | flash-lite | flash-lite |
| tradeoff | 0.175 | **0.425** | reasoning + technical (latency, ordering, failure isolation) | flash-lite | **gemini-3.5-flash** |

Model-choice accuracy on these four prompts went **2/4 → 3/4** with the keywords. **That
number is retracted.** Retested against the 60-prompt set with a leakage guard, ten of
those twelve keywords had support in exactly one tuning prompt and two had none, so none survived
(`tools/check_keywords.py`), so the gain did not generalise — it was fitted to the prompts
it was measured on. The table is kept as the record of a hypothesis that failed a proper
test, not as a result. The evaluated numbers are in the 60-prompt section below.

**The cause was vocabulary, not boundaries.** `architecture` originally scored **0.0 with
no signals at all**: the prompt names "event-driven," "idempotency," "retries," and
"observability," and the built-in technical list matched none of them. The
`technicalTerms` dimension carries weight 0.25 and never fired, so the score collapsed to
zero and was indistinguishable from a greeting. No boundary value repairs a 0.0 score.
Boundary tuning was measured and gave identical results (1/4 on tier labels either way),
so an earlier revision of this README that blamed the boundaries was wrong.

**Two metrics, and only one of them matters.** This project originally scored routing by
matching the classifier's tier against a hand-written "expected tier" per prompt, which
reported 1/4 for both configurations — while the model choices had in fact improved. The
tier labels are opinions, and one of them is wrong: "Explain in two sentences why HTTPS is
safer than HTTP" is labelled MEDIUM but is a plainly simple request, so routing it to the
cheap model is correct and the label is not. Model-choice accuracy is the outcome that
affects cost and latency, so `tools/test_routing.py` now reports both and the conclusion
uses model choice.

### Evaluation protocol and its limits

`eval/prompts.yaml` holds 60 labelled prompts, split 30 tuning / 30 held out. Only the
tuning half may be used to choose keywords and boundaries; the held-out half was read once.

**The held-out half has been spent.** An interim operating point of `simple_medium = -0.075`
was briefly set on the *pooled* 60 scores, which includes the held-out half, and then
reverted. So even that short-lived setting was fitted rather than validated, and any further
tuning needs a fresh prompt set. The shipped configuration is back at the documented
defaults, which is the configuration the tables below describe.

**Only one boundary can change a routing decision.** `SIMPLE` and `MEDIUM` map to the same
model, as do `COMPLEX` and `REASONING`, so `simple_medium` and `complex_reasoning` move the
tier *label* without changing the chosen model. The sweep holds both fixed at 0.15 and 0.60
and varies only `medium_complex`. Do not read the fixed values as tuned.

**Keyword guard.** A candidate keyword is accepted only if it appears in at least 3 tuning
prompts and in no held-out prompt (`tools/check_keywords.py`). Of the 12 candidates tried
earlier, **ten had support in exactly one tuning prompt and two had none**, because the list
was derived from the benchmark prompts themselves. All 12 fail the guard, so
`config.gemini.yaml` configures none of them. The previously reported 3/4 that depended on
those keywords was training on the test set and is not a result.

**Selection rule is a policy, not a measurement.** Configurations are ranked by fewest
under-routed prompts (quality risk) and then fewest over-routed (wasted spend). Among ties
the middle of the plateau is chosen rather than the first hit, so the selected boundary has
equal-scoring neighbours on both sides.

**Counts are reported with 95% Wilson intervals.** On 30 held-out prompts one prompt is
about 3 points, so an interval is more honest than a single percentage.

**Known limits.** One author wrote every prompt, so they share a style real traffic will
not. Each 2x2 cell holds 10 prompts, so a 10/10 result carries a 95% Wilson interval of
72-100% — strong but not precise. All results concern the **free built-in heuristic only**;
whether a learned classifier separates the cells is untested. Boundary values are an interim
choice, not a validated operating point. Savings in money are unmeasured.

**These three results are not independent.** The defaults finding, the AUC comparison and the
2x2 all use one scorer, one author's prompts and one LiteLLM version. Only the 2x2 was built
to separate length from difficulty, so the claim rests on it; the others are consistent with
it rather than corroboration. The accurate statement is that **the free heuristic behaved as a
length gate on this prompt set**, not that it is a length gate in general.

### Evidence archive

**No evidence archive is committed in this revision.** The numbers above were produced by
the scripts in `tools/` against the config in this repository, but the raw output
(`scores.csv`, the sweep tables, the 2x2 console output) was not saved. `eval/archive_results.sh`
exists so that a run can be archived in one command, and a reader can regenerate every number
here — but until it is run and committed, the tables below are not independently re-checkable
from this checkout. That is a real gap, not a formality.
`eval/archive_results.sh` writes one timestamped directory containing:

| File | Shows |
|---|---|
| `environment.txt` | commit, config hash, `pip show litellm`, Python version |
| `config.gemini.yaml` | the exact config under test |
| `scores.csv` | one classifier score per prompt |
| `sweep-thresholds.txt` | under- and over-routing at every threshold candidate |
| `compare-length.txt` | score AUC against word-count AUC |
| `2x2-confounds.txt` | the pre-registered four-cell test, with signals |

Root-level `scores.csv` and `llm_baseline.csv` are gitignored because they are generated;
anything under `eval/results/` is committed on purpose. To reproduce, start the proxy with
`config.gemini.yaml` and run `bash eval/archive_results.sh` from the repository root.

**The tier map then decides the rest.** `MEDIUM` maps to the cheap tier in this project's
design, so a mid-tier score of 0.175 is served by Flash-Lite. Remapping `MEDIUM` to the
strong model would send this prompt to Flash, but it would also send every MEDIUM prompt
there, which defeats the cost objective the project exists to measure. That tradeoff is
recorded rather than applied.

An earlier revision of this project also shipped `tools/explain_scoring.py`, a replica of
the scorer built from the documentation. It predicted 0.800 for the same prompt the real
router scored at 0.175, because the shipped keyword lists and dimension scoring are not
published. It has been replaced by `tools/test_routing.py`, which queries the real
classifier.

**Two measurement caveats.** The `simple` routed case at 7,573 ms is 5× its baseline and
consistent with provider cold start rather than routing overhead, so case 1 of a run is
unreliable. Rows are single samples, not averages.

**Honest conclusion.** The routing mechanism works — aliases resolve, the tier model
serves the request, and `return_raw_model_name` exposes the decision. The *quality of the
classification* does not: a keyword-and-score heuristic rated a step-by-step tradeoff
analysis as cheap-tier. This is the gap a learned classifier is meant to close, and the
reason classifying with a decision model such as Jev is a paid product. A defended
limitation is a more useful portfolio result than an unearned win.

## Cost

| Path | Cost | Notes |
|---|---|---|
| Free (`config.gemini.yaml`) | **$0** | Google AI Studio free tier, real Gemini models, no card |
| Free (`config.free.yaml`) | **$0** | Groq free tier, no card, no GCP resources |
| Paid (`config.yaml`) | **~$5** | under $5 if destroyed within two hours |

Google AI Studio free-tier limits per model (requests/day): Gemini 3 Flash 20,
Gemini 3.1 Flash-Lite 500, Gemini 2.5 Flash 20. The router consumes 4 requests per
benchmark, so either free path is comfortably inside quota.

On the paid path, Cloud SQL compute and storage are the only meaningful standing
charges. Note that the `$1` LiteLLM virtual-key budget caps gateway traffic only — it
does **not** cap Google Cloud billing.

To tear down the paid path:

```bash
terraform destroy
gcloud secrets delete typesafe-api-key   # created outside Terraform
```

## Notes and caveats

- **`gemini-3.5-flash` is real; `gemini-3.5-flash-lite` is not.** An earlier revision of
  this README claimed both were fabricated. That was wrong. Gemini 3.5 Flash shipped at
  general availability at Google I/O 2026 under the model ID `gemini-3.5-flash`, priced at
  $1.50/million input and $9/million output. However, **no 3.5 Flash-Lite exists** — the
  Flash-Lite line is at `gemini-3.1-flash-lite`, so the source project's pairing invents a
  variant. `config.yaml` still ships the source project's `vertex_ai/gemini-3.5-flash` /
  `vertex_ai/gemini-3.5-flash-lite` pair; the second one must be replaced before deploying.
  Step 3 of the runbook checks the catalogue against your own project.
- **Model IDs differ per path, aliases never change.** The router references
  `gemini-flash`, `gemini-flash-lite`, and `jev-router`, which are stable across all three
  configs. Only the `model:` lines under `litellm_params` differ:
  - `config.yaml` (paid, Vertex AI): `vertex_ai/gemini-*`
  - `config.free.yaml` (free, Groq): `groq/openai/gpt-oss-20b` / `groq/openai/gpt-oss-120b`
  - `config.gemini.yaml` (free, Gemini API): `gemini/gemini-3.1-flash-lite` / `gemini/gemini-3.5-flash`
- **What is and is not verified.** The local free path was run end to end: the proxy started,
  requests were answered by Gemini, and roughly 4,000 classification calls were made across
  the evaluation. The GCP path was not: `main.tf` has **never** passed `terraform validate`,
  and no `terraform plan` or `apply` has been run, because the project had no billing account
  with a card attached. Treat the first `terraform validate` in Cloud Shell as the real check
  of the infrastructure code. This repository was authored on Windows, where `terraform`,
  `gcloud`, and `docker` are not installed.
- **Every measured number comes from one machine, one LiteLLM version (1.104.0) and one
  free-tier Gemini key.** `eval/archive_results.sh` records those versions alongside the
  results so a reader can tell what would need to match to reproduce them.
- `terraform.tfvars`, `*.tfstate`, `benchmark-*.csv`, root `scores.csv`, and root
  `llm_baseline.csv` are gitignored; archived evidence under `eval/results/` is committed on
  purpose. No credentials are committed.

## Built from

The [NextWork "Route AI Requests with LiteLLM"](https://nextwork.ai) project.
