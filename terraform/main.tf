provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "Terraform"
    }
  }
}

import {
  to = aws_s3_bucket.data
  id = var.bucket_name
}

import {
  to = aws_athena_workgroup.migration
  id = "legacy-migration-lab"
}

resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_glue_catalog_database" "migration" {
  name         = var.database_name
  location_uri = "s3://${aws_s3_bucket.data.bucket}/"
}

resource "aws_athena_workgroup" "migration" {
  name = var.workgroup_name

  configuration {
    enforce_workgroup_configuration    = false
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query

    result_configuration {
      output_location = "s3://${aws_s3_bucket.data.bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}
