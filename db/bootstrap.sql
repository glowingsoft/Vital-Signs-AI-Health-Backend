-- Bootstrap, run ONCE as the ADMIN user (connected to the "postgres" database), BEFORE "alembic upgrade head".
-- Creates the logins (AI module + Node) and the "vitalsigns" database. Alembic cannot do this part:
-- it needs the database (and its login) to exist before it can connect. Everything inside the database
-- (schema, tables, triggers, grants) is created by the Alembic migrations in alembic/versions/.
-- Used by setup_db.sh and docker/postgres-init.sh. Safe to run many times.
-- Requires PostgreSQL 16 (local and AWS RDS).

\set ON_ERROR_STOP on

-- AI module login: create it, or update its password if it already exists.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec

SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec

-- Node backend login (only when a Node user was given).
\if :has_node
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'node_user', :'node_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'node_user') \gexec

SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'node_user', :'node_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'node_user') \gexec
\endif

-- On AWS RDS the admin is not a full superuser. To create a database owned by the
-- AI login, the admin must be allowed to act as that login. Locally this is skipped.
SELECT format('GRANT %I TO %I WITH SET TRUE', :'app_user', current_user)
WHERE NOT pg_has_role(current_user, :'app_user', 'SET') \gexec

-- The database, owned by the AI login.
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') \gexec
