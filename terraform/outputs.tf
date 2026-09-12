output "meeting_bucket_name" {
  description = "Name of the created GCS bucket for meeting data"
  value       = google_storage_bucket.meeting_bucket.name
}

output "meeting_bucket_uri" {
  description = "GCS URI of the created bucket"
  value       = "gs://${google_storage_bucket.meeting_bucket.name}"
}

output "service_account_email" {
  description = "Email of the dedicated Agent Service Account"
  value       = google_service_account.agent_sa.email
}

output "project_id" {
  description = "GCP Project ID"
  value       = var.project_id
}

output "region" {
  description = "GCP Region"
  value       = var.region
}
