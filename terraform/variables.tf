variable "project_name" {
  description = "Project name used for AWS resource names and tags."
  type        = string
  default     = "aws-legacy-data-migration-lab"
}

variable "aws_region" {
  description = "AWS region where project resources are created."
  type        = string
  default     = "ca-central-1"
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name for source data, curated data, and Athena results."
  type        = string
  default     = "legacy-migration-lab"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be a valid 3-63 character S3 bucket name."
  }
}

variable "database_name" {
  description = "AWS Glue Data Catalog database used by Athena."
  type        = string
  default     = "legacy_migration_lab"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]{0,254}$", var.database_name))
    error_message = "database_name must start with a lowercase letter and contain only lowercase letters, digits, and underscores."
  }
}

variable "workgroup_name" {
  description = "Athena workgroup name."
  type        = string
  default     = "legacy-migration-lab"
}

variable "bytes_scanned_cutoff_per_query" {
  description = "Maximum bytes Athena may scan per query in this workgroup."
  type        = number
  default     = 1000000000

  validation {
    condition     = var.bytes_scanned_cutoff_per_query > 0
    error_message = "bytes_scanned_cutoff_per_query must be greater than zero."
  }
}
