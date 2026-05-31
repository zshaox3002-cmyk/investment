# SQL Migrations

SQL migration files for the investment portfolio database. Executed by `src/investment/core/sql_migrator.py` via `inv migrate run`.

## Naming Convention

```
NN_description.sql
```

- `NN`: two-digit sequence number (09, 10, 11, ...)
- `description`: short snake_case label

## Rules

1. All statements must be idempotent (`CREATE TABLE IF NOT EXISTS`, `INSERT OR IGNORE`, etc.)
2. Each file is a self-contained unit — do not depend on ordering within a file
3. Files are executed in lexical order by filename
4. Once applied, a file is tracked by SHA256 checksum in `sql_schema_migrations`
5. Modifying an already-applied file requires `--force` to re-run
6. DDL statements only — data seeding belongs in Python migration scripts

## Applied Files

| File | Description |
|------|-------------|
| 09_causal_schema.sql | Causal graph nodes, edges, and views |
