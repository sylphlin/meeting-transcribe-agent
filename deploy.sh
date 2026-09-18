#!/usr/bin/env bash
# ==============================================================================
# Deploy Meeting Transcribe Agent to Vertex AI Agent Runtime for Gemini Enterprise
#
# 100% Native gcloud Deployment (Zero Terraform Dependency, Cloud Shell Ready)
#
# Usage:
#   ./deploy.sh [OPTIONS]
#
# Options:
#   -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env)
#   -r, --region REGION          Google Cloud Region (default: us-central1)
#   -b, --bucket BUCKET_NAME     Custom GCS bucket name (default: meeting-transcribe-${PROJECT_ID})
#   -s, --service-account SA     Custom service account email (default: meeting-transcribe-sa@...)
#       --ge APP_ID              Gemini Enterprise App ID or full resource name
#       --ge-location LOCATION   Gemini Enterprise location (default: global)
#       --skip-ge                Skip linking agent to Gemini Enterprise
#   -n, --dry-run                Preview deployment commands without executing
#   -h, --help                   Show this help message and exit
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
GE_DIR="$REPO_ROOT/gemini-enterprise"

# ------------------------------------------------------------------------------
# 1. Load Environment Configuration
# ------------------------------------------------------------------------------
if [ -f "$REPO_ROOT/.env" ]; then
    echo "[*] Loading environment variables from: $REPO_ROOT/.env"
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
elif [ -f "$GE_DIR/.env" ]; then
    echo "[*] Loading environment variables from: $GE_DIR/.env"
    set -a
    # shellcheck disable=SC1091
    source "$GE_DIR/.env"
    set +a
fi

PROJECT_ID="${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
PROJECT_NUMBER="${GCP_PROJECT_NUMBER:-${PROJECT_NUMBER:-}}"
REGION="${GCP_REGION:-us-central1}"
BUCKET_NAME="${MEETING_STORAGE_BUCKET:-}"
SERVICE_ACCOUNT="${GCP_SERVICE_ACCOUNT:-${SERVICE_ACCOUNT:-}}"
GE_APP="${GEMINI_ENTERPRISE_APP_ID:-}"
GE_LOCATION="${GEMINI_ENTERPRISE_LOCATION:-global}"
SKIP_GE=false
DRY_RUN=false

usage() {
    cat <<EOF
Usage: ./deploy.sh [OPTIONS]

Deploy Meeting Transcribe Agent to Vertex AI Agent Runtime for Gemini Enterprise.
(100% native gcloud provisioning - zero external tool dependencies)

Environment Variables (.env or shell):
  GCP_PROJECT / GOOGLE_CLOUD_PROJECT  Target GCP Project ID
  GCP_REGION                          Target GCP Region (default: us-central1)
  MEETING_STORAGE_BUCKET              GCS bucket name (default: meeting-transcribe-\${PROJECT_ID})
  GCP_SERVICE_ACCOUNT                 Custom Service Account for the deployed agent
  GEMINI_ENTERPRISE_APP_ID            Gemini Enterprise App ID / Resource name
  GEMINI_ENTERPRISE_LOCATION          Gemini Enterprise Location (default: global)

Options:
  -p, --project PROJECT_ID     Google Cloud Project ID (overrides .env)
  -r, --region REGION          Google Cloud Region (default: us-central1)
  -b, --bucket BUCKET_NAME     Custom GCS bucket name (default: meeting-transcribe-\${PROJECT_ID})
  -s, --service-account SA     Custom service account email (default: meeting-transcribe-sa@...)
      --ge APP_ID              Gemini Enterprise App ID or full resource name
      --ge-location LOCATION   Gemini Enterprise location (default: global)
      --skip-ge                Skip linking agent to Gemini Enterprise
  -n, --dry-run                Preview deployment commands without executing
  -h, --help                   Show this help message and exit

Examples:
  ./deploy.sh                                          # Deploys with automatic gcloud provisioning
  ./deploy.sh --bucket my-existing-bucket              # Deploys with a specific storage bucket
  ./deploy.sh --ge my-ge-app                           # Deploys and links to specific GE app
  ./deploy.sh --dry-run
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
            echo "[!] Error: Unknown argument \"$1\""
            usage
            ;;
    esac
done

echo "=================================================================="
echo "🚀 Gemini Enterprise Agent Deployment"
echo "   Target: Google Cloud Vertex AI Agent Runtime"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 3. Prerequisites & Context Resolution
# ------------------------------------------------------------------------------
if ! command -v agents-cli &> /dev/null; then
    echo "[!] Error: agents-cli is not found in PATH."
    echo "    Please run: uv tool install google-agents-cli"
    exit 1
fi

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

if [ -z "$PROJECT_NUMBER" ] && command -v gcloud &> /dev/null; then
    PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)"
fi

# Strip gs:// prefix if user specified gs://bucket
if [ -n "$BUCKET_NAME" ]; then
    BUCKET_NAME="${BUCKET_NAME#gs://}"
fi

# Set deterministic default names
if [ -z "$BUCKET_NAME" ]; then
    BUCKET_NAME="meeting-transcribe-${PROJECT_ID}"
fi

if [ -z "$SERVICE_ACCOUNT" ]; then
    SERVICE_ACCOUNT="meeting-transcribe-sa@${PROJECT_ID}.iam.gserviceaccount.com"
fi

echo "[✓] Target GCP Project: $PROJECT_ID"
echo "[✓] Target Region:      $REGION"
echo "[✓] Storage Bucket:     gs://$BUCKET_NAME"
echo "[✓] Service Account:    $SERVICE_ACCOUNT"

# ------------------------------------------------------------------------------
# 4. Step 1: Provision Google Cloud Infrastructure via setup.sh
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 1: Provisioning Google Cloud infrastructure via ./setup.sh..."

SETUP_CMD=(
    "$REPO_ROOT/setup.sh"
    --project "$PROJECT_ID"
    --region "$REGION"
    --bucket "$BUCKET_NAME"
    --service-account "$SERVICE_ACCOUNT"
    --create-sa
)

if [ "$DRY_RUN" = true ]; then
    SETUP_CMD+=(--dry-run)
fi

"${SETUP_CMD[@]}"

# ------------------------------------------------------------------------------
# 5. Step 2: Deploy Agent to Vertex AI Agent Runtime
# ------------------------------------------------------------------------------
echo ""
echo "[*] Step 2: Deploying Agent to Vertex AI Agent Runtime..."

SERVICE_NAME="${SERVICE_NAME:-meeting-transcribe-agent}"
DEPLOY_CMD=(agents-cli deploy -d agent_runtime --project "$PROJECT_ID" --region "$REGION" --service-name "$SERVICE_NAME")

if [ -n "$SERVICE_ACCOUNT" ]; then
    DEPLOY_CMD+=(--service-account "$SERVICE_ACCOUNT")
fi

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
    echo "    [Dry-Run] Executing: (cd gemini-enterprise && ${DEPLOY_CMD[*]})"
    (cd "$GE_DIR" && "${DEPLOY_CMD[@]}")
else
    echo "    Command: (cd gemini-enterprise && ${DEPLOY_CMD[*]})"
    (cd "$GE_DIR" && "${DEPLOY_CMD[@]}")
fi

echo ""
echo "=================================================================="
echo "✅ Vertex AI Agent Runtime Deployment Complete!"
echo "👉 Vertex AI Reasoning Engines: https://console.cloud.google.com/vertex-ai/reasoning-engines?project=$PROJECT_ID"
echo "=================================================================="

# ------------------------------------------------------------------------------
# 7. Step 4: Gemini Enterprise Registration (Fully Automated)
# ------------------------------------------------------------------------------
if [ "$SKIP_GE" = true ]; then
    echo ""
    echo "[*] Step 4: Skipping Gemini Enterprise registration (--skip-ge specified)."
else
    # Auto-discover Gemini Enterprise app if not explicitly provided via --ge or .env
    if [ -z "$GE_APP" ] && command -v agents-cli &> /dev/null && [ "$DRY_RUN" = false ]; then
        echo ""
        echo "[*] Step 4: Auto-discovering Gemini Enterprise apps in project $PROJECT_ID..."
        LIST_OUTPUT="$(agents-cli publish gemini-enterprise --list --project "$PROJECT_ID" 2>/dev/null || true)"
        
        DISCOVERED_APP="$(python3 -c '
import json, sys
data_str = sys.stdin.read().strip()
for line in data_str.splitlines():
    line = line.strip()
    if line.startswith("{") and "apps" in line:
        try:
            d = json.loads(line)
            apps = d.get("apps", [])
            if apps:
                print(apps[0].get("name", ""))
                sys.exit(0)
        except Exception:
            pass
' <<< "$LIST_OUTPUT")"

        if [ -n "$DISCOVERED_APP" ]; then
            GE_APP="$DISCOVERED_APP"
            echo "[✓] Auto-selected Gemini Enterprise App: $GE_APP"
        else
            echo "[!] No Gemini Enterprise apps found in project $PROJECT_ID."
        fi
    fi

    if [ -n "$GE_APP" ]; then
        echo ""
        echo "[*] Registering Agent to Gemini Enterprise..."

        # If short Engine ID provided (does not start with 'projects/'), construct full resource name
        if [[ "$GE_APP" != projects/* ]]; then
            if [ -z "$PROJECT_NUMBER" ] && command -v gcloud &> /dev/null; then
                PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)"
            fi
            if [ -n "$PROJECT_NUMBER" ]; then
                GE_APP="projects/${PROJECT_NUMBER}/locations/${GE_LOCATION}/collections/default_collection/engines/${GE_APP}"
            fi
        fi

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
            PUBLISH_CMD+=(--dry-run)
            echo "    [Dry-Run] Executing: (cd gemini-enterprise && ${PUBLISH_CMD[*]})"
            (cd "$GE_DIR" && "${PUBLISH_CMD[@]}") || true
        else
            echo "    Command: (cd gemini-enterprise && ${PUBLISH_CMD[*]})"
            (cd "$GE_DIR" && "${PUBLISH_CMD[@]}")
            echo ""
            echo "=================================================================="
            echo "🎉 Agent successfully registered to Gemini Enterprise!"
            echo "👉 Open Gemini Enterprise and start chatting with Meeting Transcribe Agent."
            echo "=================================================================="
        fi
    else
        echo ""
        echo "ℹ️  Note: No Gemini Enterprise app found or specified."
        echo "   To link to a specific app, re-run with: ./deploy.sh --ge <APP_ID_OR_FULL_RESOURCE_NAME>"
        echo "   or set GEMINI_ENTERPRISE_APP_ID in .env"
    fi
fi
