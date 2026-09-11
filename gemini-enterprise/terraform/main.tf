terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

locals {
  actual_bucket_name = var.bucket_name != "" ? var.bucket_name : "${var.project_id}-meeting-transcribe-${random_id.bucket_suffix.hex}"
}

# 1. Primary Cloud Storage Bucket for Meeting Transcripts & Media
resource "google_storage_bucket" "meeting_bucket" {
  name                        = local.actual_bucket_name
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true

  # 24-hour CORS rule for browser-based audio and video player streaming
  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["*"]
    max_age_seconds = var.cors_max_age_seconds # 86400 (24 hours)
  }

  # Tiered Lifecycle: Purge bulky raw video files after specified days
  lifecycle_rule {
    condition {
      age            = var.video_retention_days
      matches_prefix = ["raw/videos/"]
    }
    action {
      type = "Delete"
    }
  }

  # Tiered Lifecycle: Purge raw audio files after specified days
  lifecycle_rule {
    condition {
      age            = var.audio_retention_days
      matches_prefix = ["raw/audios/"]
    }
    action {
      type = "Delete"
    }
  }
}

# 2. Dedicated Service Account for Agent Runtime
resource "google_service_account" "agent_sa" {
  account_id   = "meeting-transcribe-sa"
  display_name = "Gemini Enterprise Meeting Transcribe Agent SA"
  project      = var.project_id
}

# 3. Grant IAM Permissions to Service Account
resource "google_project_iam_member" "sa_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "sa_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "sa_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}
