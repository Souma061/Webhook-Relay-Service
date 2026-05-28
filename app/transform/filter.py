from __future__ import annotations

import logging

import jmespath
from jmespath.exceptions import JMESPathError

logger = logging.getLogger(__name__)


def build_filter_context(body: dict, headers: dict | None = None) -> dict:
    context: dict = {
        "body": body or {},
        "headers": headers or {},
    }
    if isinstance(body, dict):
        for key, value in body.items():
            if key not in ("body", "headers"):
                context.setdefault(key, value)
    return context


def evaluate_filter(expression: str, context: dict) -> bool:
    result = jmespath.search(expression, context)
    return bool(result)


def route_matches_event(
    filter_expression: str | None,
    body: dict,
    headers: dict | None = None,
    *,
    route_id: str = "",
    event_id: str = "",
) -> bool:
    if not filter_expression or filter_expression.strip() == "":
        return True

    context = build_filter_context(body, headers)

    try:
        matched = evaluate_filter(filter_expression, context)
    except JMESPathError as exc:
        logger.error("Filter syntax error [event=%s route=%s expression=%r error=%s]", event_id, route_id, filter_expression, exc)
        return False
    except Exception as exc:
        logger.error("Unexpected filter error [event=%s route=%s error=%s]", event_id, route_id, exc, exc_info=True)
        return False

    if not matched:
        logger.info("Filter did not match [event=%s route=%s expression=%r]", event_id, route_id, filter_expression)

    return matched
