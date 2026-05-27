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

  rule {
    id     = "expire-brownlow"
    status = "Enabled"

    filter {
      prefix = "brownlow/"
    }

    expiration {
      days = local.retention_days
    }
  }

  rule {
    id     = "expire-premiership"
    status = "Enabled"

    filter {
      prefix = "premiership/"
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
  name = "s3-odds"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject"]
        Resource = [
          "${aws_s3_bucket.results.arn}/odds/*",
          "${aws_s3_bucket.results.arn}/brownlow/*",
          "${aws_s3_bucket.results.arn}/premiership/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.results.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_ssm" {
  name = "ssm-slack-webhook"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = "arn:aws:ssm:ap-southeast-2:${local.account_id}:parameter/afl-odds/*"
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
      RESULTS_BUCKET          = aws_s3_bucket.results.bucket
      ENVIRONMENT             = var.environment
      SLACK_PARAM_NAME        = "/afl-odds/slack-webhook"
      SLACK_FAVOURITE_PARAM_NAME = "/afl-odds/slack-webhook-favourite"
    }
  }

  tags = {
    Environment = var.environment
    Project     = "afl-odds-scraper"
  }
}

resource "aws_iam_role" "notifier" {
  name = "afl-odds-s3-notifier-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "notifier_basic" {
  role       = aws_iam_role.notifier.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "notifier_ssm" {
  name = "ssm-slack-webhook"
  role = aws_iam_role.notifier.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = "arn:aws:ssm:ap-southeast-2:${local.account_id}:parameter/afl-odds/*"
    }]
  })
}

resource "aws_lambda_function" "s3_notifier" {
  function_name = "afl-odds-s3-notifier-${var.environment}"
  description   = "Sends Slack notification when a file lands in the odds S3 bucket"
  role          = aws_iam_role.notifier.arn
  handler       = "handler.s3_lambda_handler"
  runtime       = "python3.12"
  timeout       = 10
  memory_size   = 128
  s3_bucket     = var.artifact_bucket
  s3_key        = var.artifact_key

  environment {
    variables = {
      SLACK_PARAM_NAME = "/afl-odds/slack-webhook"
    }
  }

  tags = {
    Environment = var.environment
    Project     = "afl-odds-scraper"
  }
}

resource "aws_lambda_permission" "s3_invoke_notifier" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.s3_notifier.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.results.arn
}

resource "aws_s3_bucket_notification" "results" {
  bucket = aws_s3_bucket.results.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.s3_notifier.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "odds/"
  }

  depends_on = [aws_lambda_permission.s3_invoke_notifier]
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

# Every 2 hours, 9am–9pm Melbourne time — DST-aware via Australia/Melbourne timezone
resource "aws_scheduler_schedule" "scraper" {
  name  = "afl-odds-schedule-${var.environment}"
  state = var.schedule_enabled

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 9,11,13,15,17,19,21 * * ? *)"
  schedule_expression_timezone = "Australia/Melbourne"

  target {
    arn      = aws_lambda_function.scraper.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}
