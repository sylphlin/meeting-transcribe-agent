#!/usr/bin/env bash
# ==============================================================================
# Gemini Enterprise Meeting Transcribe Agent - Unified Deployment Script
# Targets Google Cloud Vertex AI Agent Runtime (Agent Engine / Reasoning Engine)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------------------
# 0. Load Configuration from .env file (if exists)
# ------------------------------------------------------------------------------
ENV_FILE=""
if [ -f "$SCRIPT_DIR/.env" ]; then
    ENV_FILE="$SCRIPT_DIR/.env"
elif [ -f "$SCRIPT_DIR/../.env" ]; then
    ENV_FILE="$SCRIPT_DIR/../.env"
fi

if [ -n "$ENV_FILE" ]; then
    echo "[*] Loading environment variables from: $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# Fallback & Default assignments
PROJECT_ID="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${GCP_REGION:-us-central1}"
BUCKET_NAME="${MEETING_STORAGE_BUCKET:-}"
APPLY_TERRAFORM=false
DRY_RUN=false

usage() {
    cat <<EOF
Usage: ./deploy.sh [OPTIONS]

Deploy Meeting Transcribe Agent to Vertex AI Agent Runtime for Gemini Enterprise.

Environment Variables (.env or shell):
  GCP_PROJECT / GOOGLE_CLOUD_PROJECT  Target GCP Project ID
  GCP_REGION                          Target GCP Region (default: us-central1)
  MEETING_STORAGE_BUCKET              GCS bucket name for meeting media & minutes

Options:
  -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env)
  -r, --region REGION          Google Cloud Region (default: us-central1)
      --apply-terraform        Automatically apply Terraform storage configuration
  -b, --bucket BUCKET_NAME     Custom GCS bucket name for meeting data
  -n, --dry-run                Preview deployment commands without executing
  -h, --help                   Show this help message and exit

Examples:
  ./deploy.sh                                          # Reads from .env or prompts
  ./deploy.sh --apply-terraform                        # Reads from .env and applies Terraform
  ./deploy.sh --project my-gcp-project --region us-central1 --apply-terraform
  ./deploy.sh --dry-run
EOF
    exit 0
}

# ------------------------------------------------------------------------------
# 1. Parse Command-Line Flags (Flags override .env)
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
        --apply-terraform)
            APPLY_TERRAFORM=true
            shift
            ;;
        -b|--bucket)
            BUCKET_NAME="$2"
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
            echo "[!] Unknown option: $1"
            usage
            ;;
    esac
done

echo "=================================================================="
echo "🚀 Gemini Enterprise Agent Deployment"
echo "   Target: Google Cloud Vertex AI Agent Runtime"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 2. Dependency & CLI Validation
# ------------------------------------------------------------------------------
if ! command -v agents-cli &> /dev/null; then
    echo "[!] Error: agents-cli is not found in PATH."
    echo "    Please run: uv tool install google-agents-cli"
    exit 1
fi

# ------------------------------------------------------------------------------
# 3. Project ID Resolution
# Priority: 1. CLI flag -> 2. .env -> 3. gcloud config -> 4. Interactive prompt
# ------------------------------------------------------------------------------
if [ -z "$PROJECT_ID" ]; then
    if command -v gcloud &> /dev/null; then
        PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    fi
fi

if [ -z "$PROJECT_ID" ]; then
    read -rp "Enter your Google Cloud Project ID: " PROJECT_ID
fi

if [ -z "$PROJECT_ID" ]; then
    echo "[!] Error: Project ID is required."
    exit 1
fi

# Strip gs:// prefix if user specified gs://bucket
if [ -n "$BUCKET_NAME" ]; then
    BUCKET_NAME="${BUCKET_NAME#gs://}"
fi

echo "[✓] Target GCP Project: $PROJECT_ID"
echo "[✓] Target Region:      $REGION"
if [ -n "$BUCKET_NAME" ]; then
    echo "[✓] Target Storage:     gs://$BUCKET_NAME"
fi

# ------------------------------------------------------------------------------
# 4. Terraform Storage Provisioning (Optional / Automatic)
# ------------------------------------------------------------------------------
TF_DIR="$SCRIPT_DIR/../terraform"
if [ "$APPLY_TERRAFORM" = true ] || [ "$APPLY_TERRAFORM" = false -a -t 0 ]; then
    if [ "$APPLY_TERRAFORM" = false ]; then
        echo ""
        read -rp "Do you want to provision/update the GCS Storage Bucket via Terraform? (y/N): " PROMPT_TF
        if [[ "$PROMPT_TF" =~ ^[Yy]$ ]]; then
            APPLY_TERRAFORM=true
        fi
    fi

    if [ "$APPLY_TERRAFORM" = true ]; then
        if ! command -v terraform &> /dev/null; then
            echo "[!] Error: Terraform CLI is required to provision storage."
            exit 1
        fi

        echo ""
        echo "[*] Step 1: Applying Terraform Storage Configuration..."
        TF_VARS=(
            -var="project_id=$PROJECT_ID"
            -var="region=$REGION"
            -var="cors_max_age_seconds=86400"
        )
        if [ -n "$BUCKET_NAME" ]; then
            TF_VARS+=(-var="bucket_name=$BUCKET_NAME")
        fi

        if [ "$DRY_RUN" = true ]; then
            echo "[Dry-Run] Would run terraform init and terraform apply ${TF_VARS[*]} in $TF_DIR"
        else
            (
                cd "$TF_DIR"
                terraform init -upgrade
                terraform apply -auto-approve "${TF_VARS[@]}"
            )
            # Retrieve created bucket name if not explicitly set
            if [ -z "$BUCKET_NAME" ]; then
                TF_OUT_BUCKET="$(cd "$TF_DIR" && terraform output -raw meeting_bucket_name 2>/dev/null || true)"
                if [ -n "$TF_OUT_BUCKET" ]; then
                    BUCKET_NAME="$TF_OUT_BUCKET"
                    echo "[✓] Terraform created bucket: gs://$BUCKET_NAME"
                fi
            fi
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 5. Build Deployment Command with Environment Variables for Agent Runtime
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 2: Deploying Agent to Vertex AI Agent Runtime..."

DEPLOY_CMD=(agents-cli deploy -d agent_runtime --project "$PROJECT_ID" --region "$REGION")

# Pass runtime environment variables to the deployed container
RUNTIME_ENV=()
if [ -n "$BUCKET_NAME" ]; then
    RUNTIME_ENV+=("MEETING_STORAGE_BUCKET=gs://$BUCKET_NAME")
fi
if [ -n "${GEMINI_API_KEY:-}" ]; then
    RUNTIME_ENV+=("GEMINI_API_KEY=$GEMINI_API_KEY")
fi

if [ ${#RUNTIME_ENV[@]} -gt 0 ]; then
    ENV_STR=$(IFS=,; echo "${RUNTIME_ENV[*]}")
    DEPLOY_CMD+=(--update-env-vars "$ENV_STR")
fi

if [ "$DRY_RUN" = true ]; then
    DEPLOY_CMD+=(--dry-run)
    echo "[Dry-Run] Executing: ${DEPLOY_CMD[*]}"
    "${DEPLOY_CMD[@]}"
else
    echo "    Command: ${DEPLOY_CMD[*]}"
    "${DEPLOY_CMD[@]}"
fi

echo ""
echo "=================================================================="
echo "✅ Deployment complete!"
echo "👉 Vertex AI Reasoning Engines: https://console.cloud.google.com/vertex-ai/reasoning-engines?project=$PROJECT_ID"
echo "=================================================================="
