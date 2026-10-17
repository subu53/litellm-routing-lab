# Runbook — deploy to Google Cloud from Cloud Shell

Every command here runs in **Google Cloud Shell**, which already has `gcloud` and `docker`.
Nothing needs installing on your own machine.

Set shell variables once per session (Cloud Shell resets on idle, so re-run this if you
come back later):

```bash
export PROJECT_ID="your-billed-project-id"
export REGION="us-central1"
gcloud config set project "$PROJECT_ID"
```

Verify you are pointed at the billed project:

```bash
gcloud config get-value project    # must print $PROJECT_ID
gcloud beta billing projects describe "$PROJECT_ID" --format='value(billingEnabled)'
```

The second command must print `True`. If it prints `False`, stop — nothing below will work
and you will hit opaque quota errors instead.

---

## Step 0 — Get this repo into Cloud Shell

Cloud Shell starts empty. Two options.

**Option A — clone (once the repo is on GitHub):**

```bash
gh auth login                      # one-time, follow the browser prompt
gh repo clone <your-user>/litellm-routing-lab
cd litellm-routing-lab
```

**Option B — no GitHub needed:** in the Cloud Shell toolbar, use **⋯ → Upload** and upload
the folder contents, or drag the files into the Cloud Shell Editor.

Confirm you are in the right place:

```bash
ls    # expect: Dockerfile  README.md  benchmark.py  config.yaml  main.tf  RUNBOOK.md ...
```

---

## Step 1 — Terraform

Terraform is not preinstalled in Cloud Shell. This installs it into your persistent
`$HOME`, which is a Cloud Shell download, not a change to your own machine.

```bash
mkdir -p "$HOME/bin"
curl -fsSLo "$HOME/terraform.zip" \
  "https://releases.hashicorp.com/terraform/1.16.5/terraform_1.16.5_linux_amd64.zip"
unzip -o "$HOME/terraform.zip" -d "$HOME/bin"
rm "$HOME/terraform.zip"
export PATH="$HOME/bin:$PATH"
terraform version    # expect 1.16.5
```

Persist the PATH so later sessions keep it:

```bash
grep -qxF 'export PATH="$HOME/bin:$PATH"' "$HOME/.bashrc" \
  || echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"
```

> If `terraform version` reports something newer than 1.16.5, Terraform will refuse to run
> because `versions.tf` pins `= 1.16.5`. That pin is deliberate.

---

## Step 2 — Configure the project ID

`terraform.tfvars` is gitignored, so it does not travel with the repo. Create it:

```bash
cat > terraform.tfvars <<EOF
project_id = "$PROJECT_ID"
EOF
```

Enable the one API that must exist *before* Terraform runs:

```bash
gcloud services enable cloudresourcemanager.googleapis.com
```

**Checkpoint:**

```bash
terraform init        # downloads Google 8.5.0 + Random 3.9.1
terraform validate    # <- the real syntax/schema check for main.tf
terraform fmt -check  # should print nothing
```

`terraform validate` is the first moment `main.tf` is genuinely checked. If it fails, the
error names the file and line — read it before changing anything.

---

## Step 3 — ⚠️ Verify the Gemini model IDs (do not skip)

The repo ships `gemini-3.5-flash` / `gemini-3.5-flash-lite`, which are **unverified and
probably wrong** (the catalogue looks to offer `gemini-3-flash`, `gemini-2.5-flash`,
`gemini-2.5-flash-lite`). Confirm against your own project:

```bash
gcloud ai models list --region=global --format='value(name)' 2>/dev/null | sort -u | head -40
```

That often returns nothing useful for publisher models. A more reliable probe is to make
Terraform create the resources and let the first real request tell you — but cheaper is to
check the current published list:

```bash
curl -s "https://aiplatform.googleapis.com/v1/publishers/google/models" | head -50
```

**Pick two models that actually exist:** one Flash-class and one cheaper Flash-Lite-class.
Then edit only the `model:` lines in `config.yaml`:

```bash
nano config.yaml
```

```yaml
  - model_name: gemini-flash-lite
    litellm_params:
      model: vertex_ai/<your-flash-lite-id>     # edit this line only
  - model_name: gemini-flash
    litellm_params:
      model: vertex_ai/<your-flash-id>          # edit this line only
```

**Do not rename the `model_name:` aliases.** `gemini-flash`, `gemini-flash-lite`, and
`jev-router` are referenced by the router tiers and by `benchmark.py`. Only the
`vertex_ai/...` values change.

Record what you chose — you will need it to interpret the benchmark:

```bash
grep -A2 'model_name:' config.yaml
```

---

## Step 4 — TypeSafe (Jev) API key

You need a TypeSafe account; the classifier is a paid third-party call and there is no
offline substitute. Create a key at the TypeSafe console, then store it in Secret Manager
**before** deploying, because `main.tf` reads this secret by name:

```bash
gcloud services enable secretmanager.googleapis.com
gcloud secrets create typesafe-api-key --replication-policy=automatic --data-file=-
```

Paste the key, then press `Ctrl+D` twice (no Enter). Verify it works:

```bash
export TYPESAFE_API_KEY="$(gcloud secrets versions access latest --secret=typesafe-api-key)"
curl -s https://api.typesafe.ai/v1/models -H "Authorization: Bearer $TYPESAFE_API_KEY"
unset TYPESAFE_API_KEY
```

The response should list a model such as `jev-latest`. If it returns 401, replace the
secret with a fresh key:

```bash
gcloud secrets versions add typesafe-api-key --data-file=-
```

---

## Step 5 — Create the image repository

Cloud Run needs an image that does not exist yet, so create the destination first:

```bash
terraform apply -target=google_artifact_registry_repository.images
```

Get the exact image URI Terraform computed:

```bash
export IMAGE_URI="$(terraform console <<< 'local.image_uri' | tr -d '"')"
echo "$IMAGE_URI"    # expect .../litellm:1.104.0
```

---

## Step 6 — Build and push the gateway image

```bash
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker build -t "$IMAGE_URI" .
docker push "$IMAGE_URI"
```

The first build downloads the pinned LiteLLM base image, so expect several minutes of layer
progress. Success ends with a `digest: sha256:...` line.

---

## Step 7 — Deploy everything

```bash
terraform apply
```

Cloud SQL provisioning dominates this step; ~15 minutes is normal and a long pause is not a
hang. Expect output for `service_url`, `admin_url`, and `image_uri`.

```bash
terraform output -raw admin_url
```

Open that URL. The LiteLLM sign-in page confirms the gateway and its database are live.

---

## Step 8 — Sign in and create the benchmark key

The Terraform-generated master key is the Admin UI password:

```bash
gcloud secrets versions access latest --secret=litellm-master-key
```

Sign in with it, then in the Admin UI create a virtual key:

| Setting | Value |
|---|---|
| Key name | `benchmark-key` |
| Allowed model | `gemini-flash` |
| Max budget | `1` (USD) |

Copy the generated `sk-...` value, then:

```bash
gcloud secrets create litellm-api-key --replication-policy=automatic --data-file=-
```

Paste it, `Ctrl+D` twice. Load both variables:

```bash
export LITELLM_BASE_URL="$(terraform output -raw service_url)"
export LITELLM_API_KEY="$(gcloud secrets versions access latest --secret=litellm-api-key)"
```

---

## Step 9 — Run the fixed-model baseline

```bash
python3 benchmark.py baseline
```

Expect four rows, all `returned=gemini-flash`, with an empty `classifier_cost` column —
nothing is being classified yet. That is the waste you are about to remove.

---

## Step 10 — Turn on Jev routing

The running container still has the old `config.yaml`. Bump the image tag so Terraform sees
a change, then rebuild:

```bash
sed -i 's|litellm:1.104.0"|litellm:1.104.0-jev"|' main.tf
terraform validate
export IMAGE_URI="$(terraform console <<< 'local.image_uri' | tr -d '"')"
docker build -t "$IMAGE_URI" .
docker push "$IMAGE_URI"
terraform apply
```

In the Admin UI, edit `benchmark-key` and **add `jev-router`** to the allowed models, keeping
`gemini-flash`. Then:

```bash
python3 benchmark.py routed
```

Now `returned_model` shows the concrete Gemini model Jev chose, not `jev-router`.

---

## Step 11 — Assemble the evidence

Open both CSVs and match rows on the `case` column:

- which cases changed model
- routed `latency_ms` minus baseline `latency_ms`
- `classifier_cost_header` per row

In the Admin UI, **Logs** should show `cause: jev_classifier` with the selected tier, plus a
separate TypeSafe classifier call near the completion. **Usage** shows classifier spend and
completion spend as *separate* records for `benchmark-key`.

> Do not add `x-litellm-classifier-cost` to a Usage total that already includes the
> classifier record — that counts the same charge twice.

---

## Step 12 — Secret mission: prove the budget guardrail

Lower `benchmark-key`'s max budget to a tiny value (e.g. `0.001`), then:

```bash
python3 benchmark.py routed
```

Expected: HTTP **422** once the budget is exhausted. That is the gateway rejecting traffic on
cost grounds — the mechanism that makes this a cost-safety pattern rather than a demo.

---

## Step 13 — Clean up (do this the same day)

Cloud SQL compute and storage bill **even while idle**, and the `$1` virtual-key budget does
**not** cap Google Cloud billing.

```bash
terraform destroy
gcloud secrets delete typesafe-api-key    # created outside Terraform, so not destroyed
```

Confirm the plan removes the Cloud Run service, the Cloud SQL instance, the Artifact Registry
repository (with its images), the three Terraform-managed secrets, the service account, and
the IAM bindings. The `typesafe-api-key` secret is left behind by design.

---

## Troubleshooting firsts

| Symptom | First thing to check |
|---|---|
| `terraform validate` fails | Read the file:line it names. `main.tf` was never validated offline. |
| Model 404 / "model not found" | Step 3. The shipped model IDs are probably wrong. |
| 401 from the gateway | `LITELLM_API_KEY` is stale — re-read the secret, and confirm the virtual key still exists. |
| `jev-router` rejected | `benchmark-key` must list `jev-router` under allowed models. |
| Classifier errors but requests still succeed | Expected: `classifier_fallback: default_model` sends them to `gemini-flash`. |
| Cloud SQL connection refused | The `/cloudsql` volume mount and `roles/cloudsql.client` grant are both required. |
