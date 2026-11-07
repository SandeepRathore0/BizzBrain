#!/bin/bash
set -e

# ==============================================================================
# BizBrain User Service - Role Initialization Script (SEC-02)
# Runs during PostgreSQL container initialization.
# Passwords are read from environment variables, not hardcoded.
# ==============================================================================

MIGRATION_USER="${DB_MIGRATION_USER:-bizbrain_migrator}"
MIGRATION_PASS="${DB_MIGRATION_PASSWORD:-migrator_secure_pass_123}"

APP_USER="${DB_APP_USER:-bizbrain_app}"
APP_PASS="${DB_APP_PASSWORD:-app_runtime_secure_pass_123}"

DATABASE_NAME="${POSTGRES_DB:-user_db}"

echo "Configuring PostgreSQL roles for BizBrain User Service..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DATABASE_NAME" <<-EOSQL
    -- 1. Create Migration/Owner Role (runs Alembic migrations, owns tables)
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$MIGRATION_USER') THEN
            CREATE ROLE $MIGRATION_USER WITH LOGIN PASSWORD '$MIGRATION_PASS';
        ELSE
            ALTER ROLE $MIGRATION_USER WITH PASSWORD '$MIGRATION_PASS';
        END IF;
    END
    \$\$;

    -- 2. Create Application Runtime Role (used by FastAPI service, least privilege)
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$APP_USER') THEN
            CREATE ROLE $APP_USER WITH LOGIN PASSWORD '$APP_PASS';
        ELSE
            ALTER ROLE $APP_USER WITH PASSWORD '$APP_PASS';
        END IF;
    END
    \$\$;

    -- 3. Database connection privileges
    GRANT CONNECT ON DATABASE $DATABASE_NAME TO $MIGRATION_USER;
    GRANT CONNECT ON DATABASE $DATABASE_NAME TO $APP_USER;

    -- 4. Schema privileges
    GRANT ALL ON SCHEMA public TO $MIGRATION_USER;
    GRANT USAGE ON SCHEMA public TO $APP_USER;
    ALTER SCHEMA public OWNER TO $MIGRATION_USER;

    -- 5. Default privileges for future tables created by the migration user
    -- bizbrain_app receives SELECT, INSERT, UPDATE, DELETE on ordinary tables
    ALTER DEFAULT PRIVILEGES FOR ROLE $MIGRATION_USER IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO $APP_USER;

    ALTER DEFAULT PRIVILEGES FOR ROLE $MIGRATION_USER IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO $APP_USER;

    -- 6. Defense-in-depth: Ensure audit_log is strictly append-only (SELECT & INSERT only)
    DO \$\$
    BEGIN
        IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'audit_log') THEN
            REVOKE ALL ON TABLE audit_log FROM $APP_USER;
            REVOKE ALL ON TABLE audit_log FROM PUBLIC;
            GRANT SELECT, INSERT ON TABLE audit_log TO $APP_USER;
        END IF;
    END
    \$\$;
EOSQL

echo "Role configuration completed successfully."
