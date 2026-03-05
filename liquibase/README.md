# Liquibase

This folder contains database migrations for Azure PostgreSQL.

- `changelog/db.changelog-master.yaml`: root changelog
- `changelog/001-create-users-table.sql`: initial users table

The `db-migrate.yml` workflow runs these migrations by reading DB secrets from Azure Key Vault.
