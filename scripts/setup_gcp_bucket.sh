#!/usr/bin/env bash
# Create the Cloud Storage bucket used by Video Intelligence for large DVR uploads.
# Requires: gcloud CLI logged in (gcloud auth login) and billing enabled.
set -euo pipefail

PROJECT_ID="${1:-}"
REGION="${2:-us-west1}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "Usage: $0 <gcp-project-id> [region]"
  echo "Example: $0 my-sentinel-project us-west1"
  exit 1
fi

BUCKET="${PROJECT_ID}-sentinel-video"

echo "Project: $PROJECT_ID"
echo "Bucket:  gs://${BUCKET}"
echo "Region:  $REGION"

gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com videointelligence.googleapis.com texttospeech.googleapis.com

if gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1; then
  echo "Bucket already exists."
else
  gcloud storage buckets create "gs://${BUCKET}" --location="$REGION" --uniform-bucket-level-access
  echo "Bucket created."
fi

cat <<EOF

Next steps:
1. Create a service account (if needed):
   gcloud iam service-accounts create sentinel-pi --display-name="Sentinel Pi"

2. Grant roles:
   gcloud projects add-iam-policy-binding ${PROJECT_ID} \\
     --member="serviceAccount:sentinel-pi@${PROJECT_ID}.iam.gserviceaccount.com" \\
     --role="roles/storage.objectAdmin"
   gcloud projects add-iam-policy-binding ${PROJECT_ID} \\
     --member="serviceAccount:sentinel-pi@${PROJECT_ID}.iam.gserviceaccount.com" \\
     --role="roles/videointelligence.admin"
   gcloud projects add-iam-policy-binding ${PROJECT_ID} \\
     --member="serviceAccount:sentinel-pi@${PROJECT_ID}.iam.gserviceaccount.com" \\
     --role="roles/cloudtexttospeech.user"

3. Download JSON key to secrets/gcp-service-account.json on the Pi.

4. In config.yaml set:
   google:
     project_id: "${PROJECT_ID}"
     gcs_bucket: "${BUCKET}"

5. Restart: sudo systemctl restart sentinel
EOF
