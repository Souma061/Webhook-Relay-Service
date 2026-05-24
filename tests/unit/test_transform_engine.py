"""
Unit tests for app/transform/engine.py

Tests the transform pipeline engine in total isolation (no DB, no Redis, no Kafka).
Covers: passthrough, template substitution, JMESPath extraction, division arithmetic,
nested access, multi-step pipelines, and edge cases.
"""
import pytest
from app.transform.engine import apply_pipeline, _apply_template, _eval_expr


# ── passthrough ────────────────────────────────────────────────────────────────

class TestPassthrough:
    def test_passthrough_returns_payload_unchanged(self):
        payload = {"event": "payment.succeeded", "amount": 100}
        result = apply_pipeline([{"type": "passthrough"}], payload)
        assert result == payload

    def test_empty_pipeline_returns_payload_unchanged(self):
        payload = {"key": "value"}
        result = apply_pipeline([], payload)
        assert result == payload

    def test_multiple_passthroughs_unchanged(self):
        payload = {"a": 1, "b": 2}
        result = apply_pipeline(
            [{"type": "passthrough"}, {"type": "passthrough"}],
            payload,
        )
        assert result == payload


# ── template ──────────────────────────────────────────────────────────────────

class TestTemplate:
    def test_simple_key_substitution(self):
        payload = {"name": "Alice", "event": "signup"}
        pipeline = [{"type": "template", "body": {"greeting": "Hello {{name}}", "action": "{{event}}"}}]
        result = apply_pipeline(pipeline, payload)
        assert result == {"greeting": "Hello Alice", "action": "signup"}

    def test_nested_dot_access(self):
        payload = {"user": {"email": "alice@example.com"}}
        pipeline = [{"type": "template", "body": {"to": "{{user.email}}"}}]
        result = apply_pipeline(pipeline, payload)
        assert result == {"to": "alice@example.com"}

    def test_division_arithmetic(self):
        payload = {"amount_cents": 4999}
        pipeline = [{"type": "template", "body": {"amount_dollars": "{{amount_cents / 100}}"}}]
        result = apply_pipeline(pipeline, payload)
        assert result == {"amount_dollars": "49.99"}

    def test_missing_key_returns_empty_string(self):
        payload = {"name": "Bob"}
        pipeline = [{"type": "template", "body": {"val": "{{missing_key}}"}}]
        result = apply_pipeline(pipeline, payload)
        assert result == {"val": ""}

    def test_non_string_value_preserved(self):
        payload = {"x": 1}
        pipeline = [{"type": "template", "body": {"static_int": 42, "flag": True}}]
        result = apply_pipeline(pipeline, payload)
        assert result["static_int"] == 42
        assert result["flag"] is True

    def test_multiple_placeholders_in_one_field(self):
        payload = {"first": "John", "last": "Doe"}
        pipeline = [{"type": "template", "body": {"full_name": "{{first}} {{last}}"}}]
        result = apply_pipeline(pipeline, payload)
        assert result == {"full_name": "John Doe"}


# ── JMESPath ──────────────────────────────────────────────────────────────────

class TestJmesPath:
    def test_simple_field_extraction(self):
        payload = {"data": {"customer": "Alice"}, "meta": "x"}
        pipeline = [{"type": "jmespath", "expression": "data"}]
        result = apply_pipeline(pipeline, payload)
        assert result == {"customer": "Alice"}

    def test_nested_field_extraction(self):
        payload = {"order": {"items": [{"price": 10}, {"price": 20}]}}
        pipeline = [{"type": "jmespath", "expression": "order.items[0].price"}]
        result = apply_pipeline(pipeline, payload)
        assert result == 10

    def test_filter_projection(self):
        payload = {"events": [{"type": "a", "v": 1}, {"type": "b", "v": 2}]}
        pipeline = [{"type": "jmespath", "expression": "events[?type=='a'].v"}]
        result = apply_pipeline(pipeline, payload)
        assert result == [1]

    def test_jmespath_no_match_returns_none(self):
        payload = {"foo": "bar"}
        pipeline = [{"type": "jmespath", "expression": "nonexistent"}]
        result = apply_pipeline(pipeline, payload)
        assert result is None


# ── Multi-step pipelines ───────────────────────────────────────────────────────

class TestMultiStep:
    def test_jmespath_then_template(self):
        payload = {"data": {"amount": 5000, "currency": "USD"}}
        pipeline = [
            {"type": "jmespath", "expression": "data"},
            {"type": "template", "body": {"label": "{{currency}}: {{amount / 100}}"}},
        ]
        result = apply_pipeline(pipeline, payload)
        assert result == {"label": "USD: 50.0"}

    def test_passthrough_then_template(self):
        payload = {"event": "order.created"}
        pipeline = [
            {"type": "passthrough"},
            {"type": "template", "body": {"topic": "{{event}}"}},
        ]
        result = apply_pipeline(pipeline, payload)
        assert result == {"topic": "order.created"}


# ── _eval_expr edge cases ─────────────────────────────────────────────────────

class TestEvalExpr:
    def test_float_result(self):
        payload = {"val": 1}
        assert _eval_expr("val / 3", payload) == str(1 / 3)

    def test_deeply_nested_access(self):
        payload = {"a": {"b": {"c": "deep"}}}
        assert _eval_expr("a.b.c", payload) == "deep"

    def test_non_dict_intermediate_returns_early(self):
        payload = {"a": "not_a_dict"}
        # accessing a.b where a is a string — should not crash
        result = _eval_expr("a.b", payload)
        assert result == "not_a_dict"

    def test_division_on_non_numeric_skips(self):
        payload = {"val": "text"}
        result = _eval_expr("val / 100", payload)
        # val is a string, so division is skipped and we get the raw string
        assert result == "text"
