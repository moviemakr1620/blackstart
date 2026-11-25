from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"timeout": 30},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


# Columns added after the first release. create_all() does not alter existing
# tables, so add them one by one; "duplicate column" errors mean already done.
_ADDED_COLUMNS = [
    ("policies", "ups_source_id INTEGER"),
    ("episode_actions", "host_id INTEGER"),
    ("users", "must_change_password BOOLEAN NOT NULL DEFAULT 0"),
    ("users", "token_version INTEGER NOT NULL DEFAULT 0"),
    ("notify_channels", "url_enc TEXT NOT NULL DEFAULT ''"),
    ("episodes", "fired_json TEXT NOT NULL DEFAULT '[]'"),
]


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        existing: dict[str, set[str]] = {}
        for table, ddl in _ADDED_COLUMNS:
            if table not in existing:
                rows = (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()
                existing[table] = {r[1] for r in rows}
            column = ddl.split()[0]
            if column not in existing[table]:
                await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {ddl}")
                existing[table].add(column)
