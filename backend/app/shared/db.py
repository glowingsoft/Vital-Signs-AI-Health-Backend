# Async connection to Postgres with SQLAlchemy and the asyncpg driver.
# Gives one shared engine (connection pool) and a session factory for transactions.

from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app import config

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker] = None


def build_url() -> URL:
    # URL.create escapes special characters in the password for us.
    return URL.create(
        "postgresql+asyncpg",
        username=config.DB_APP_USER,
        password=config.DB_APP_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
    )


def get_engine() -> AsyncEngine:
    """The engine holds a pool of open connections, so each query does not reconnect."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            build_url(),
            pool_pre_ping=True,  # check a pooled connection still works before using it
            connect_args={"timeout": 2},  # if Postgres is down, fail after 2 s
        )
    return _engine


def get_session_factory() -> async_sessionmaker:
    """Call the result to get a session: async with get_session_factory()() as session: ..."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _session_factory


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()  # closes every pooled connection
        _engine = None
        _session_factory = None


async def ping() -> bool:
    """True if Postgres answers, False if it is down. Never raises."""
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
