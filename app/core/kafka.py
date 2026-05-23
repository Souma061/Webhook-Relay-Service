import json
import logging

from aiokafka import AIOKafkaProducer

from app.core.config import settings

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None


async def init_kafka() -> AIOKafkaProducer:
    """Start the global Kafka producer. Call once during app lifespan startup."""
    global _producer
    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        # acks=all ensures the broker has written the message before ack-ing
        acks="all",
    )
    await _producer.start()
    logger.info("Kafka producer started (brokers=%s)", settings.kafka_bootstrap_servers)
    return _producer


async def close_kafka():
    """Flush pending messages and close the producer. Call during app lifespan shutdown."""
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None
        logger.info("Kafka producer closed")


def get_kafka() -> AIOKafkaProducer:
    """Return the running producer instance. Raises if not yet initialised."""
    if _producer is None:
        raise RuntimeError("Kafka producer not initialised — call init_kafka() first")
    return _producer
"""
kafka.py — Async Kafka producer lifecycle.

Manages a singleton AIOKafkaProducer for the app.
Used by the ingestion gateway to publish raw webhook events.

Export:
- init_kafka(): call during app startup (lifespan)
- close_kafka(): call during app shutdown (lifespan)
- get_kafka(): get the running producer (raises if not initialised)
"""
