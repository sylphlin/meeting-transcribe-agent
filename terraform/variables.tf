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

variable "video_retention_days" {
  type        = number
  description = "Number of days before raw video files in raw/videos/ are automatically purged"
  default     = 14
}

variable "audio_retention_days" {
  type        = number
  description = "Number of days before raw audio files in raw/audios/ are automatically purged"
  default     = 30
}
