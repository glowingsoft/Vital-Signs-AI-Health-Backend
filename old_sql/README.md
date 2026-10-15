# Old SQL scripts (archive, not used)

The database used to be built by these hand-written scripts. They are kept for reference only.

- `01_roles_and_database.sql`: logins and database. Still in use as **`db/bootstrap.sql`**, because Alembic cannot create the database it connects to.
- `02_schema_and_tables.sql`: the `ai` schema and tables. **Replaced by** the SQLAlchemy models in `collection/` and the Alembic migrations in `alembic/versions/`. The first migration recreates exactly this schema.

Do not edit these files to change the database. See "Database models and migrations" in the README.
