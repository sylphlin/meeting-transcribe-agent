# Gemini Enterprise Meeting Transcribe Agent

**Gemini Enterprise Meeting Transcribe Agent** is a cloud meeting transcription and intelligence service built with **ADK 2.0** and **`agents-cli`**. It deploys to **Google Cloud Vertex AI Agent Runtime** (Agent Engine) and registers with **Gemini Enterprise**.

---

## Architecture & Overview

This package wraps the core transcription engine as a managed cloud agent. It processes meeting recordings from four input sources:

1. **Google Drive Recordings**: Streams video and audio from Google Drive to Cloud Storage (`gs://<bucket>/raw/`), recovers UTF-8 CJK filenames, and runs multimodal vision and audio analysis.
2. **YouTube Video URLs**: Streams YouTube videos directly to Vertex AI without local file downloads.
3. **Google Cloud Storage (GCS) Media**: Transcribes audio and video stored in GCS using **Gemini 3.5 Transcribe** (`gemini-3.5-transcribe-preview`) and **Gemini 3.8 Flash** (`gemini-3.8-flash`).
4. **Automated Deliverable Hosting**: Uploads Markdown meeting minutes (`.md`) and interactive HTML players (`.html`) to GCS and returns **24-hour v4 signed URLs**.

---

## Directory Structure

```text
gemini-enterprise/
├── app/
│   ├── __init__.py               # Application package initializer
│   ├── agent.py                  # ADK 2.0 Agent definition (prompt, tools, model)
│   ├── tools/                    # Enterprise tool modules
│   │   ├── __init__.py
│   │   ├── transcribe_tool.py    # Unified meeting transcription tool
│   │   ├── drive_tool.py         # Google Drive to GCS streaming & export tool
│   │   └── gcs_tool.py           # GCS upload and 24-hour signed URL tool
│   ├── core/                     # Upstream transcription and diarization modules
│   └── assets/                   # Prompt templates and HTML player templates
├── deploy.sh                     # Wrapper forwarding to root deploy.sh
├── agents-cli-manifest.yaml      # agents-cli deployment manifest (target: agent_runtime)
└── pyproject.toml                # Package dependencies (google-adk, google-genai, google-cloud-storage)
```

---

## Cloud Storage CORS & Two-Tier Lifecycle Policy

The staging bucket (`gs://meeting-transcribe-${GOOGLE_CLOUD_PROJECT}`) enforces browser CORS rules and automatic object expiration:

* **24-Hour Browser CORS (`max_age_seconds = 86400`)**: Allows the interactive HTML player to stream audio and video from GCS signed URLs.
* **Two-Tier Lifecycle Rules**:
  * `raw/`: Deletes staged input media automatically after **2 days** (`age: 2`).
  * `minutes/`, `players/`, `output/`, and `deliverables/`: Deletes generated reports and HTML players automatically after **15 days** (`age: 15`).

---

## Deploy to Vertex AI Agent Runtime

### Prerequisites

1. Install `uv` and `google-agents-cli`:
   ```bash
   uv tool install google-agents-cli
   ```
2. Authenticate Google Cloud ADC:
   ```bash
   gcloud auth application-default login
   ```

### Run One-Click Deployment (`./deploy.sh`)

Run [`deploy.sh`](../deploy.sh) from the repository root to provision cloud resources via `setup.sh`, deploy the agent to Vertex AI Agent Runtime, and register with Gemini Enterprise:

```bash
chmod +x setup.sh deploy.sh

# Deploy using .env or active gcloud configuration:
./deploy.sh

# Specify project ID, region, and bucket explicitly:
./deploy.sh --project YOUR_GCP_PROJECT_ID --region us-central1 --bucket YOUR_BUCKET

# Preview commands without making cloud changes:
./deploy.sh --dry-run
```

---

## Query the Agent in Gemini Enterprise

Send natural language prompts to the deployed agent in Gemini Enterprise or through the Vertex AI Reasoning Engine API:

* *"Transcribe this Google Meet recording: `https://drive.google.com/file/d/FILE_ID/view`"*
* *"Analyze and summarize this public session: `https://www.youtube.com/watch?v=VIDEO_ID`"*
* *"Transcribe the audio recording at `gs://meeting-transcribe-PROJECT_ID/raw/audios/meeting_recording.mp3`"*

The agent returns:
1. **Meeting Metadata & Attendees**: Canonical speaker names and titles matched from visual nameplates and dialogue.
2. **Executive Summary**: Structured overview of key decisions and discussion points.
3. **Action Items Table**: Assigned owners, due dates, and action descriptions.
4. **24-Hour Signed URLs**: Direct browser links to the Markdown report and interactive HTML player.
