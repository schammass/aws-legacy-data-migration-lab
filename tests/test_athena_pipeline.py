import unittest

from scripts.run_athena_pipeline import (
    load_config,
    render_sql,
    SOURCE_HEADERS,
    validate_existing_source_table,
    validate_inputs,
    validate_sql_statements,
)


class AthenaPipelineSqlTests(unittest.TestCase):
    def test_split_statements_ignores_semicolons_in_strings_and_comments(self):
        statements = validate_sql_statements(
            "SELECT 'a;b'; -- comment ;\nSELECT 2;"
        )

        self.assertEqual(len(statements), 2)
        self.assertIn("'a;b'", statements[0])
        self.assertIn("SELECT 2", statements[1])

    def test_unterminated_statement_fails(self):
        with self.assertRaisesRegex(ValueError, "terminating semicolon"):
            validate_sql_statements("SELECT 1")

    def test_render_sql_uses_configured_database_and_bucket(self):
        rendered = render_sql(
            "CREATE TABLE legacy_migration_lab.example "
            "LOCATION 's3://legacy-migration-lab/data/';",
            "poc_database",
            "poc-data-bucket",
        )

        self.assertIn("poc_database.example", rendered)
        self.assertIn("s3://poc-data-bucket/data/", rendered)

    def test_repository_inputs_validate(self):
        source_files, ddl_statements, check_statements = validate_inputs(load_config())

        self.assertEqual(len(source_files), 3)
        self.assertEqual(len(ddl_statements), 5)
        self.assertEqual(len(check_statements), 3)

    def test_existing_raw_table_accepts_location_without_trailing_slash(self):
        source_name = "master"
        source_config = {
            "table_name": "raw_customers_master",
            "s3_prefix": "s3://legacy-migration-lab/data/master/",
        }
        table = {
            "Table": {
                "StorageDescriptor": {
                    "Location": "s3://legacy-migration-lab/data/master",
                    "Columns": [
                        {"Name": name} for name in SOURCE_HEADERS[source_name]
                    ],
                }
            }
        }

        validate_existing_source_table(table, source_name, source_config)


if __name__ == "__main__":
    unittest.main()
