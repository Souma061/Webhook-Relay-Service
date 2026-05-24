from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "webhook-relay"
    debug: bool = False

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

    # ── Kafka (Phase 2) ────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_raw_events: str = "raw-events"
    kafka_topic_transformed_events: str = "transformed-events"
    kafka_topic_dead_letter: str = "dead-letter"
    kafka_consumer_group_transform: str = "relay-transform-group"
    kafka_consumer_group_delivery: str = "relay-delivery-group"

    model_config = {"env_prefix": "RELAY_", "env_file": ".env"}


settings = Settings()
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
