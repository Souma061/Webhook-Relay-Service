# Architecture: Modular Monolith

**Not microservices.** Microservices add service discovery, inter-service auth, distributed tracing — you'd spend months on infra before writing any business logic.

**Instead: Modular monolith.** Single deployment, single codebase, but CLEAR package boundaries. Every component can be pulled out into its own service later by running its entry point as a separate container.

```
┌───────────────────────────────────────────────────────┐
│                   Docker Container                     │
│                                                        │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────┐ │
│  │  FastAPI   │  │  Transform  │  │   Delivery       │ │
│  │  Gateway   │  │  Worker     │  │   Worker(s)      │ │
│  │  (ASGI)    │  │  (asyncio)  │  │   (asyncio)      │ │
│  └─────┬──────┘  └──────┬─────┘  └────────┬─────────┘ │
│        │                │                  │           │
│        └────────────────┼──────────────────┘           │
│                         ▼                              │
│                   Kafka (internal)                     │
└────────────────────────────────────────────────────────┘
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
        PostgreSQL    Redis       Destinations
```

When you need scale, each worker entry point becomes a separate `docker run`:

```bash
# Scale delivery workers independently
docker run relay delivery-worker   # instance 1
docker run relay delivery-worker   # instance 2
docker run relay delivery-worker   # instance 3
```

No code changes needed — just Kafka consumer groups handle the partitioning.
