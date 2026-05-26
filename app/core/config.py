from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "webhook-relay"
    environment: str = "development"
    debug: bool = False
    cors_allowed_origins: str = ""

    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/webhook_relay"
    )
    redis_url: str = "redis://localhost:6379/0"

    max_delivery_attempts: int = 5
    retry_backoff_ms: int = 1000
    delivery_timeout_ms: int = 10000
    circuit_breaker_threshold: int = 10
    circuit_breaker_cooldown_s: int = 30
    rate_limit_rpm: int = 60
    idempotency_ttl_s: int = 86400
    max_webhook_body_bytes: int = 1024 * 1024
    allow_insecure_delivery_urls: bool = False
    auth_rate_limit_per_minute: int = 10

    # ── Kafka (Phase 2) ────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_raw_events: str = "raw-events"
    kafka_topic_transformed_events: str = "transformed-events"
    kafka_topic_dead_letter: str = "dead-letter"
    kafka_consumer_group_transform: str = "relay-transform-group"
    kafka_consumer_group_delivery: str = "relay-delivery-group"

    # ── Auth / JWT ─────────────────────────────────────────────────────────────
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440  # 24 hours
    password_pepper: str = ""

    model_config = {"env_prefix": "RELAY_", "env_file": ".env"}


settings = Settings()


def is_production() -> bool:
    return settings.environment.lower() in {"prod", "production"}


def validate_production_settings() -> None:
    if len(settings.jwt_secret_key) < 32:
        raise RuntimeError("RELAY_JWT_SECRET_KEY must be set to a strong value")
    if len(settings.password_pepper) < 32:
        raise RuntimeError("RELAY_PASSWORD_PEPPER must be set to a strong value")

    if not is_production():
        return

    if settings.allow_insecure_delivery_urls:
        raise RuntimeError("RELAY_ALLOW_INSECURE_DELIVERY_URLS must be false in production")
"""
config.py — Application configuration via environment variables.

Uses pydantic-settings to load config from env vars prefixed with RELAY_.
Fallback defaults work for local dev. Override via .env file or system env.

Key settings:
- database_url: PostgreSQL connection string (asyncpg driver)
- redis_url: Redis connection string
- max_delivery_attempts: how many times to retry a failed delivery
- retry_backoff_ms: base delay for exponential backoff
- rate_limit_rpm: max requests per minute per destination
"""
