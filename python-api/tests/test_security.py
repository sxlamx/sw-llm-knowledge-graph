"""Security-focused tests — injection, sanitization, input validation."""

import pytest
from app.db.lancedb_client import _safe_id, _safe_str


# ---------------------------------------------------------------------------
# Task 1.2: LanceDB WHERE clause sanitization
# ---------------------------------------------------------------------------

class TestLanceDBSanitization:
    def test_safe_id_rejects_special_chars(self):
        with pytest.raises(ValueError):
            _safe_id("abc; DROP TABLE users--")

    def test_safe_id_rejects_single_quotes(self):
        with pytest.raises(ValueError):
            _safe_id("abc' OR '1'='1")

    def test_safe_id_accepts_uuid(self):
        assert _safe_id("550e8400-e29b-41d4-a716-446655440000") == "550e8400-e29b-41d4-a716-446655440000"

    def test_safe_str_escapes_single_quotes(self):
        result = _safe_str("it's a test")
        assert "\\'" in result
        assert "'" not in result.replace("\\'", "")

    def test_safe_id_rejects_empty_string(self):
        with pytest.raises(ValueError):
            _safe_id("")

    def test_safe_id_accepts_alphanumeric(self):
        assert _safe_id("abc123_-") == "abc123_-"

    def test_safe_str_escapes_backslash(self):
        result = _safe_str('path\\with\\backslash')
        assert "\\\\" in result

    def test_safe_str_escapes_double_quotes(self):
        result = _safe_str('say "hello"')
        assert '\\"' in result