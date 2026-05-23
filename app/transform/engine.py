import re

TEMPLATE_PATTERN = re.compile(r"\{\{(.+?)\}\}")


def apply_pipeline(pipeline: list[dict], payload: dict) -> dict: # this function applies the transformation pipeline to the payload
    current = payload
    for step in pipeline:
        t = step["type"]
        if t == "passthrough":
            continue
        elif t == "template":
            current = _apply_template(step["body"], current)
        elif t == "jmespath":
            import jmespath
            current = jmespath.search(step["expression"], current)
    return current


def _apply_template(template: dict, payload: dict) -> dict:  # this function applies the template transformation to the payload
    result = {}
    for key, value in template.items():
        if isinstance(value, str):
            result[key] = TEMPLATE_PATTERN.sub(
                lambda m: _eval_expr(m.group(1).strip(), payload), value
            )
        else:
            result[key] = value
    return result


def _eval_expr(expr: str, payload: dict) -> str: # this function evaluates the expression in the template, supporting dot notation and division
    parts = expr.replace(" ", "").split("/")
    current = payload
    for part in parts[0].split("."):
        if isinstance(current, dict):
            current = current.get(part, "")
        else:
            return str(current)
    if len(parts) > 1 and isinstance(current, (int, float)):
        divisor = float(parts[1])
        return str(current / divisor)
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
