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

output "parquet_builder_function_name" {
  description = "Parquet builder Lambda function name"
  value       = aws_lambda_function.parquet_builder.function_name
}

output "parquet_s3_path" {
  description = "S3 path where odds-over-time Parquet files are written"
  value       = "s3://${aws_s3_bucket.results.bucket}/parquet/"
}

output "chart_builder_function_name" {
  description = "Chart builder Lambda function name"
  value       = aws_lambda_function.chart_builder.function_name
}

output "charts_s3_path" {
  description = "S3 path where odds-over-time chart PNGs are written"
  value       = "s3://${aws_s3_bucket.results.bucket}/charts/"
}
