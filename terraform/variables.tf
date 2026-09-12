variable "project_id" {
  type        = string
  description = "Google Cloud Project ID"
}

variable "region" {
  type        = string
  description = "Google Cloud Region for deployment and storage"
  default     = "us-central1"
}

variable "bucket_name" {
  type        = string
  description = "Name of the Google Cloud Storage bucket for meeting media and minutes (leave empty for auto-generated name)"
  default     = ""
}

variable "cors_max_age_seconds" {
  type        = number
  description = "CORS Max-Age in seconds for the storage bucket (default: 86400 = 24 hours)"
  default     = 86400
}

variable "raw_retention_days" {
  type        = number
  description = "Number of days before raw media in raw/ (uploaded to feed Gemini, mirroring the old Files API's ephemeral upload) is automatically purged"
  default     = 2
}

variable "output_retention_days" {
  type        = number
  description = "Number of days before generated deliverables in minutes/ and players/ are automatically purged"
  default     = 14
}

variable "bucket_editors" {
  type        = list(string)
  description = "Additional IAM members granted object-admin (create/read/delete) access on the bucket, in `user:`/`serviceAccount:`/`group:` form. The dedicated agent service account is always included; add your own identity here for local/personal use (e.g. \"user:you@example.com\") if your project role doesn't already cover it."
  default     = []
}
