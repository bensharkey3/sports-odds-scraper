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
    error_message = "The schedule_enabled value must be ENABLED or DISABLED."
  }
}

variable "lambda_timeout" {
  type        = number
  default     = 300
  description = "Lambda timeout in seconds"
}

variable "lambda_memory" {
  type        = number
  default     = 128
  description = "Lambda memory in MB"
}

variable "chart_artifact_key" {
  type        = string
  default     = "afl-odds/chart-lambda.zip"
  description = "S3 key of the self-contained chart builder Lambda zip (pandas + matplotlib + fastparquet)"
}

variable "pandas_layer_arn" {
  type        = string
  default     = "arn:aws:lambda:ap-southeast-2:336392948345:layer:AWSSDKPandas-Python312:27"
  description = "AWS-managed SDK for pandas layer (pandas + pyarrow) for the parquet builder Lambda"
}
