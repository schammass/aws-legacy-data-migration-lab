-----------------------------------------------
-- Verification Query 1: Preview Golden Dataset
SELECT * 
FROM legacy_migration_lab.customer_golden 
LIMIT 10;

-- Verification Query 2: Check Deduplication and Source Breakdown
SELECT 
    source_system,
    COUNT(*) AS record_count
FROM legacy_migration_lab.customer_golden
GROUP BY source_system;

-- Verification Query 3: Check Null Values in Key Identifiers
SELECT 
    COUNT(*) AS total_records,
    COUNT(golden_customer_id) AS valid_ids,
    COUNT(normalized_email) AS valid_emails
FROM legacy_migration_lab.customer_golden;