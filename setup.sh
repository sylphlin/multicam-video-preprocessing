#!/usr/bin/env bash
# ==============================================================================
# Antigravity & GCP Environment Setup Script (setup.sh)
#
# 100% Native gcloud Provisioning for Multicam Video Preprocessing Suite.
# Designed for Antigravity IDE / Agent & Developer CLI to provision GCP Vertex AI,
# GCS Storage, Lifecycle Auto-Cleanup, IAM bindings, and local .env in one step.
# (Note: deploy.sh is reserved for future Gemini Enterprise Agent Runtime deployment.)
#
# Usage:
#   ./setup.sh [OPTIONS]
#
# Options:
#   -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env / gcloud config)
#   -r, --region REGION          Google Cloud Storage Region (default: us-central1)
#   -l, --location LOCATION      Vertex AI Model Location (default: global)
#   -b, --bucket BUCKET_NAME     Custom GCS bucket name (default: multicam-video-${PROJECT_ID})
#   -s, --service-account SA     Optional custom service account email
#   -n, --dry-run                Preview gcloud setup commands without executing
#   -h, --help                   Show this help message and exit
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

# Ensure common macOS / Linux Google Cloud SDK & Homebrew paths are in PATH
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/homebrew/share/google-cloud-sdk/bin:/usr/local/share/google-cloud-sdk/bin:$HOME/google-cloud-sdk/bin:$PATH"

# ------------------------------------------------------------------------------
# 1. Load Environment Configuration
# ------------------------------------------------------------------------------
if [ -f "$REPO_ROOT/.env" ]; then
    echo "[*] Loading environment variables from: $REPO_ROOT/.env"
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
fi

PROJECT_ID="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
PROJECT_NUMBER="${GCP_PROJECT_NUMBER:-${PROJECT_NUMBER:-}}"
REGION="${GCP_REGION:-us-central1}"
LOCATION="${GOOGLE_CLOUD_LOCATION:-global}"
BUCKET_NAME="${GCS_BUCKET:-${MULTICAM_STORAGE_BUCKET:-}}"
SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-${SERVICE_ACCOUNT:-}}"
DRY_RUN=false

usage() {
    cat <<EOF
Usage: ./setup.sh [OPTIONS]

Provision Google Cloud Vertex AI & Cloud Storage (GCS) environment for
Multicam Video Preprocessing & AI Editing Suite (100% native gcloud + ADC).

Environment Variables (.env or shell):
  GOOGLE_CLOUD_PROJECT / GCP_PROJECT  Target GCP Project ID
  GOOGLE_CLOUD_LOCATION               Vertex AI Gemini endpoint location (default: global)
  GCP_REGION                          GCS Bucket region (default: us-central1)
  GCS_BUCKET                          GCS bucket name (default: multicam-video-\${PROJECT_ID})
  GCP_SERVICE_ACCOUNT                 Optional custom Service Account

Options:
  -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env)
  -r, --region REGION          GCS Bucket Region (default: us-central1)
  -l, --location LOCATION      Vertex AI Model Location (default: global)
  -b, --bucket BUCKET_NAME     Custom GCS bucket name (default: multicam-video-\${PROJECT_ID})
  -s, --service-account SA     Optional custom service account email
  -n, --dry-run                Preview commands without executing
  -h, --help                   Show this help message and exit

Examples:
  ./setup.sh                                          # Auto-provisions GCS bucket, Lifecycle, IAM & .env
  ./setup.sh --project my-gcp-project                 # Specify project ID explicitly
  ./setup.sh --bucket my-existing-bucket              # Use a custom GCS bucket name
  ./setup.sh --dry-run                                # Preview all gcloud actions
EOF
    exit 0
}

# ------------------------------------------------------------------------------
# 2. Parse Command-Line Flags (Flags override .env)
# ------------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--project)
            PROJECT_ID="$2"
            shift 2
            ;;
        -r|--region)
            REGION="$2"
            shift 2
            ;;
        -l|--location)
            LOCATION="$2"
            shift 2
            ;;
        -b|--bucket)
            BUCKET_NAME="$2"
            shift 2
            ;;
        -s|--service-account)
            SERVICE_ACCOUNT="$2"
            shift 2
            ;;
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "[!] Error: Unknown argument \"$1\""
            usage
            ;;
    esac
done

echo "=================================================================="
echo "🛠️  Multicam Video Preprocessing Suite - GCP Environment Setup"
echo "    Mode: 100% Native gcloud + Application Default Credentials (ADC)"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 3. Prerequisites & Context Resolution
# ------------------------------------------------------------------------------
if [ "$DRY_RUN" = false ] && ! command -v gcloud &> /dev/null; then
    echo "[!] Error: gcloud CLI is not installed or not in PATH."
    echo "    Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

if [ -z "$PROJECT_ID" ] && command -v gcloud &> /dev/null; then
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    if [ "$PROJECT_ID" = "(unset)" ]; then
        PROJECT_ID=""
    fi
fi

if [ -z "$PROJECT_ID" ]; then
    if [ -t 0 ]; then
        read -rp "Enter your Google Cloud Project ID: " PROJECT_ID
    fi
fi

if [ -z "$PROJECT_ID" ]; then
    echo "[!] Error: Google Cloud Project ID is required (pass --project <ID> or set GOOGLE_CLOUD_PROJECT)."
    exit 1
fi

# Verify ADC authentication status & Google Drive Read-Only scope
ADC_SCOPES="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly"
if [ "$DRY_RUN" = false ]; then
    TOKEN="$(gcloud auth application-default print-access-token 2>/dev/null || true)"
    if [ -z "$TOKEN" ]; then
        echo "[!] Warning: Application Default Credentials (ADC) not found or expired."
        echo "    Launching: gcloud auth application-default login (with Cloud Platform + Google Drive Read-Only scopes)..."
        gcloud auth application-default login --scopes="$ADC_SCOPES"
    else
        # Check if current ADC token includes Google Drive scope; if not, inform user
        TOKEN_INFO="$(curl -s "https://oauth2.googleapis.com/tokeninfo?access_token=${TOKEN}" 2>/dev/null || true)"
        if echo "$TOKEN_INFO" | grep -q "drive"; then
            echo "[✓] Application Default Credentials (ADC) verified (includes Google Drive scope)."
        else
            echo "[✓] Application Default Credentials (ADC) verified."
            echo "    [i] Note: To enable direct Google Drive link ingestion, ensure ADC includes 'drive.readonly':"
            echo "        gcloud auth application-default login --scopes=\"$ADC_SCOPES\""
        fi
    fi
    gcloud auth application-default set-quota-project "$PROJECT_ID" --quiet 2>/dev/null || true
fi

if [ -z "$PROJECT_NUMBER" ] && command -v gcloud &> /dev/null; then
    PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)"
fi

ACTIVE_ACCOUNT=""
if command -v gcloud &> /dev/null; then
    ACTIVE_ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
fi

# Strip gs:// prefix if user specified gs://bucket
if [ -n "$BUCKET_NAME" ]; then
    BUCKET_NAME="${BUCKET_NAME#gs://}"
fi

# Set deterministic default bucket name
if [ -z "$BUCKET_NAME" ]; then
    BUCKET_NAME="multicam-video-${PROJECT_ID}"
fi

echo "[✓] Target GCP Project:   $PROJECT_ID (Number: ${PROJECT_NUMBER:-unknown})"
echo "[✓] Vertex AI Location:   $LOCATION"
echo "[✓] GCS Storage Region:   $REGION"
echo "[✓] GCS Storage Bucket:   gs://$BUCKET_NAME"
if [ -n "$ACTIVE_ACCOUNT" ]; then
    echo "[✓] Active GCP Identity:  $ACTIVE_ACCOUNT"
fi

# ------------------------------------------------------------------------------
# 4. Step 1: Enable Required Google Cloud APIs via gcloud
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 1: Enabling Vertex AI, Cloud Storage & Google Drive APIs via gcloud..."
if [ "$DRY_RUN" = false ]; then
    gcloud services enable aiplatform.googleapis.com storage.googleapis.com drive.googleapis.com \
        --project="$PROJECT_ID" --quiet
    echo "    [✓] APIs enabled (aiplatform.googleapis.com, storage.googleapis.com, drive.googleapis.com)."
else
    echo "    [Dry-Run] Would run: gcloud services enable aiplatform.googleapis.com storage.googleapis.com drive.googleapis.com --project=$PROJECT_ID"
fi

# ------------------------------------------------------------------------------
# 5. Step 2: Provision GCS Bucket & 2-Day Lifecycle Rules via gcloud
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 2: Provisioning / Verifying GCS Bucket & Lifecycle Auto-Cleanup..."
if [ "$DRY_RUN" = false ]; then
    if ! gcloud storage buckets describe "gs://$BUCKET_NAME" --project="$PROJECT_ID" &>/dev/null; then
        echo "    [*] Creating GCS bucket: gs://$BUCKET_NAME (Region: $REGION)..."
        gcloud storage buckets create "gs://$BUCKET_NAME" \
            --project="$PROJECT_ID" \
            --location="$REGION" \
            --uniform-bucket-level-access \
            --public-access-prevention \
            --quiet
        echo "    [✓] Created GCS bucket gs://$BUCKET_NAME."
    else
        echo "    [✓] Storage bucket gs://$BUCKET_NAME already exists."
    fi

    # Configure Lifecycle Rules:
    # - raw/: Auto-delete after 2 days (ephemeral staging for multimodal video & audio)
    # - output/, deliverables/, multicam_assets/: Auto-delete after 15 days (deliverable retention)
    LIFECYCLE_FILE="$(mktemp 2>/dev/null || echo "/tmp/multicam_lifecycle_$$.json")"
    cat << 'EOF' > "$LIFECYCLE_FILE"
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {
        "age": 2,
        "matchesPrefix": ["raw/"]
      }
    },
    {
      "action": {"type": "Delete"},
      "condition": {
        "age": 15,
        "matchesPrefix": ["output/", "deliverables/", "multicam_assets/"]
      }
    }
  ]
}
EOF
    gcloud storage buckets update "gs://$BUCKET_NAME" --lifecycle-file="$LIFECYCLE_FILE" --quiet 2>/dev/null || true
    rm -f "$LIFECYCLE_FILE"
    echo "    [✓] Applied Lifecycle policy: 'raw/' (2 days) & 'output/, deliverables/, multicam_assets/' (15 days)."
else
    echo "    [Dry-Run] Would ensure GCS bucket gs://$BUCKET_NAME exists in $REGION with Lifecycle rules (raw/: 2d, deliverables: 15d)."
fi

# ------------------------------------------------------------------------------
# 6. Step 3: Ensure Least-Privilege IAM (roles/storage.objectUser) via gcloud
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 3: Ensuring Least-Privilege IAM bindings (roles/storage.objectUser) on gs://$BUCKET_NAME..."
if [ "$DRY_RUN" = false ]; then
    # 1. Grant to current authenticated user
    if [ -n "$ACTIVE_ACCOUNT" ]; then
        MEMBER_PREFIX="user"
        if [[ "$ACTIVE_ACCOUNT" == *.gserviceaccount.com ]]; then
            MEMBER_PREFIX="serviceAccount"
        fi
        echo "    Granting roles/storage.objectUser to ${MEMBER_PREFIX}:${ACTIVE_ACCOUNT}..."
        gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
            --member="${MEMBER_PREFIX}:${ACTIVE_ACCOUNT}" \
            --role="roles/storage.objectUser" --quiet 2>/dev/null || true
    fi

    # 2. Grant to custom Service Account if specified
    if [ -n "$SERVICE_ACCOUNT" ]; then
        echo "    Granting roles/storage.objectUser to serviceAccount:${SERVICE_ACCOUNT}..."
        gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
            --member="serviceAccount:${SERVICE_ACCOUNT}" \
            --role="roles/storage.objectUser" --quiet 2>/dev/null || true
    fi

    # 3. Grant to Vertex AI Service Agents (so Vertex AI Gemini can read gs:// media directly)
    if [ -n "$PROJECT_NUMBER" ]; then
        VERTEX_AGENTS=(
            "service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"
            "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
            "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
        )
        for sa in "${VERTEX_AGENTS[@]}"; do
            echo "    Granting roles/storage.objectUser to Vertex AI Service Agent ($sa)..."
            gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
                --member="serviceAccount:$sa" \
                --role="roles/storage.objectUser" --quiet 2>/dev/null || true
        done
    fi
else
    echo "    [Dry-Run] Would grant roles/storage.objectUser on gs://$BUCKET_NAME to active user and Vertex AI service agents."
fi

# ------------------------------------------------------------------------------
# 7. Step 4: Write / Synchronize Project .env File
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 4: Writing configuration to $REPO_ROOT/.env ..."
if [ "$DRY_RUN" = false ]; then
    cat <<EOF > "$REPO_ROOT/.env"
# Google Cloud Vertex AI & Cloud Storage Configuration (100% ADC)
GOOGLE_CLOUD_PROJECT=$PROJECT_ID
GOOGLE_CLOUD_LOCATION=$LOCATION
GCP_REGION=$REGION
GCS_BUCKET=$BUCKET_NAME
EOF
    echo "    [✓] Saved $REPO_ROOT/.env"
else
    echo "    [Dry-Run] Would write GOOGLE_CLOUD_PROJECT=$PROJECT_ID, GOOGLE_CLOUD_LOCATION=$LOCATION, GCS_BUCKET=$BUCKET_NAME to $REPO_ROOT/.env"
fi

echo ""
echo "=================================================================="
echo "✅ GCP Environment Setup Complete (setup.sh)!"
echo "   • Project  : $PROJECT_ID"
echo "   • Location : $LOCATION (Vertex AI)"
echo "   • Bucket   : gs://$BUCKET_NAME (raw/: 2d | deliverables: 15d)"
echo "   • Config   : $REPO_ROOT/.env"
echo "=================================================================="
