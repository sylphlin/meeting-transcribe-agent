# Gemini Enterprise Meeting Transcribe Agent

An enterprise-grade meeting intelligence and transcription agent powered by **ADK 2.0** and **`agents-cli`**, deployed natively on **Google Cloud Vertex AI Agent Runtime** (Agent Engine / Reasoning Engine) for **Gemini Enterprise**.

---

## Architecture & Overview

This package extends the core transcription suite into a managed cloud enterprise agent. It seamlessly handles multimodal meetings from multiple corporate and public sources:

1. **Google Meet Recordings (Google Drive)**: Low-memory streaming from Google Drive into Cloud Storage staging, executing multimodal vision analysis (identifying speaker names from screen titles and extracting presentation slide content).
2. **YouTube Video URLs**: Direct streaming via Gemini Cloud backbone without local downloads or disk footprint.
3. **Google Cloud Storage (GCS) Media**: High-precision acoustic transcription (`gemini-3.5-transcribe`) with millisecond timestamps and physical acoustic diarization, structured concurrently by `gemini-3.8-flash`.
4. **Automated Storage & Delivery**: Generates structured executive meeting minutes (`.md`) and interactive HTML playback players (`.html`), uploaded to GCS with **24-hour signed URLs** for instant browser review.

---

## Directory Structure

```text
gemini-enterprise/
├── app/
│   ├── __init__.py               # App factory and A2A surface
│   ├── agent.py                  # ADK 2.0 Agent definition (Prompt, Tools, Model)
│   ├── tools/                    # Enterprise Tool Modules
│   │   ├── __init__.py
│   │   ├── transcribe_tool.py    # Unified meeting transcribe tool
│   │   ├── drive_tool.py         # Google Drive stream pipe to GCS
│   │   └── gcs_tool.py           # GCS storage and 24h signed URL generator
│   ├── core/                     # Transcription and diarization engines
│   └── assets/                   # Offline audio and multimodal video player templates
├── deploy.sh                     # Unified shell deployment script
├── agents-cli-manifest.yaml      # agents-cli project manifest (deployment target: agent_runtime)
└── pyproject.toml                # Dependencies (google-adk, google-genai, google-cloud-storage)

../terraform/                     # Shared GCS storage provisioning (repo root; also used by the Antigravity CLI)
├── main.tf                       # GCS bucket with 24h CORS & lifecycle rules
├── variables.tf                  # GCP project, region, and retention policies
├── outputs.tf                    # Bucket name and IAM service account email
└── terraform.tfvars.example      # Configuration template
```

---

## Storage & 24-Hour CORS Configuration

The generated meeting storage bucket enforces enterprise lifecycle rules and cross-origin browser access:

* **24-Hour CORS (`max_age_seconds = 86400`)**: Allows web browsers to stream audio/video media directly from Cloud Storage signed URLs within the interactive player.
* **Tiered Lifecycle Policies**:
  * `raw/`: Media staged here purely to feed Gemini (Vertex AI has no Files API of its own, so this mirrors its old ~48h ephemeral-upload behavior) is automatically deleted after 2 days.
  * `minutes/` and `players/`: Generated executive minutes and player HTML deliverables are automatically deleted after 14 days.

---

## Deployment to Vertex AI Agent Runtime

### Prerequisites

1. Install `uv` and `agents-cli`:
   ```bash
   uv tool install google-agents-cli
   ```
2. Authenticate with Google Cloud:
   ```bash
   gcloud auth application-default login
   ```

### Quick Start: One-Click Shell Script

The unified [`deploy.sh`](deploy.sh) script handles prerequisite validation, optional Terraform storage provisioning, and `agents-cli deploy`:

```bash
chmod +x deploy.sh

# Interactive mode (prompts for Project ID if not set):
./deploy.sh

# Non-interactive automated deployment:
./deploy.sh --project YOUR_PROJECT_ID --region us-central1 --apply-terraform

# Preview execution without making cloud changes:
./deploy.sh --project YOUR_PROJECT_ID --dry-run
```

### Manual CLI Deployment (Alternative)

1. Provision Storage:
   ```bash
   cd ../terraform
   terraform init
   terraform apply -var="project_id=YOUR_PROJECT_ID" -var="region=us-central1"
   cd -
   ```
2. Deploy Agent:
   ```bash
   agents-cli deploy -d agent_runtime --project YOUR_PROJECT_ID --region us-central1
   ```

---

## Interacting with the Agent in Gemini Enterprise

Once deployed, the agent can be queried via natural language within Gemini Enterprise or through the Vertex AI Reasoning Engines API:

* *"Please transcribe this Google Meet recording: https://drive.google.com/file/d/FILE_ID"*
* *"Analyze and summarize this public session: https://www.youtube.com/watch?v=VIDEO_ID"*
* *"Transcribe the audio recording stored at gs://YOUR_PROJECT_ID-meeting-data/raw/audios/session.mp3"*

The agent returns:
1. **Meeting Title & Attendees**: Speaker identification cross-referenced with on-screen nameplates and vocal dialogue.
2. **Executive Summary**: 300–400 word synthesis of key discussions.
3. **Action Items Table**: Assigned owners, deadlines, and statuses.
4. **24-Hour Signed Links**: Direct web access to the Markdown record and interactive HTML player.
