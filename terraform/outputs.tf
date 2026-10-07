output "data_bucket_name" {
  description = "S3 bucket for source files, curated output, and Athena results."
  value       = aws_s3_bucket.data.bucket
}

output "raw_data_prefix" {
  description = "S3 prefix for source CSV uploads."
  value       = "s3://${aws_s3_bucket.data.bucket}/data/"
}

output "curated_data_prefix" {
  description = "S3 prefix for curated Parquet output."
  value       = "s3://${aws_s3_bucket.data.bucket}/curated/"
}

output "athena_workgroup_name" {
  description = "Athena workgroup with scan limits and encrypted default query results."
  value       = aws_athena_workgroup.migration.name
}

output "glue_database_name" {
  description = "Glue Data Catalog database name."
  value       = aws_glue_catalog_database.migration.name
}
