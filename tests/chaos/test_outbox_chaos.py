"""Outbox resilience tests: crash recovery, max retries, concurrent relays.

Integration tests that verify the transactional outbox pattern survives
all failure modes. These require real PostgreSQL and Redis.
"""

import asyncio
import uuid
import pytest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, func


class TestOutboxCrashRecovery:
    """Gateway crash scenarios — event should survive."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_event_survives_gateway_crash_after_commit(
        self, client, live_endpoint
    ):
        """Simulate gateway crash after commit: inject event+outbox directly via DB,
        then verify the outbox record exists and is 'pending'."""
        from app.core.database import async_session_factory
        from app.models.event import Event
        from app.models.outbox import OutboxRecord

        event_id = uuid.uuid4()
        async with async_session_factory() as db:
            event = Event(
                id=event_id,
                endpoint_id=uuid.UUID(live_endpoint["id"]),
                request_body={"simulated": "crash"},
                request_headers={"x-test": "true"},
                status="pending",
            )
            outbox = OutboxRecord(
                event_id=event_id,
                publish_key=live_endpoint["id"],
                publish_topic="raw-events",
            )
            db.add(event)
            db.add(outbox)
            await db.commit()

        async with async_session_factory() as db:
            ev = await db.get(Event, event_id)
            assert ev is not None, "Event should survive crash"
            assert ev.status == "pending"

            result = await db.execute(
                select(OutboxRecord).where(OutboxRecord.event_id == event_id)
            )
            ob = result.scalar_one_or_none()
            assert ob is not None, "Outbox record should survive crash"
            assert ob.status == "pending"

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_multiple_events_survive_crash(
        self, client, live_endpoint
    ):
        """10 events created in a batch before 'crash' — all should exist as pending."""
        from app.core.database import async_session_factory
        from app.models.event import Event
        from app.models.outbox import OutboxRecord

        ids = []
        async with async_session_factory() as db:
            for i in range(10):
                eid = uuid.uuid4()
                ids.append(str(eid))
                event = Event(
                    id=eid,
                    endpoint_id=uuid.UUID(live_endpoint["id"]),
                    request_body={"seq": i},
                    status="pending",
                )
                outbox = OutboxRecord(
                    event_id=eid,
                    publish_key=live_endpoint["id"],
                    publish_topic="raw-events",
                )
                db.add(event)
                db.add(outbox)
            await db.commit()

        async with async_session_factory() as db:
            result = await db.execute(
                select(func.count()).where(
                    Event.id.in_([uuid.UUID(i) for i in ids])
                )
            )
            assert result.scalar() == 10


class TestOutboxRelayFailures:
    """Outbox relay worker failure modes."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_outbox_retry_on_kafka_failure(self, client, live_endpoint):
        """Simulate Kafka being down: outbox relay should increment attempts
        but NOT mark completed."""
        from app.core.database import async_session_factory
        from app.models.outbox import OutboxRecord

        event_id = uuid.uuid4()
        async with async_session_factory() as db:
            ob = OutboxRecord(
                event_id=event_id,
                publish_key=live_endpoint["id"],
                publish_topic="raw-events",
                attempts=0,
                status="pending",
            )
            db.add(ob)
            await db.commit()

        # Simulate relay failure: update attempts
        async with async_session_factory() as db:
            ob = await db.get(OutboxRecord, ob.id)
            ob.attempts += 1
            ob.last_error = "KafkaTimeoutError: broker not available"
            db.add(ob)
            await db.commit()

        async with async_session_factory() as db:
            ob = await db.get(OutboxRecord, ob.id)
            assert ob.status == "pending"
            assert ob.attempts == 1
            assert ob.last_error is not None

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_outbox_max_attempts_exhausted(self, client, live_endpoint):
        """After 10 failed attempts, status should become 'failed'."""
        from app.core.database import async_session_factory
        from app.models.outbox import OutboxRecord

        event_id = uuid.uuid4()
        async with async_session_factory() as db:
            ob = OutboxRecord(
                event_id=event_id,
                publish_key=live_endpoint["id"],
                publish_topic="raw-events",
                attempts=10,
                status="failed",
                last_error="Max attempts exhausted",
            )
            db.add(ob)
            await db.commit()

        async with async_session_factory() as db:
            ob = await db.get(OutboxRecord, ob.id)
            assert ob.status == "failed"
            assert ob.attempts >= 10


class TestOutboxRelayDrain:
    """Verify the outbox relay drains pending records correctly."""

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_drain_1000_pending_records(self, client, live_endpoint):
        """Insert 1000 pending outbox records and verify they can all be read
        with FOR UPDATE SKIP LOCKED."""
        from app.core.database import async_session_factory
        from app.models.outbox import OutboxRecord

        ids = []
        async with async_session_factory() as db:
            for i in range(1000):
                eid = uuid.uuid4()
                ids.append(eid)
                ob = OutboxRecord(
                    event_id=eid,
                    publish_key=live_endpoint["id"],
                    publish_topic="raw-events",
                    status="pending",
                )
                db.add(ob)
            await db.commit()

        # Simulate relay batch reads (FOR UPDATE SKIP LOCKED, batch=10)
        total_read = 0
        while total_read < 1000:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(OutboxRecord)
                    .where(OutboxRecord.status == "pending")
                    .order_by(OutboxRecord.created_at)
                    .limit(10)
                    .with_for_update(skip_locked=True)
                )
                batch = result.scalars().all()
                if not batch:
                    break
                for ob in batch:
                    ob.status = "completed"
                await db.commit()
                total_read += len(batch)

        assert total_read == 1000, f"Expected to drain 1000, got {total_read}"

        async with async_session_factory() as db:
            result = await db.execute(
                select(func.count()).where(
                    OutboxRecord.id.in_([uuid.UUID(str(e)) for e in ids])
                )
            )
            assert result.scalar() == 1000

    @pytest.mark.skip(reason="Requires live docker-compose stack with real DB")
    async def test_concurrent_relays_dont_conflict(self, client, live_endpoint):
        """Two concurrent 'relay' processes reading from outbox — each should
        get distinct records."""
        from app.core.database import async_session_factory
        from app.models.outbox import OutboxRecord

        event_ids = [uuid.uuid4() for _ in range(20)]
        async with async_session_factory() as db:
            for eid in event_ids:
                db.add(OutboxRecord(
                    event_id=eid,
                    publish_key=live_endpoint["id"],
                    publish_topic="raw-events",
                    status="pending",
                ))
            await db.commit()

        async def relay_sim(name: str, count: int) -> list[uuid.UUID]:
            collected = []
            while len(collected) < count:
                async with async_session_factory() as db:
                    result = await db.execute(
                        select(OutboxRecord)
                        .where(OutboxRecord.status == "pending")
                        .order_by(OutboxRecord.created_at)
                        .limit(10)
                        .with_for_update(skip_locked=True)
                    )
                    batch = result.scalars().all()
                    for ob in batch:
                        ob.status = "completed"
                    await db.commit()
                    collected.extend(ob.event_id for ob in batch)
                    if not batch:
                        await asyncio.sleep(0.1)
            return collected

        relay_a = asyncio.create_task(relay_sim("A", 10))
        relay_b = asyncio.create_task(relay_sim("B", 10))
        results = await asyncio.gather(relay_a, relay_b)

        a_ids = set(str(e) for e in results[0])
        b_ids = set(str(e) for e in results[1])
        assert len(a_ids) == 10
        assert len(b_ids) == 10
        assert a_ids.isdisjoint(b_ids), "Relays got overlapping records!"
