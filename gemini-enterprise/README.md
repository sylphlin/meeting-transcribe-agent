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
├── deploy.sh                     # Forwarder to root deploy.sh
├── agents-cli-manifest.yaml      # agents-cli deployment manifest (target: agent_runtime)
└── pyproject.toml                # Dependencies (google-adk, google-genai, google-cloud-storage)
```

---

## Storage & 24-Hour CORS Configuration

The storage bucket (`gs://meeting-transcribe-${GCP_PROJECT}`) enforces enterprise lifecycle rules and cross-origin browser access:

* **24-Hour CORS (`max_age_seconds = 86400`)**: Allows web browsers to stream audio/video media directly from Cloud Storage signed URLs within the interactive player.
* **Tiered Lifecycle Policies**:
  * `raw/`: Ephemeral media staged for Gemini transcription is automatically deleted after 2 days.
  * `minutes/` and `players/`: Generated executive minutes and interactive player deliverables are automatically deleted after 30 days.

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

The unified [`deploy.sh`](../deploy.sh) script located at the repository root handles prerequisite validation, 100% native `gcloud` storage bucket creation with CORS & lifecycle rules, dedicated least-privilege service account setup (`roles/storage.objectUser`), `agents-cli deploy`, and automated Gemini Enterprise registration:

```bash
chmod +x deploy.sh

# Standard automated deployment (reads .env, provisions storage via gcloud, deploys, and links to Gemini Enterprise):
./deploy.sh

# Explicit project/region with custom storage bucket:
./deploy.sh --project YOUR_PROJECT_ID --region us-central1 --bucket YOUR_BUCKET

# Preview execution without making cloud changes:
./deploy.sh --dry-run
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
