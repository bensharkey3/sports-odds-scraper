variable "environment" {
  type        = string
  description = "Deployment environment"
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be dev or prod."
  }
}

variable "artifact_bucket" {
  type        = string
  description = "S3 bucket containing the Lambda deployment zip"
}

variable "artifact_key" {
  type        = string
  default     = "afl-odds/lambda.zip"
  description = "S3 key of the Lambda zip package"
}

variable "schedule_enabled" {
  type        = string
  default     = "ENABLED"
  description = "Whether the EventBridge schedule is active"
  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.schedule_enabled)
    error_message = "schedule_enabled must be ENABLED or DISABLED."
  }
}

variable "lambda_timeout" {
  type        = number
  default     = 120
  description = "Lambda timeout in seconds"
}

variable "lambda_memory" {
  type        = number
  default     = 128
  description = "Lambda memory in MB"
}
