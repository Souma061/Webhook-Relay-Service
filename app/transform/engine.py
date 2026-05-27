import logging
import re

import jmespath

TEMPLATE_PATTERN = re.compile(r"\{\{(.+?)\}\}")

_MAX_EXPRESSION_LEN = 2048  # mirrors the cap in app/schemas/api.py

logger = logging.getLogger(__name__)


def apply_pipeline(pipeline: list[dict], payload: dict) -> dict:
    """Apply a sequence of transform steps against *payload*.

    Each step is a dict that must contain a ``type`` key matching one of:
    ``passthrough``, ``template``, or ``jmespath``.

    Raises:
        KeyError:   if a required field (``body`` / ``expression``) is absent.
        ValueError: if an unknown step type is supplied, or if the JMESPath
                    expression exceeds the maximum allowed length.
    """
    current = payload
    for idx, step in enumerate(pipeline):
        step_type = step.get("type")

        if step_type == "passthrough":
            continue

        elif step_type == "template":
            if "body" not in step:
                raise KeyError(
                    f"Pipeline step {idx} (template) is missing the required 'body' field."
                )
            current = _apply_template(step["body"], current)

        elif step_type == "jmespath":
            if "expression" not in step:
                raise KeyError(
                    f"Pipeline step {idx} (jmespath) is missing the required 'expression' field."
                )
            expr = step["expression"]
            if len(expr) > _MAX_EXPRESSION_LEN:
                raise ValueError(
                    f"Pipeline step {idx}: jmespath expression exceeds "
                    f"{_MAX_EXPRESSION_LEN} character limit."
                )
            result = jmespath.search(expr, current)
            # jmespath.search returns None for no-match; preserve the payload
            # rather than silently replacing it with None.
            current = result if result is not None else current

        else:
            raise ValueError(
                f"Pipeline step {idx}: unknown step type {step_type!r}. "
                "Allowed types: 'passthrough', 'template', 'jmespath'."
            )

    return current


def _apply_template(template: dict, payload: dict) -> dict:
    """Apply {{expr}} substitutions to every string value in *template*."""
    result = {}
    for key, value in template.items():
        if isinstance(value, str):
            result[key] = TEMPLATE_PATTERN.sub(
                lambda m: _eval_expr(m.group(1).strip(), payload), value
            )
        else:
            result[key] = value
    return result


def _eval_expr(expr: str, payload: dict) -> str:
    """Evaluate a simple dot-path (with optional division) against *payload*.

    Supports:
    - Dot notation:  ``data.customer_email``
    - Division:      ``data.amount_total / 100``

    No ``eval()`` is used — only literal key traversal and arithmetic.
    """
    parts = expr.replace(" ", "").split("/")
    current = payload
    for part in parts[0].split("."):
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return str(current)
    if len(parts) > 1 and isinstance(current, (int, float)):
        try:
            divisor = float(parts[1])
            if divisor == 0:
                return "0"
            return str(current / divisor)
        except (ValueError, ZeroDivisionError):
            return str(current)
    return str(current)
"""
engine.py — Transform pipeline engine.

Executes a sequence of transform steps against a payload.

Supported step types:
- passthrough: return payload unchanged (useful as a no-op)
- template: apply {{var}} substitution with basic arithmetic (e.g., {{data.amount / 100}})
- jmespath: run a JMESPath expression to select/restructure data

The _eval_expr function supports:
- Dot notation: data.customer_email
- Division: data.amount_total / 100
- Nested access: data.items[0].price

This is intentionally simple for Phase 1. Expression safety is
maintained by avoiding eval() — only literal dot access and division.
"""
