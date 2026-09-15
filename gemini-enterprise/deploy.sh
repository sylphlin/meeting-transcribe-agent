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
PROJECT_NUMBER="${GCP_PROJECT_NUMBER:-${PROJECT_NUMBER:-}}"
REGION="${GCP_REGION:-us-central1}"
BUCKET_NAME="${MEETING_STORAGE_BUCKET:-}"
SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-${SERVICE_ACCOUNT:-}}"
GE_APP="${GEMINI_ENTERPRISE_APP_ID:-}"
GE_LOCATION="${GEMINI_ENTERPRISE_LOCATION:-global}"
SKIP_GE=false
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
  GCP_SERVICE_ACCOUNT                 Custom Service Account for the deployed agent
  GEMINI_ENTERPRISE_APP_ID            Gemini Enterprise App ID / Resource name
  GEMINI_ENTERPRISE_LOCATION          Gemini Enterprise Location (default: global)

Options:
  -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env)
  -r, --region REGION          Google Cloud Region (default: us-central1)
      --apply-terraform        Automatically apply Terraform storage configuration
  -b, --bucket BUCKET_NAME     Custom GCS bucket name for meeting data
  -s, --service-account SA     Custom service account email for the deployed agent
      --ge APP_ID              Gemini Enterprise App ID or full resource name (overrides .env/auto-detect)
      --ge-location LOCATION   Gemini Enterprise location (default: global)
      --skip-ge                Skip linking agent to Gemini Enterprise
  -n, --dry-run                Preview deployment commands without executing
  -h, --help                   Show this help message and exit

Examples:
  ./deploy.sh                                          # Reads from .env or prompts
  ./deploy.sh --ge my-ge-app                           # Deploys and registers to Gemini Enterprise
  ./deploy.sh --apply-terraform                        # Reads from .env and applies Terraform
  ./deploy.sh --project my-gcp-project --region us-central1 --ge my-ge-app
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
        -s|--service-account)
            SERVICE_ACCOUNT="$2"
            shift 2
            ;;
        --ge)
            GE_APP="$2"
            shift 2
            ;;
        --ge-location)
            GE_LOCATION="$2"
            shift 2
            ;;
        --skip-ge)
            SKIP_GE=true
            shift
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

# Ensure target GCS bucket has objectAdmin permissions for Agent Runtime
if [ -n "$BUCKET_NAME" ]; then
    if [ -z "$PROJECT_NUMBER" ] && command -v gcloud &> /dev/null; then
        PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)"
    fi

    if command -v gcloud &> /dev/null && [ "$DRY_RUN" = false ]; then
        echo "[*] Ensuring GCS bucket IAM permissions on gs://$BUCKET_NAME..."
        if [ -n "$SERVICE_ACCOUNT" ]; then
            echo "    Granting roles/storage.objectAdmin to $SERVICE_ACCOUNT..."
            gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
                --member="serviceAccount:$SERVICE_ACCOUNT" \
                --role="roles/storage.objectAdmin" --quiet 2>/dev/null || true
        else
            if [ -n "$PROJECT_NUMBER" ]; then
                RE_AGENTS=(
                    "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
                    "service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"
                    "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
                )
                for sa in "${RE_AGENTS[@]}"; do
                    echo "    Granting roles/storage.objectAdmin to $sa..."
                    gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
                        --member="serviceAccount:$sa" \
                        --role="roles/storage.objectAdmin" --quiet 2>/dev/null || true
                done
            fi
        fi
    fi
fi

SERVICE_NAME="${SERVICE_NAME:-meeting-transcribe-agent}"
DEPLOY_CMD=(agents-cli deploy -d agent_runtime --project "$PROJECT_ID" --region "$REGION" --service-name "$SERVICE_NAME")

if [ -n "$SERVICE_ACCOUNT" ]; then
    DEPLOY_CMD+=(--service-account "$SERVICE_ACCOUNT")
fi

# Pass runtime environment variables to the deployed container. Gemini calls
# always use Vertex AI + the deployed agent's own service account credentials
# -- there is no AI Studio API key to pass through.
RUNTIME_ENV=()
if [ -n "$BUCKET_NAME" ]; then
    RUNTIME_ENV+=("MEETING_STORAGE_BUCKET=gs://$BUCKET_NAME")
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
echo "✅ Vertex AI Agent Runtime Deployment Complete!"
echo "👉 Vertex AI Reasoning Engines: https://console.cloud.google.com/vertex-ai/reasoning-engines?project=$PROJECT_ID"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 6. Step 3: Gemini Enterprise Registration (Fully Automated)
# ------------------------------------------------------------------------------
if [ "$SKIP_GE" = true ]; then
    echo ""
    echo "[*] Step 3: Skipping Gemini Enterprise registration (--skip-ge specified)."
else
    # Auto-discover Gemini Enterprise app if not explicitly provided via --ge or .env
    if [ -z "$GE_APP" ]; then
        echo ""
        echo "[*] Step 3: Auto-discovering Gemini Enterprise apps in project $PROJECT_ID..."
        GE_LIST_RAW="$(agents-cli publish gemini-enterprise --list --project "$PROJECT_ID" 2>/dev/null || true)"

        GE_APP="$(echo "$GE_LIST_RAW" | python3 -c '
import sys, json, re
text = sys.stdin.read()
match = re.search(r"\{\s*\"apps\"\s*:\s*\[.*?\]\s*\}", text, re.DOTALL)
if match:
    try:
        data = json.loads(match.group(0))
        apps = data.get("apps", [])
        if apps:
            print(apps[0].get("name", ""))
    except Exception:
        pass
' 2>/dev/null || true)"

        if [ -n "$GE_APP" ]; then
            echo "[✓] Auto-detected Gemini Enterprise app: $GE_APP"
        else
            echo "[!] No Gemini Enterprise apps found in project $PROJECT_ID."
        fi
    fi

    if [ -n "$GE_APP" ]; then
        echo ""
        echo "[*] Step 3: Registering Agent to Gemini Enterprise..."

        # If short Engine ID provided (does not start with 'projects/'), construct full resource name
        if [[ "$GE_APP" != projects/* ]]; then
            if [ -z "$PROJECT_NUMBER" ] && command -v gcloud &> /dev/null; then
                PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)"
            fi
            if [ -n "$PROJECT_NUMBER" ]; then
                GE_APP="projects/${PROJECT_NUMBER}/locations/${GE_LOCATION}/collections/default_collection/engines/${GE_APP}"
            else
                echo "[!] Warning: Could not resolve project number. Passing '$GE_APP' directly."
            fi
        fi

        echo "    Target GE App: $GE_APP"

        GE_DISPLAY_NAME="${GEMINI_DISPLAY_NAME:-Meeting Transcribe Agent}"
        GE_DESCRIPTION="${GEMINI_DESCRIPTION:-Universal meeting intelligence and interactive verbatim transcription suite.}"
        GE_TOOL_DESCRIPTION="${GEMINI_TOOL_DESCRIPTION:-Transcribes meeting audio/video, generates structured executive minutes, action items, and interactive verbatim transcripts.}"

        PUBLISH_CMD=(
            agents-cli publish gemini-enterprise
            --gemini-enterprise-app-id "$GE_APP"
            --registration-type adk
            --display-name "$GE_DISPLAY_NAME"
            --description "$GE_DESCRIPTION"
            --tool-description "$GE_TOOL_DESCRIPTION"
        )

        if [ -n "$PROJECT_ID" ]; then
            PUBLISH_CMD+=(--project-id "$PROJECT_ID")
        fi
        if [ -n "$PROJECT_NUMBER" ]; then
            PUBLISH_CMD+=(--project-number "$PROJECT_NUMBER")
        fi

        if [ "$DRY_RUN" = true ]; then
            echo "[Dry-Run] Executing: ${PUBLISH_CMD[*]}"
        else
            echo "    Command: ${PUBLISH_CMD[*]}"
            "${PUBLISH_CMD[@]}"
            echo ""
            echo "=================================================================="
            echo "🎉 Successfully linked Meeting Transcribe Agent to Gemini Enterprise!"
            echo "=================================================================="
        fi
    else
        echo ""
        echo "ℹ️  Note: No Gemini Enterprise app found or specified."
        echo "   To link to a specific app, re-run with: ./deploy.sh --ge <APP_ID_OR_FULL_RESOURCE_NAME>"
        echo "   or set GEMINI_ENTERPRISE_APP_ID in .env"
    fi
fi
