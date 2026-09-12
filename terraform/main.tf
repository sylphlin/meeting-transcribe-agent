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

# 1. Primary Cloud Storage Bucket for Meeting Media & Generated Deliverables
resource "google_storage_bucket" "meeting_bucket" {
  name                        = local.actual_bucket_name
  location                    = var.region
  project                     = var.project_id
  storage_class               = "STANDARD" # Objects live days, not months; Nearline/Coldline would cost more.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Ephemeral staging objects only; versioning would keep deleted objects
  # around and work against the retention rules below.
  versioning {
    enabled = false
  }

  # CORS is only needed so a browser can stream media/deliverables directly
  # from signed URLs (the interactive player); server-to-server uploads to
  # raw/ don't need it, but a single bucket-wide rule is simplest.
  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["*"]
    max_age_seconds = var.cors_max_age_seconds # 86400 (24 hours)
  }

  # raw/: media staged here purely to feed Gemini (mirrors the old Files API's
  # ephemeral ~48h auto-delete behavior, since Vertex AI has no Files API of
  # its own and requires a GCS URI instead).
  lifecycle_rule {
    condition {
      age            = var.raw_retention_days
      matches_prefix = ["raw/"]
    }
    action {
      type = "Delete"
    }
  }

  # minutes/ and players/: the generated deliverables. Kept longer than raw/
  # since users need to actually download them, but not forever.
  lifecycle_rule {
    condition {
      age            = var.output_retention_days
      matches_prefix = ["minutes/", "players/"]
    }
    action {
      type = "Delete"
    }
  }
}

# 2. Dedicated Service Account (used by the deployed Gemini Enterprise agent;
#    the Antigravity CLI instead uses the caller's own ADC identity, granted
#    via var.bucket_editors below).
resource "google_service_account" "agent_sa" {
  account_id   = "meeting-transcribe-sa"
  display_name = "Meeting Transcribe Agent Service Account"
  project      = var.project_id
}

# 3. Grant IAM Permissions to Service Account
# Vertex AI Gemini calls (Vertex AI + ADC, no AI Studio API key).
resource "google_project_iam_member" "sa_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "sa_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Object-level access is scoped to just this bucket (not project-wide
# Storage Admin), so a compromised service account can't touch other buckets.
resource "google_storage_bucket_iam_member" "sa_bucket_object_admin" {
  bucket = google_storage_bucket.meeting_bucket.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Extra bucket-scoped object-admin grants (e.g. a personal Google identity
# running the Antigravity CLI locally via `gcloud auth application-default
# login`), on top of the service account above.
resource "google_storage_bucket_iam_member" "extra_bucket_editors" {
  for_each = toset(var.bucket_editors)
  bucket   = google_storage_bucket.meeting_bucket.name
  role     = "roles/storage.objectAdmin"
  member   = each.value
}
