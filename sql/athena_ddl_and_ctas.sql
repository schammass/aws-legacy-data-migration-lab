-- Database Setup
CREATE DATABASE IF NOT EXISTS legacy_migration_lab;

-- 1. Raw External Table: Master Customers
CREATE EXTERNAL TABLE IF NOT EXISTS legacy_migration_lab.raw_customers_master (
  customer_id string,
  full_name string,
  email string,
  phone string,
  address string,
  city string,
  province string,
  postal_code string,
  updated_at string
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://legacy-migration-lab/data/master/'
TBLPROPERTIES ('skip.header.line.count'='1');

-- 2. Raw External Table: CRM Customers
CREATE EXTERNAL TABLE IF NOT EXISTS legacy_migration_lab.raw_customers_crm (
  crm_customer_id string,
  customer_name string,
  email_address string,
  mobile string,
  street string,
  municipality string,
  prov string,
  zip string,
  last_modified string
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://legacy-migration-lab/data/crm/'
TBLPROPERTIES ('skip.header.line.count'='1');

-- 3. Raw External Table: Customer Addresses
CREATE EXTERNAL TABLE IF NOT EXISTS legacy_migration_lab.raw_customer_addresses (
  customer_id string,
  address_type string,
  street string,
  city string,
  province string,
  postal_code string,
  effective_from string
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://legacy-migration-lab/data/addresses/'
TBLPROPERTIES ('skip.header.line.count'='1');

-- 4. CTAS Transformation: Generate Curated Parquet "Golden" Table
CREATE TABLE legacy_migration_lab.customer_golden
WITH (
  format = 'PARQUET',
  external_location = 's3://legacy-migration-lab/curated/customer_golden/'
) AS
SELECT 
    COALESCE(m.customer_id, c.crm_customer_id) AS golden_customer_id,
    TRIM(COALESCE(m.full_name, c.customer_name)) AS full_name,
    LOWER(TRIM(COALESCE(m.email, c.email_address))) AS normalized_email,
    REGEXP_REPLACE(COALESCE(m.phone, c.mobile), '[^0-9]', '') AS normalized_phone,
    COALESCE(a.street, m.address, c.street) AS address,
    COALESCE(a.city, m.city, c.municipality) AS city,
    COALESCE(a.province, m.province, c.prov) AS province,
    REGEXP_REPLACE(COALESCE(a.postal_code, m.postal_code, c.zip), '[^a-zA-Z0-9]', '') AS normalized_postal_code,
    CASE 
        WHEN m.customer_id IS NOT NULL THEN 'MASTER'
        ELSE 'CRM'
    END AS source_system,
    COALESCE(m.customer_id, c.crm_customer_id) AS source_customer_id,
    CAST(CURRENT_TIMESTAMP AS TIMESTAMP) AS source_updated_at,
    CURRENT_DATE AS valid_from,
    CAST(NULL AS DATE) AS valid_to,
    TRUE AS is_current,
    CAST(CURRENT_TIMESTAMP AS TIMESTAMP) AS processed_at
FROM legacy_migration_lab.raw_customers_master m
FULL OUTER JOIN legacy_migration_lab.raw_customers_crm c 
    ON LOWER(TRIM(m.email)) = LOWER(TRIM(c.email_address))
LEFT JOIN legacy_migration_lab.raw_customer_addresses a 
    ON m.customer_id = a.customer_id;