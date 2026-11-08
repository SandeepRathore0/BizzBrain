-- Role initialization script for invoice_db
-- Run as postgres / superuser once during database cluster initialization

-- 1. Create migration/owner role if not exists
DO
$do$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'invoice_owner') THEN
      CREATE ROLE invoice_owner WITH LOGIN PASSWORD 'invoice_owner_pass' CREATEDB;
   END IF;
END
$do$;

-- 2. Create runtime restricted role if not exists
DO
$do$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'invoice_app') THEN
      CREATE ROLE invoice_app WITH LOGIN PASSWORD 'invoice_app_pass' NOSUPERUSER NOCREATEDB NOCREATEROLE;
   END IF;
END
$do$;

-- 3. Grant schema usage and ensure invoice_owner owns public schema
ALTER SCHEMA public OWNER TO invoice_owner;
GRANT USAGE, CREATE ON SCHEMA public TO invoice_owner;
GRANT USAGE ON SCHEMA public TO invoice_app;
