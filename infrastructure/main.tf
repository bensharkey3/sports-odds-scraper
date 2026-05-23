data "aws_caller_identity" "current" {}

locals {
  account_id     = data.aws_caller_identity.current.account_id
  retention_days = var.environment == "prod" ? 365 : 30
}

resource "aws_s3_bucket" "results" {
  bucket = "afl-odds-${var.environment}-${local.account_id}"

  tags = {
    Environment = var.environment
    Project     = "afl-odds-scraper"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    id     = "expire-odds"
    status = "Enabled"

    filter {
      prefix = "odds/"
    }

    expiration {
      days = local.retention_days
    }
  }
}

resource "aws_iam_role" "lambda" {
  name = "afl-odds-lambda-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_s3" {
  name = "s3-put-odds"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:PutObject"
      Resource = "${aws_s3_bucket.results.arn}/odds/*"
    }]
  })
}

resource "aws_lambda_function" "scraper" {
  function_name = "afl-odds-scraper-${var.environment}"
  description   = "Fetches AFL H2H odds from Sportsbet and writes JSONL to S3"
  role          = aws_iam_role.lambda.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"
  timeout       = var.lambda_timeout
  memory_size   = var.lambda_memory
  s3_bucket     = var.artifact_bucket
  s3_key        = var.artifact_key

  environment {
    variables = {
      RESULTS_BUCKET = aws_s3_bucket.results.bucket
      ENVIRONMENT    = var.environment
    }
  }

  tags = {
    Environment = var.environment
    Project     = "afl-odds-scraper"
  }
}

resource "aws_iam_role" "scheduler" {
  name = "afl-odds-scheduler-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-scraper"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.scraper.arn
    }]
  })
}

# Every 4 hours, 9am–9pm Melbourne time — DST-aware via Australia/Melbourne timezone
resource "aws_scheduler_schedule" "scraper" {
  name  = "afl-odds-schedule-${var.environment}"
  state = var.schedule_enabled

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 9,13,17,21 * * ? *)"
  schedule_expression_timezone = "Australia/Melbourne"

  target {
    arn      = aws_lambda_function.scraper.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}
