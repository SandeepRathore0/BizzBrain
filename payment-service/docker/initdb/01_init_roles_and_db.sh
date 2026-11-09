#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'payment_migrator') THEN
            CREATE ROLE payment_migrator WITH LOGIN PASSWORD '${PAYMENT_MIGRATOR_PASSWORD:-migrator_secure_pass_123}' CREATEDB;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'payment_app') THEN
            CREATE ROLE payment_app WITH LOGIN PASSWORD '${PAYMENT_APP_PASSWORD:-app_runtime_secure_pass_123}';
        END IF;
    END
    \$\$;

    GRANT ALL PRIVILEGES ON DATABASE payment_db TO payment_migrator;
    GRANT CONNECT ON DATABASE payment_db TO payment_app;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "payment_db" <<-EOSQL
    GRANT USAGE, CREATE ON SCHEMA public TO payment_migrator;
    GRANT USAGE ON SCHEMA public TO payment_app;

    ALTER DEFAULT PRIVILEGES FOR ROLE payment_migrator IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE ON TABLES TO payment_app;

    ALTER DEFAULT PRIVILEGES FOR ROLE payment_migrator IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES TO payment_app;

    ALTER DEFAULT PRIVILEGES FOR ROLE payment_migrator IN SCHEMA public
        REVOKE DELETE, TRUNCATE ON TABLES FROM payment_app;
EOSQL
