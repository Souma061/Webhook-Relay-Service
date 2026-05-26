from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Zero-downtime schema migrations for new columns.
        # ADD COLUMN IF NOT EXISTS is idempotent — safe to run on every startup.
        await conn.execute(
            text("ALTER TABLE routes ADD COLUMN IF NOT EXISTS filter_expression TEXT;")
        )
        await conn.execute(
            text("ALTER TABLE events ADD COLUMN IF NOT EXISTS request_headers JSONB;")
        )
        await conn.execute(
            text("ALTER TABLE endpoints ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id);")
        )
        await conn.execute(
            text("ALTER TABLE events ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'pending';")
        )
        await conn.execute(
            text("ALTER TABLE events ADD COLUMN IF NOT EXISTS retry_at TIMESTAMPTZ;")
        )
"""
database.py — SQLAlchemy async engine and session factory.

Creates an async PostgreSQL engine using asyncpg driver.
Base class for all ORM models to inherit from.
init_db() creates all tables on startup (dev-friendly; use Alembic in prod).

Export:
- async_session_factory: callable that yields AsyncSession
- Base: declarative base for models
- init_db(): one-time table creation
"""
