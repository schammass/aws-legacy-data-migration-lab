# Terraform and GitHub Actions

This Terraform configuration provisions the AWS foundation for the lab:

- The existing `legacy-migration-lab` S3 bucket, imported into Terraform,
  configured with encryption, versioning, and public access blocked
- A Glue Data Catalog database
- An Athena workgroup with encrypted default query results and a per-query scan
  limit. Workgroup settings are not enforced so CTAS queries can set their own
  `external_location`.

The bucket is also used as the Terraform state backend for this proof of
concept. Keep the Terraform state under its dedicated key; do not delete the
bucket or the `aws-legacy-data-migration-lab/` state prefix.

## One-time setup

1. Confirm that `legacy-migration-lab` exists in the configured AWS region and
   that the GitHub Actions role can access it. Terraform will import the bucket
   rather than create a new one, and will enable versioning when applied.
   Versioning is recommended for state recovery but is not required for the
   S3 backend to function.
2. Configure the AWS IAM GitHub OIDC provider and a role trusted only for this
   repository's `main` branch and the `terraform-apply` GitHub environment.
   The role needs Terraform plan/apply permissions for the S3 bucket, Glue
   database, and Athena workgroup. For the S3 backend it also needs access to
   the state object and its `.tflock` file.
3. In GitHub repository settings, configure the `terraform-apply` environment
   to allow deployments only from the `main` branch. The workflow uses this
   environment for its manually dispatched apply job.
4. Optionally add repository **variables**:
   - `TF_DATA_BUCKET`: only if overriding the default `legacy-migration-lab`
   - `AWS_REGION`: only if overriding the default `ca-central-1`
   - `ATHENA_WORKGROUP`: only if overriding the default `legacy-migration-lab`.
     Terraform and the data pipeline both use this value.
5. Add the repository **secret**:
   - `AWS_ROLE_ARN`: ARN of the OIDC role

Do not store long-lived AWS access keys in GitHub. Restrict the IAM trust policy
to this repository and the expected branch/environment subjects.

For this repository, the trust policy's `token.actions.githubusercontent.com:sub`
condition should allow only these subjects:

```text
repo:schammass@110421078/aws-legacy-data-migration-lab@1408967298:ref:refs/heads/main
repo:schammass@110421078/aws-legacy-data-migration-lab@1408967298:environment:terraform-apply
```

Also restrict the token audience to `sts.amazonaws.com`. The role must be able
to list the bucket and read/write/delete the state object and its `.tflock`
file. Since the same bucket holds project data, review the generated plan
carefully before applying it. Terraform manages bucket encryption, versioning,
and public-access settings. Existing lifecycle rules are intentionally not
managed by this configuration.

### Athena pipeline permissions

The `Athena data pipeline` workflow uses the same OIDC role and the
`terraform-apply` environment. Add these permissions to the role before running
that workflow:

- S3 bucket-level: `s3:ListBucket`, `s3:GetBucketLocation`, and
  `s3:ListBucketMultipartUploads` for the configured data bucket. Scope
  `s3:ListBucket` to the `data/*`, `curated/customer_golden/*`, and
  `athena-results/*` prefixes as well as the Terraform state prefix.
- S3 object-level: `s3:GetObject` for `data/*` and
  `curated/customer_golden/*`; `s3:PutObject` for `data/*`,
  `curated/customer_golden/*`, and `athena-results/*`; and
  `s3:AbortMultipartUpload` plus `s3:ListMultipartUploadParts` for those
  object prefixes.
- Athena: `athena:StartQueryExecution`, `athena:GetQueryExecution`,
  `athena:GetQueryResults`, and `athena:GetWorkGroup`.
- Glue: `glue:GetDatabase`, `glue:GetTable`, and `glue:CreateTable` for the
  configured database and its tables.

Keep the data workflow limited to this repository's `main` branch and the
`terraform-apply` environment. Do not grant it permission to delete S3 objects:
the pipeline intentionally stops if the curated table or output prefix already
exists, and it never performs a refresh or cleanup.

## Workflow

- Pull requests run `terraform fmt` and `terraform validate`; they do not use
  AWS credentials.
- A push to `main` creates and stores a Terraform plan as a short-lived
  workflow artifact. Review the plan in the run log before applying it.
- To apply, manually dispatch the workflow on `main` and provide the run ID
  of the successful push that generated the reviewed plan. The workflow checks
  that the plan run succeeded on the current `main` commit, downloads that
  run's artifact, and applies the saved plan. A new commit requires a new plan.
- Pushes do not apply infrastructure automatically. The `terraform-apply`
  environment is restricted to `main`; it does not replace review of the plan.
- The Athena workgroup does not enforce its result configuration, allowing the
  CTAS query to write to `curated/customer_golden/`. Queries can override the
  workgroup's default result location and encryption settings.
- The separate `Athena data pipeline` workflow validates CSV headers, row
  widths, configuration, and SQL on pull requests. To upload the source CSVs,
  create the external tables, run CTAS, and execute data checks, manually
  dispatch it on `main` and explicitly choose `yes`.
- Before upload, the pipeline verifies the database and workgroup exist, the
  target Glue table does not exist, existing raw tables match their configured
  location and schema, and the curated S3 prefix is empty. It rejects
  unexpected existing source objects, uploads only CSV files, and never deletes
  or refreshes existing data. A failed CTAS that leaves output objects behind
  requires manual investigation before another run.
- Successful data processing requires non-empty output, both `MASTER` and
  `CRM` records, and non-null golden IDs and normalized emails.

The state backend uses S3 native state locking (`use_lockfile`). Terraform
version 1.10 or newer is required. The data bucket has `prevent_destroy` as a
safeguard; removing it requires an intentional configuration change. Before
the first remote plan, Terraform must be initialized against this existing
bucket and the import of `aws_s3_bucket.data` must appear in the plan.

## Local validation

```sh
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform fmt -check
terraform -chdir=terraform validate
VALIDATE_ONLY=true TF_DATA_BUCKET=legacy-migration-lab python scripts/run_athena_pipeline.py
python -m unittest discover -s tests
```

# Notes

Changes to any file within terraform/, including documentation, trigger validation and the generation of a new plan upon pushing to main.