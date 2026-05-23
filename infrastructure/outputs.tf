output "results_bucket_name" {
  description = "S3 bucket where odds files are written"
  value       = aws_s3_bucket.results.bucket
}

output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.scraper.function_name
}

output "latest_odds_s3_path" {
  description = "S3 path for latest odds snapshot"
  value       = "s3://${aws_s3_bucket.results.bucket}/odds/latest.jsonl"
}
