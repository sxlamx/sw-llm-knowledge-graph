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


# ---------------------------------------------------------------------------
# Task 1.3: Graph and document router injection protection
# ---------------------------------------------------------------------------

class TestGraphRouterInjection:
    """Verify that graph.py and documents.py use sanitized WHERE params."""

    def test_graph_router_imports_safe_id(self):
        from app.routers.graph import _safe_id as graph_safe_id
        assert graph_safe_id is _safe_id

    def test_graph_router_imports_safe_str(self):
        from app.routers.graph import _safe_str as graph_safe_str
        assert graph_safe_str is _safe_str

    def test_documents_router_uses_safe_str(self):
        import inspect
        from app.routers.documents import _get_chunks_for_doc
        source = inspect.getsource(_get_chunks_for_doc)
        assert "_safe_str" in source, "documents.py must use _safe_str for doc_id"
        assert ".replace" not in source or "_safe_str" in source, \
            "documents.py should use _safe_str, not manual .replace"

    def test_safe_id_rejects_sql_injection_in_graph_context(self):
        with pytest.raises(ValueError):
            _safe_id("node'; DROP TABLE nodes;--")

    def test_safe_str_prevents_early_termination_in_doc_id(self):
        result = _safe_str('doc"; DROP TABLE chunks;--')
        assert '\\"' in result
        assert "DROP" not in result.split('\\"')[0]