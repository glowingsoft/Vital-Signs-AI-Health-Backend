# Alembic's entry point: connects to Postgres and compares the models in collection/ with the database.
# Database URL: DATABASE_URL if set, otherwise built from the DB_* values (both may come from .env).

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import URL, make_url

from alembic import context
from app import config as app_config  # importing it loads .env into os.environ
from collection import Base  # importing "collection" loads every model
from collection.base import SCHEMA

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> URL:
    url = os.environ.get("DATABASE_URL")
    if url:
        url = make_url(url)
        # Alembic uses the sync psycopg driver, even when the URL names asyncpg or no driver.
        return url.set(drivername="postgresql+psycopg")
    return URL.create(
        "postgresql+psycopg",
        username=app_config.DB_APP_USER,
        password=app_config.DB_APP_PASSWORD,  # URL.create escapes special characters
        host=app_config.DB_HOST,
        port=app_config.DB_PORT,
        database=app_config.DB_NAME,
    )


def include_name(name, type_, parent_names) -> bool:
    # Only look at the "ai" schema; Node or extensions may own other schemas in the same database.
    if type_ == "schema":
        return name == SCHEMA
    return True


CONTEXT_OPTIONS = dict(
    target_metadata=target_metadata,
    include_schemas=True,
    include_name=include_name,
    version_table_schema=SCHEMA,  # ai.alembic_version, next to the tables it describes
    compare_type=True,  # detect column type changes
    compare_server_default=True,  # detect DEFAULT changes
)


def run_migrations_offline() -> None:
    """'alembic upgrade head --sql': print the SQL instead of running it."""
    context.configure(
        url=database_url().render_as_string(hide_password=False),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONTEXT_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # The version table lives in "ai", so the schema must exist before Alembic creates that table.
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()

        context.configure(connection=connection, **CONTEXT_OPTIONS)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
