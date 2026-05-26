"""
filter.py — Dynamic Route Filter Engine.

Evaluates a JMESPath expression against a unified evaluation context
that contains both the webhook payload body and the request headers.

WHY a unified context?
  Many real-world webhook providers (GitHub, Shopify) put the event type
  in an HTTP header (e.g. X-GitHub-Event: push), not inside the JSON body.
  By merging headers and body into a single dict, users can write filters
  that work with any provider schema without changing routing logic.

Evaluation context shape passed to JMESPath:
  {
    "body":    { ...original webhook JSON payload... },
    "headers": { "x-github-event": "push", ... }
  }

Example filter expressions:
  - body.event_type == 'payment.succeeded'
  - starts_with(body.event_type, 'payment.')
  - headers."x-github-event" == 'push'
  - body.customer.email           (existence check — truthy if present)
  - body.items[?price > `100`]    (array filter — truthy if non-empty)
"""

from __future__ import annotations

import logging

import jmespath
from jmespath.exceptions import JMESPathError

logger = logging.getLogger(__name__)


def build_filter_context(body: dict, headers: dict | None = None) -> dict:
    """
    Build the unified evaluation context passed to every JMESPath filter.

    Args:
        body:    The raw webhook JSON payload dict.
        headers: The HTTP request headers dict (lowercased keys), if available.

    Returns:
        A dict with two top-level keys: `body` and `headers`.
    """
    # Build a unified context with both namespaced and top-level access.
    # - `body.*` and `headers.*` for explicit namespaced access
    # - Top-level keys from body are also merged in so bare expressions
    #   like `event == 'payment.succeeded'` work without the `body.` prefix.
    context: dict = {
        "body": body or {},
        "headers": headers or {},
    }
    # Merge top-level body keys, skipping reserved keys "body"/"headers".
    if isinstance(body, dict):
        for key, value in body.items():
            if key not in ("body", "headers"):
                context.setdefault(key, value)
    return context


def evaluate_filter(expression: str, context: dict) -> bool:
    """
    Evaluate a JMESPath filter expression against the given context.

    Behaviour when the path is missing or the result is falsy:
      - Returns False cleanly (no exception raised).
      - JMESPath returns None for missing paths; bool(None) == False.
      - An empty array []  or empty string "" are also treated as no-match.

    Args:
        expression: A compiled-or-string JMESPath expression.
        context:    The unified dict from build_filter_context().

    Returns:
        True if the expression matches (truthy result), False otherwise.

    Raises:
        JMESPathError: Only if the expression string itself is syntactically
                       invalid. Callers should catch this and treat as no-match.
    """
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
    """
    Decide whether an event should be delivered to a route.

    This is the single entry-point used by the transform worker.
    It combines filter context building, evaluation, and safe error handling.

    Rules:
      - No filter_expression → always deliver (match all).
      - Filter returns a truthy value  → deliver.
      - Filter returns a falsy value   → skip.
      - Filter raises JMESPathError    → skip and log the error (fail-closed).
      - Filter raises any other error  → skip and log the error (fail-closed).

    Args:
        filter_expression: The JMESPath string saved on the Route, or None.
        body:              Webhook JSON payload.
        headers:           HTTP request headers (lowercased), optional.
        route_id:          Used only for structured log messages.
        event_id:          Used only for structured log messages.

    Returns:
        True if the event should be delivered, False if it should be skipped.
    """
    # No filter set on this route → pass all events through.
    if not filter_expression or filter_expression.strip() == "":
        return True

    context = build_filter_context(body, headers)

    try:
        matched = evaluate_filter(filter_expression, context)
    except JMESPathError as exc:
        logger.error(
            "Filter syntax error — skipping delivery "
            "[event=%s route=%s expression=%r error=%s]",
            event_id, route_id, filter_expression, exc,
        )
        return False
    except Exception as exc:
        logger.error(
            "Unexpected filter evaluation error — skipping delivery "
            "[event=%s route=%s error=%s]",
            event_id, route_id, exc,
            exc_info=True,
        )
        return False

    if not matched:
        logger.info(
            "Filter did not match — route skipped "
            "[event=%s route=%s expression=%r]",
            event_id, route_id, filter_expression,
        )

    return matched
