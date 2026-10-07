#!/usr/bin/env python3
"""Upload the lab CSVs, create Athena tables, run CTAS, and check the output."""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal, overload
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEADERS = {
    "master": [
        "customer_id",
        "full_name",
        "email",
        "phone",
        "address",
        "city",
        "province",
        "postal_code",
        "updated_at",
    ],
    "crm": [
        "crm_customer_id",
        "customer_name",
        "email_address",
        "mobile",
        "street",
        "municipality",
        "prov",
        "zip",
        "last_modified",
    ],
    "addresses": [
        "customer_id",
        "address_type",
        "street",
        "city",
        "province",
        "postal_code",
        "effective_from",
    ],
}
AWS_REGION = os.environ.get("AWS_REGION", "ca-central-1")
DATA_BUCKET = os.environ.get("TF_DATA_BUCKET", "legacy-migration-lab")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "legacy-migration-lab")
QUERY_TIMEOUT_SECONDS = 1800


def load_config() -> dict[str, Any]:
    with (ROOT / "config" / "source_config.json").open(encoding="utf-8") as config_file:
        return json.load(config_file)


@overload
def run_aws_json(
    *arguments: str,
    allow_failure: Literal[False] = False,
) -> tuple[dict[str, Any], str]: ...


@overload
def run_aws_json(
    *arguments: str,
    allow_failure: Literal[True],
) -> tuple[dict[str, Any] | None, str]: ...


def run_aws_json(
    *arguments: str,
    allow_failure: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    result = subprocess.run(
        ["aws", *arguments, "--region", AWS_REGION, "--no-cli-pager", "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        if allow_failure:
            return None, result.stderr
        raise RuntimeError(f"AWS CLI command failed: {result.stderr.strip()}")
    return json.loads(result.stdout), ""


def validate_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    in_line_comment = False
    index = 0

    while index < len(sql_text):
        char = sql_text[index]
        next_char = sql_text[index + 1] if index + 1 < len(sql_text) else ""

        if in_line_comment:
            current.append(char)
            if char == "\n":
                in_line_comment = False
        elif in_string:
            current.append(char)
            if char == "'" and next_char == "'":
                current.append(next_char)
                index += 1
            elif char == "'":
                in_string = False
        elif char == "-" and next_char == "-":
            current.extend((char, next_char))
            index += 1
            in_line_comment = True
        elif char == "'":
            current.append(char)
            in_string = True
        elif char == ";":
            statement = "".join(current).strip()
            if statement and re.sub(r"--[^\n]*", "", statement).strip():
                statements.append(statement)
            current = []
        else:
            current.append(char)
        index += 1

    remainder = "".join(current).strip()
    if remainder and re.sub(r"--[^\n]*", "", remainder).strip():
        raise ValueError("SQL file contains a statement without a terminating semicolon.")
    if in_string:
        raise ValueError("SQL file contains an unterminated string literal.")
    return statements


def render_sql(sql_text: str, database_name: str, bucket_name: str) -> str:
    rendered = sql_text.replace("s3://legacy-migration-lab/", f"s3://{bucket_name}/")
    return re.sub(r"\blegacy_migration_lab\b", database_name, rendered)


def validate_inputs(config: dict[str, Any]) -> tuple[list[Path], list[str], list[str]]:
    database_name = config["database_name"]
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,254}", database_name):
        raise ValueError(f"Invalid Glue database name: {database_name}")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", ATHENA_WORKGROUP):
        raise ValueError(f"Invalid Athena workgroup name: {ATHENA_WORKGROUP}")
    configured_bucket = config["s3_bucket"]
    for bucket_name in (configured_bucket, DATA_BUCKET):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket_name):
            raise ValueError(f"Invalid S3 bucket name: {bucket_name}")

    source_files: list[Path] = []
    if set(config["sources"]) != set(SOURCE_HEADERS):
        raise ValueError(
            f"Source configuration must define exactly: {', '.join(SOURCE_HEADERS)}."
        )
    for source_name, expected_header in SOURCE_HEADERS.items():
        source = config["sources"].get(source_name)
        if source is None:
            raise ValueError(f"Missing source configuration: {source_name}")
        prefix = urlparse(source["s3_prefix"])
        if prefix.scheme != "s3" or prefix.netloc != configured_bucket:
            raise ValueError(
                f"Source prefix for {source_name} must use config bucket {configured_bucket}."
            )
        if prefix.path.lstrip("/") != f"data/{source_name}/":
            raise ValueError(f"Unexpected S3 prefix for source {source_name}: {source['s3_prefix']}")
        source["s3_prefix"] = f"s3://{DATA_BUCKET}/{prefix.path.lstrip('/')}"

        source_dir = ROOT / "data" / source_name
        csv_files = sorted(source_dir.glob("*.csv"))
        if not csv_files:
            raise ValueError(f"No CSV files found for source {source_name}: {source_dir}")
        for csv_file in csv_files:
            with csv_file.open(encoding="utf-8-sig", newline="") as input_file:
                reader = csv.reader(input_file)
                header = next(reader, None)
                if header != expected_header:
                    raise ValueError(f"Unexpected CSV header in {csv_file.relative_to(ROOT)}")
                row_count = 0
                for line_number, row in enumerate(reader, start=2):
                    if not row:
                        continue
                    if len(row) != len(expected_header):
                        raise ValueError(
                            f"Invalid column count in {csv_file.relative_to(ROOT)}:{line_number}"
                        )
                    row_count += 1
                if row_count == 0:
                    raise ValueError(f"CSV contains no data rows: {csv_file.relative_to(ROOT)}")
            source_files.append(csv_file)

    target = config["curated_target"]
    target_prefix = urlparse(target["s3_prefix"])
    if (
        target_prefix.scheme != "s3"
        or target_prefix.netloc != configured_bucket
        or target_prefix.path.lstrip("/") != "curated/customer_golden/"
        or target["format"].upper() != "PARQUET"
    ):
        raise ValueError("Curated target must be the configured Parquet prefix in the data bucket.")
    target["s3_prefix"] = f"s3://{DATA_BUCKET}/{target_prefix.path.lstrip('/')}"

    ddl_sql = render_sql(
        (ROOT / "sql" / "athena_ddl_and_ctas.sql").read_text(encoding="utf-8"),
        database_name,
        DATA_BUCKET,
    )
    check_sql = render_sql(
        (ROOT / "sql" / "athena_checks.sql").read_text(encoding="utf-8"),
        database_name,
        DATA_BUCKET,
    )
    ddl_statements = validate_sql_statements(ddl_sql)
    check_statements = validate_sql_statements(check_sql)
    if len(ddl_statements) != 5:
        raise ValueError(f"Expected 5 DDL/CTAS statements, found {len(ddl_statements)}.")
    if len(check_statements) != 3:
        raise ValueError(f"Expected 3 Athena check statements, found {len(check_statements)}.")
    if not any(
        f"external_location = '{target['s3_prefix']}'" in statement
        for statement in ddl_statements
    ):
        raise ValueError("CTAS output location does not match config/source_config.json.")
    for source_name, source in config["sources"].items():
        if source["s3_prefix"] not in ddl_sql:
            raise ValueError(f"DDL is missing the configured S3 prefix for {source_name}.")
        if source["table_name"] not in ddl_sql:
            raise ValueError(f"DDL is missing the configured table for {source_name}.")

    return source_files, ddl_statements, check_statements


def assert_not_found(error: str, resource: str) -> None:
    if "EntityNotFoundException" in error or "ResourceNotFoundException" in error:
        return
    raise RuntimeError(f"Could not check {resource}: {error.strip()}")


def validate_existing_source_table(
    table: dict[str, Any],
    source_name: str,
    source_config: dict[str, str],
) -> None:
    table_data = table["Table"]
    location = table_data.get("StorageDescriptor", {}).get("Location")
    columns = [
        column["Name"]
        for column in table_data.get("StorageDescriptor", {}).get("Columns", [])
    ]
    expected_location = source_config["s3_prefix"]
    if (
        not isinstance(location, str)
        or location.rstrip("/") != expected_location.rstrip("/")
        or columns != SOURCE_HEADERS[source_name]
    ):
        raise RuntimeError(
            f"Existing raw table {source_config['table_name']} does not match "
            f"the configured location/schema. Expected {expected_location}."
        )


def preflight(config: dict[str, Any], source_files: list[Path]) -> None:
    database_name = config["database_name"]
    target_table = config["curated_target"]["table_name"]
    run_aws_json("glue", "get-database", "--name", database_name)

    workgroup, _ = run_aws_json(
        "athena", "get-work-group", "--work-group", ATHENA_WORKGROUP
    )
    if workgroup["WorkGroup"]["State"] != "ENABLED":
        raise RuntimeError(f"Athena workgroup is not enabled: {ATHENA_WORKGROUP}")

    for source_name, source_config in config["sources"].items():
        raw_table, error = run_aws_json(
            "glue",
            "get-table",
            "--database-name",
            database_name,
            "--name",
            source_config["table_name"],
            allow_failure=True,
        )
        if raw_table is None:
            assert_not_found(error, f"Glue table {database_name}.{source_config['table_name']}")
        else:
            validate_existing_source_table(raw_table, source_name, source_config)

    table, error = run_aws_json(
        "glue",
        "get-table",
        "--database-name",
        database_name,
        "--name",
        target_table,
        allow_failure=True,
    )
    if table is not None:
        raise RuntimeError(
            f"Curated table already exists: {database_name}.{target_table}. "
            "The pipeline will not overwrite or delete existing output."
        )
    assert_not_found(error, f"Glue table {database_name}.{target_table}")

    target_prefix = config["curated_target"]["s3_prefix"].removeprefix(
        f"s3://{DATA_BUCKET}/"
    )
    output, _ = run_aws_json(
        "s3api",
        "list-objects-v2",
        "--bucket",
        DATA_BUCKET,
        "--prefix",
        target_prefix,
        "--max-keys",
        "1",
    )
    if output.get("Contents"):
        raise RuntimeError(
            f"Curated output prefix is not empty: s3://{DATA_BUCKET}/{target_prefix}. "
            "The pipeline will not delete existing objects."
        )

    expected_keys: dict[str, set[str]] = {}
    for source_file in source_files:
        source_name = source_file.parent.name
        expected_keys.setdefault(source_name, set()).add(
            f"data/{source_name}/{source_file.name}"
        )
    for source_name, expected in expected_keys.items():
        existing, _ = run_aws_json(
            "s3api",
            "list-objects-v2",
            "--bucket",
            DATA_BUCKET,
            "--prefix",
            f"data/{source_name}/",
        )
        actual = {
            item["Key"]
            for item in existing.get("Contents", [])
            if item["Key"] != f"data/{source_name}/"
        }
        stale_keys = sorted(actual - expected)
        if stale_keys:
            raise RuntimeError(
                f"Unexpected existing objects in data/{source_name}/: "
                f"{', '.join(stale_keys)}. Remove or review them manually before retrying."
            )


def upload_sources(source_files: list[Path]) -> None:
    for source_name in SOURCE_HEADERS:
        source_dir = ROOT / "data" / source_name
        destination = f"s3://{DATA_BUCKET}/data/{source_name}/"
        subprocess.run(
            [
                "aws",
                "s3",
                "cp",
                str(source_dir) + "/",
                destination,
                "--recursive",
                "--exclude",
                "*",
                "--include",
                "*.csv",
                "--region",
                AWS_REGION,
                "--no-cli-pager",
            ],
            check=True,
        )


def start_athena_query(
    statement: str,
    database_name: str,
    *,
    fetch_results: bool = True,
) -> list[list[str]]:
    arguments = [
        "athena",
        "start-query-execution",
        "--query-string",
        statement,
        "--work-group",
        ATHENA_WORKGROUP,
    ]
    if not statement.lstrip().upper().startswith("CREATE DATABASE"):
        arguments.extend(["--query-execution-context", f"Database={database_name}"])
    started, _ = run_aws_json(*arguments)
    query_id = started["QueryExecutionId"]
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        execution, _ = run_aws_json(
            "athena",
            "get-query-execution",
            "--query-execution-id",
            query_id,
        )
        status = execution["QueryExecution"]["Status"]
        state = status["State"]
        if state == "SUCCEEDED":
            if not fetch_results:
                return []
            results, _ = run_aws_json(
                "athena",
                "get-query-results",
                "--query-execution-id",
                query_id,
                "--max-results",
                "1000",
            )
            rows = results["ResultSet"]["Rows"]
            return [
                [cell.get("VarCharValue", "") for cell in row.get("Data", [])]
                for row in rows
            ]
        if state in {"FAILED", "CANCELLED"}:
            raise RuntimeError(
                f"Athena query {query_id} {state}: "
                f"{status.get('StateChangeReason', 'No failure reason returned.')}"
            )
        time.sleep(5)

    raise TimeoutError(
        f"Athena query did not finish within {QUERY_TIMEOUT_SECONDS} seconds: {query_id}"
    )


def print_results(label: str, rows: list[list[str]]) -> None:
    print(f"\n{label}")
    for row in rows:
        print(" | ".join(row))


def run_quality_checks(check_statements: list[str], database_name: str) -> None:
    preview_rows = start_athena_query(check_statements[0], database_name)
    print_results("Golden table preview", preview_rows)

    source_rows = start_athena_query(check_statements[1], database_name)
    print_results("Source breakdown", source_rows)
    source_counts = {
        row[0]: int(row[1])
        for row in source_rows[1:]
        if len(row) >= 2 and row[0]
    }
    if not source_counts or sum(source_counts.values()) == 0:
        raise RuntimeError("Data quality check failed: the curated table has no records.")
    if not {"MASTER", "CRM"}.issubset(source_counts):
        raise RuntimeError("Data quality check failed: expected MASTER and CRM source records.")

    metric_rows = start_athena_query(check_statements[2], database_name)
    print_results("Key-field completeness", metric_rows)
    if len(metric_rows) < 2 or len(metric_rows[1]) < 3:
        raise RuntimeError("Data quality check returned an unexpected result shape.")
    total_records, valid_ids, valid_emails = map(int, metric_rows[1][:3])
    if total_records == 0 or valid_ids != total_records or valid_emails != total_records:
        raise RuntimeError(
            "Data quality check failed: expected non-empty output with no null "
            "golden_customer_id or normalized_email values."
        )


def main() -> int:
    try:
        config = load_config()
        source_files, ddl_statements, check_statements = validate_inputs(config)
        print(
            f"Validated {len(source_files)} CSV file(s), "
            f"{len(ddl_statements)} DDL/CTAS statement(s), and "
            f"{len(check_statements)} check query(ies)."
        )
        if os.environ.get("VALIDATE_ONLY") == "true":
            return 0

        preflight(config, source_files)
        upload_sources(source_files)

        for statement in ddl_statements:
            start_athena_query(
                statement,
                config["database_name"],
                fetch_results=False,
            )

        run_quality_checks(check_statements, config["database_name"])
        print("\nAthena data pipeline completed successfully.")
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        csv.Error,
        RuntimeError,
        TimeoutError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
