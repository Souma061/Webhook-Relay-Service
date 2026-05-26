from __future__ import annotations

import json
import logging

from aiokafka import AIOKafkaProducer

from app.core.config import settings

logger = logging.getLogger(__name__)

_producer: AIOKafkaProducer | None = None


async def init_kafka() -> AIOKafkaProducer | None:
    """Start the global Kafka producer.

    Returns the producer on success, or None if Kafka is unreachable.
    The app continues to serve webhooks either way — events are stored
    in PostgreSQL and can be replayed later when Kafka recovers.
    """
    global _producer
    _producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
    )
    try:
        await _producer.start()
    except Exception:
        logger.warning(
            "Kafka unreachable at %s — continuing without producer. "
            "Events will be stored in PostgreSQL only.",
            settings.kafka_bootstrap_servers,
        )
        try:
            await _producer.stop()
        except Exception:
            pass
        _producer = None
        return None

    logger.info("Kafka producer started (brokers=%s)", settings.kafka_bootstrap_servers)
    return _producer


async def close_kafka():
    global _producer
    if _producer:
        try:
            await _producer.stop()
        except Exception:
            logger.warning("Error stopping Kafka producer", exc_info=True)
        _producer = None
        logger.info("Kafka producer closed")


def get_kafka() -> AIOKafkaProducer | None:
    """Return the running producer, or None if Kafka is unavailable."""
    return _producer
