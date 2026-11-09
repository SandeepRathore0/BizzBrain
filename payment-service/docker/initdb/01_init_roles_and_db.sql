-- 01_init_roles_and_db.sql
-- Role and Database Initialization for BizBrain Payment Service

-- 1. Create Roles
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'payment_migrator') THEN
        CREATE ROLE payment_migrator WITH LOGIN PASSWORD 'migrator_secure_pass_123' CREATEDB;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'payment_app') THEN
        CREATE ROLE payment_app WITH LOGIN PASSWORD 'app_runtime_secure_pass_123';
    END IF;
END
$$;

-- 2. Databases (Created if not existing via script runner or docker-entrypoint)
-- In Docker entrypoint, POSTGRES_DB is often payment_db.
-- This script ensures roles and default privileges are set.

GRANT ALL PRIVILEGES ON DATABASE payment_db TO payment_migrator;
GRANT CONNECT ON DATABASE payment_db TO payment_app;

\connect payment_db

-- Grant schema usage to payment_app
GRANT USAGE, CREATE ON SCHEMA public TO payment_migrator;
GRANT USAGE ON SCHEMA public TO payment_app;

-- Default privileges for future tables created by payment_migrator
ALTER DEFAULT PRIVILEGES FOR ROLE payment_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO payment_app;

ALTER DEFAULT PRIVILEGES FOR ROLE payment_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO payment_app;

-- Prohibit DELETE and TRUNCATE for payment_app on all future tables
ALTER DEFAULT PRIVILEGES FOR ROLE payment_migrator IN SCHEMA public
    REVOKE DELETE, TRUNCATE ON TABLES FROM payment_app;
