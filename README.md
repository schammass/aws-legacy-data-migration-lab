# AWS Legacy Data Migration Lab

A proof of concept for loading legacy customer CSVs into Amazon S3, combining
records with Amazon Athena CTAS, and storing a curated Parquet table.

## Implemented capabilities

- Separate S3 raw prefixes and Glue Data Catalog external tables for master,
  CRM, and address CSV sources.
- Athena CTAS processing into the `customer_golden` Parquet table.
- Basic input validation for CSV headers and row widths, configuration, and
  SQL statement structure.
- Athena data-quality checks for output preview, source coverage, non-empty
  results, and non-null golden IDs and normalized emails.
- Terraform-managed S3, Glue Data Catalog database, and Athena workgroup.
- GitHub Actions workflows for Terraform validation/planning and manually
  approved infrastructure applies, plus input validation and manual data
  pipeline execution.

## Architecture

```text
Local CSV files
    |
    v
S3 raw prefixes (master / crm / addresses)
    |
    v
AWS Glue Data Catalog (database and external-table metadata)
    |
    v
Amazon Athena CTAS
    |
    v
S3 curated/customer_golden/ (Parquet)
    |
    v
Athena data-quality queries
```

AWS Glue is used only as the Data Catalog; it does not run ETL jobs. Athena
executes the DDL, CTAS transformation, and validation queries. Athena query
results are configured by default to use `s3://legacy-migration-lab/athena-results/`.
The CTAS table data is written separately to
`s3://legacy-migration-lab/curated/customer_golden/`.

## Source data and transformation

The repository includes three CSV inputs:

| Source | Local file | S3 prefix | Glue table |
| --- | --- | --- | --- |
| Master customers | `data/master/customers_master.csv` | `data/master/` | `raw_customers_master` |
| CRM customers | `data/crm/customers_crm.csv` | `data/crm/` | `raw_customers_crm` |
| Addresses | `data/addresses/customer_addresses.csv` | `data/addresses/` | `raw_customer_addresses` |

The CTAS query combines the master and CRM rows with a full outer join using
lowercased, trimmed email addresses. Master values take precedence where
present; email, phone, and postal code are normalized. Address records are
left-joined using the master customer ID.

The curated output includes source identifiers and the fields `valid_from`,
`valid_to`, and `is_current`. These are initial current-state values: the
pipeline does not maintain history or update effective dates across runs.

This is a simple rule-based POC, not a full entity-resolution system. It does
not implement fuzzy matching, conflict scoring, duplicate resolution,
quarantine/reject handling, or a separate legacy-to-golden crosswalk. Multiple
source rows with the same matching email can produce multiple joined rows;
the project does not guarantee deterministic deduplication.

## AWS infrastructure and CI/CD

Terraform provisions/configures:

- The existing `legacy-migration-lab` S3 bucket, including encryption,
  versioning, and public-access blocking.
- The Glue Data Catalog database `legacy_migration_lab`.
- The Athena workgroup `legacy-migration-lab`, with a per-query scan cutoff
  and encrypted default query-results location.

The Athena workgroup does not enforce its result configuration. This allows
the CTAS statement to set the curated `external_location`; individual queries
can also override the workgroup's default result location or encryption.

GitHub Actions behavior:

- Pull requests validate Terraform or the pipeline inputs and run the Python
  unit tests.
- Pushes to `main` validate Terraform and create a saved plan; they do not
  apply infrastructure.
- Applying Terraform requires a manual workflow dispatch using the run ID of
  a reviewed plan from the current `main` commit.
- The Athena data workflow runs against AWS only when manually dispatched on
  `main` with `confirm_run` set to `yes`.

See [terraform/README.md](terraform/README.md) for AWS OIDC setup, IAM
permissions, Terraform workflow details, and pipeline safeguards. Do not put
long-lived AWS credentials in GitHub or commit them to the repository.

## Run the data pipeline locally

Provision the AWS infrastructure before running the data pipeline. The data
script does not create or manage infrastructure with Terraform. Requirements:

- Python 3.12 or newer.
- AWS CLI v2.
- AWS credentials with the Athena pipeline permissions documented in
  [terraform/README.md](terraform/README.md#athena-pipeline-permissions).

Use an approved local AWS profile, such as an IAM Identity Center profile. The
GitHub Actions OIDC role is for GitHub Actions and is not a local credential.

### Configure AWS credentials

For Linux/macOS or Windows, configure and log in to an IAM Identity Center
profile, then verify the AWS identity:

```sh
aws configure sso --profile migration-lab
aws sso login --profile migration-lab
aws sts get-caller-identity --profile migration-lab --region ca-central-1
```

### Set up Python

From the repository root, create and activate a virtual environment. This
project's Python pipeline uses only the standard library; no `pip install` is
required.

Linux/macOS:

```sh
python3.12 -m venv venv
source venv/bin/activate
```

Windows PowerShell:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
```

### Validate without accessing AWS

Linux/macOS:

```sh
VALIDATE_ONLY=true TF_DATA_BUCKET=legacy-migration-lab \
  python scripts/run_athena_pipeline.py
python -m unittest discover -s tests
```

Windows PowerShell:

```powershell
$env:VALIDATE_ONLY = "true"
$env:TF_DATA_BUCKET = "legacy-migration-lab"
python scripts/run_athena_pipeline.py
Remove-Item Env:VALIDATE_ONLY
python -m unittest discover -s tests
```

### Run against AWS

First confirm the selected AWS account, region, bucket, database, workgroup,
Glue tables, and S3 prefixes. The pipeline checks that the curated table and
output prefix do not already exist and that existing raw tables have the
expected schema and S3 location.

Linux/macOS:

```sh
AWS_PROFILE=migration-lab \
AWS_REGION=ca-central-1 \
TF_DATA_BUCKET=legacy-migration-lab \
ATHENA_WORKGROUP=legacy-migration-lab \
python scripts/run_athena_pipeline.py
```

Windows PowerShell:

```powershell
$env:AWS_PROFILE = "migration-lab"
$env:AWS_REGION = "ca-central-1"
$env:TF_DATA_BUCKET = "legacy-migration-lab"
$env:ATHENA_WORKGROUP = "legacy-migration-lab"
python scripts/run_athena_pipeline.py
```

The real run uploads the CSVs, creates any missing raw external tables, runs
the CTAS query, and executes the data-quality checks. Uploading replaces an
existing S3 object if it has the same key as a local CSV. The pipeline does not
delete data or provide a refresh mode. A successful run leaves a curated table
and output that cause a later run to stop; a failed run may leave uploaded
files, tables, or query results. Inspect AWS resources before retrying or
cleaning anything up.

Athena queries, S3 requests, and storage may incur charges. The workgroup has a
per-query bytes-scanned cutoff, but this is not a total project cost limit.

## Future extensions

Potential next steps include quarantine and reject handling, duplicate/conflict
resolution, a maintained legacy-to-golden crosswalk, historical effective-date
management, Step Functions orchestration, and CloudWatch monitoring.
