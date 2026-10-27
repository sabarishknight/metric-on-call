#!/usr/bin/env bash
# One-command local build + test + deploy of Metric On-Call to your Google Cloud project.
#
#   Run from a normal Mac terminal (it opens your browser for Google sign-in):
#       bash scripts/deploy_gcp.sh
#
# Order: verify locally (tests, data, offline eval)  ->  sign in  ->  check billing  ->
#        Terraform (APIs + registry)  ->  build loader image in Cloud Build  ->  Terraform (everything)
#        ->  load the dataset into BigQuery  ->  run the offline eval against BigQuery.
# Nothing touches the cloud unless the local tests pass. Terraform asks you to confirm each apply.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_ID="${GCP_PROJECT:-metric-on-call}"
REGION="${GCP_REGION:-australia-southeast1}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/moc/loader:latest"
export GCP_PROJECT="$PROJECT_ID" GCP_REGION="$REGION"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing '$1'. Install: $2"; exit 1; }; }
need uv        "brew install uv"
need gcloud    "brew install --cask google-cloud-sdk"
need terraform "brew tap hashicorp/tap && brew install hashicorp/tap/terraform"

say "1/8 Install and verify locally (no cloud yet)   [set SKIP_LOCAL=1 to skip on re-runs]"
if [ "${SKIP_LOCAL:-0}" != "1" ]; then
uv sync --python 3.11 --extra agents --extra warehouse --extra app --extra cloud --extra dev
uv run ruff check . || echo "ruff reported issues (continuing; tests decide)"
uv run python -m generator.build
uv run pytest
uv run python -m evals.run --config scripted --smoke
else
  uv sync --python 3.11 --extra agents --extra warehouse --extra app --extra cloud --extra dev >/dev/null
fi

say "2/8 Sign in to Google Cloud (browser window will open if needed)"
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then gcloud auth login; fi
gcloud config set project "$PROJECT_ID" >/dev/null
if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then gcloud auth application-default login; fi
gcloud auth application-default set-quota-project "$PROJECT_ID" >/dev/null 2>&1 || true

say "3/8 Check the project exists and billing is linked"
gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1 || { echo "Project '$PROJECT_ID' not found. Create it in the console first (pick a Billing account in the dialog)."; exit 1; }
if [ "$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null)" != "True" ]; then
  echo "Billing is not linked to '$PROJECT_ID'."
  echo "Link it: https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT_ID  then re-run this script."
  exit 1
fi

say "4/8 Terraform step 1: enable APIs and create the image registry"
( cd infra
  terraform init
  terraform validate
  terraform apply -target=google_project_service.apis -target=google_artifact_registry_repository.moc \
    -var "project=$PROJECT_ID" -var "region=$REGION" -var "loader_image=$IMAGE" )

say "5/8 Build the loader image in Cloud Build"
PN="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
for role in roles/cloudbuild.builds.builder roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" --role="$role" --condition=None >/dev/null || true
done
LOADER_IMAGE="$IMAGE"
if ! gcloud builds submit --config pipelines/cloudbuild.yaml --substitutions "_IMAGE=${IMAGE}" . ; then
  echo ""
  echo "WARNING: image build failed (see error above). Continuing WITHOUT the nightly loader job."
  echo "         BigQuery, the bucket and the data load still go ahead. Fix permissions later and re-run."
  LOADER_IMAGE=""
fi

say "6/8 Terraform step 2: bucket, BigQuery datasets, Cloud Run job, nightly schedule"
( cd infra && terraform apply -var "project=$PROJECT_ID" -var "region=$REGION" -var "loader_image=$LOADER_IMAGE" )
BUCKET="$(cd infra && terraform output -raw landing_bucket)"

say "7/8 Load the dataset into BigQuery (and archive the CSVs in the bucket)"
gcloud storage cp data/csv/*.csv "gs://${BUCKET}/seed/"
uv run python -m pipelines.bootstrap_bigquery

say "8/8 Prove it end to end: run the offline eval reading from BigQuery"
WAREHOUSE=bigquery BQ_MAX_BYTES_BILLED="${BQ_MAX_BYTES_BILLED:-5000000000}" \
  uv run python -m evals.run --config scripted --smoke

say "Done"
echo "Project:   https://console.cloud.google.com/home/dashboard?project=${PROJECT_ID}"
echo "BigQuery:  https://console.cloud.google.com/bigquery?project=${PROJECT_ID}"
echo "Bucket:    gs://${BUCKET}"
echo "Next:      set a budget alert at https://console.cloud.google.com/billing/budgets"
echo "Tear down: make cloud-down"
