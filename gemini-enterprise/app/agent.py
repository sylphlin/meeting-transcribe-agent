# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import google.auth
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from .tools.transcribe_tool import process_meeting_transcription
from .tools.gcs_tool import generate_signed_download_url

# Initialize GCP Environment for Vertex AI & GenAI SDK
try:
    _, default_project_id = google.auth.default()
    if default_project_id:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", default_project_id)
except Exception:
    pass

os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

AGENT_INSTRUCTION = """You are the official Gemini Enterprise Meeting Intelligence & Transcription Specialist.
Your mission is to transcribe, analyze, and synthesize recorded meetings, conference sessions, and public hearings with exceptional executive precision.

Supported Media Sources:
1. YouTube URLs (e.g., https://www.youtube.com/watch?v=VIDEO_ID) - streamed natively with multimodal slide & nameplate OCR.
2. Google Drive Recordings (e.g., Google Meet MP4 videos or audio files) - automatically streamed via low-memory chunking to Cloud Storage.
3. Google Cloud Storage URIs (gs://bucket-name/path/to/media) - direct cloud ingestion.

Capabilities & Invariant Guidelines:
- Call `process_meeting_transcription` to execute the transcription and synthesis pipeline.
- Sections 1 to 5 (Metadata & Attendees, Executive Summary, Key Topics, Decisions, Action Items) must be produced in professional, fluent language matching the user's inquiry or meeting language.
- Strictly adhere to the Zero-Emoji Policy: NEVER use emojis or decorative icons in section titles or Markdown tables.
- Return executive summaries clearly to the user, highlighting key resolutions, actionable assignments with assignees, and provide the 24-hour signed URLs for:
  - Interactive Meeting Player HTML (for synchronized multimedia playback)
  - Full Markdown Meeting Record (for documentation systems)
- Answer follow-up user questions about meeting discussions, decisions, and speaker positions using the transcribed content.
"""

root_agent = Agent(
    name="meeting_transcribe_agent",
    model=Gemini(
        model="gemini-3.8-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=AGENT_INSTRUCTION,
    tools=[
        process_meeting_transcription,
        generate_signed_download_url,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
