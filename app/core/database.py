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
    # Models must be imported before create_all so their tables are registered
    # in Base.metadata. Without this, create_all is a silent no-op.
    import app.models.user       # noqa: F401
    import app.models.workspace  # noqa: F401
    import app.models.endpoint   # noqa: F401
    import app.models.route      # noqa: F401
    import app.models.event      # noqa: F401
    import app.models.delivery_attempt  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Zero-downtime schema migrations for new columns.
        # ADD COLUMN IF NOT EXISTS is idempotent — safe to run on every startup.
        for stmt in [
            "ALTER TABLE routes ADD COLUMN IF NOT EXISTS filter_expression TEXT;",
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS request_headers JSONB;",
            "ALTER TABLE endpoints ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces(id);",
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'pending';",
            "ALTER TABLE events ADD COLUMN IF NOT EXISTS retry_at TIMESTAMPTZ;",
        ]:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # column already exists — safe to ignore
