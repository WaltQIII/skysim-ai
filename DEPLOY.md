# SkySim AI — Cloud Run Deployment & Hard Budget Cap Guide (`DEPLOY.md`)

This document provides the production deployment commands and **Section 7 Guardrail #2** billing protections (**$50 / 50%** and **$90 / 90%** alerts on the **$100 monthly credit** plus `max-instances=3` scaling cap).

---

## 1. Automated Google Cloud Billing Alerts ($50 at 50% and $90 at 90% of $100 Budget)

### Option A: `gcloud` CLI Commands
Run the following commands to create a **$100/month** hard-monitored budget with automated email/PubSub alerts at **50% ($50)** and **90% ($90)**:

```bash
export PROJECT_ID="skysim-ai-prod"
export BILLING_ACCOUNT_ID="012345-6789AB-CDEF01"

# Enable Cloud Billing Budgets API
gcloud services enable billingbudgets.googleapis.com --project="${PROJECT_ID}"

# Create the $100 monthly budget with 50% ($50) and 90% ($90) threshold rules
gcloud billing budgets create \
  --billing-account="${BILLING_ACCOUNT_ID}" \
  --display-name="SkySim AI $100 Monthly Hard Cap" \
  --budget-amount=100USD \
  --calendar-period=month \
  --filter-projects="projects/${PROJECT_ID}" \
  --threshold-rule=percent=0.50,basis=current-spend \
  --threshold-rule=percent=0.90,basis=current-spend \
  --threshold-rule=percent=0.95,basis=forecasted-spend
```

### Option B: Terraform Configuration (`billing_budget.tf`)

```hcl
resource "google_billing_budget" "skysim_monthly_100_cap" {
  billing_account = var.billing_account_id
  display_name    = "SkySim AI $100 Monthly Cap ($50 & $90 Alerts)"

  budget_filter {
    projects               = ["projects/${var.project_id}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "100"
    }
  }

  # $50.00 (50% of $100 monthly credit)
  threshold_rules {
    threshold_percent = 0.50
    spend_basis       = "CURRENT_SPEND"
  }

  # $90.00 (90% of $100 monthly credit)
  threshold_rules {
    threshold_percent = 0.90
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [var.notification_channel_id]
    disable_default_iam_recipients   = false
  }
}
```

---

## 2. Secret Manager & GCS Direct Upload Bucket Setup

```bash
# Create GCS bucket for direct client signed-URL PUT uploads
gcloud storage buckets create "gs://skysim-ai-raw-uploads" \
  --project="${PROJECT_ID}" \
  --location="us-east1" \
  --uniform-bucket-level-access

# Store API credentials strictly in Google Secret Manager (Zero plaintext keys in client bundles)
echo -n "${GEMINI_ULTRA_API_KEY}" | gcloud secrets create gemini-ultra-api-key --data-file=-
echo -n "${PROJECT_GENIE_API_KEY}" | gcloud secrets create project-genie-api-key --data-file=-
```

---

## 3. Single-Command Cloud Run Deployment (`max-instances=3`)

```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_REGION="us-east1",_GCS_BUCKET="skysim-ai-raw-uploads"
```

Or deploy directly via `gcloud run deploy`:

```bash
gcloud run deploy skysim-ai-backend \
  --source . \
  --region us-east1 \
  --platform managed \
  --allow-unauthenticated \
  --max-instances=3 \
  --min-instances=0 \
  --memory=1Gi \
  --set-env-vars="GCP_MONTHLY_BUDGET_USD=100.0,CLOUD_RUN_MAX_INSTANCES=3,GCS_UPLOAD_BUCKET=skysim-ai-raw-uploads"
```
