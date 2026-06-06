import json
import logging
import sys
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("collector")

app = FastAPI()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def catch_all(request: Request, path: str):
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace")

    try:
        body_json = json.loads(body_text) if body_text else None
    except json.JSONDecodeError:
        body_json = None

    logger.info("─" * 60)
    logger.info("Received %s /%s", request.method, path)
    logger.info("Headers: %s", dict(request.headers))
    if body_json:
        logger.info("Body (JSON): %s", json.dumps(body_json, indent=2)[:2000])
    elif body_text:
        logger.info("Body (raw): %s", body_text[:2000])
    logger.info("Time: %s", datetime.utcnow().isoformat())
    logger.info("─" * 60)

    return {"status": "collected", "method": request.method, "path": f"/{path}"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9999, log_level="info")
