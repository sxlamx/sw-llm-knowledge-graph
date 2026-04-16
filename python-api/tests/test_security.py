"""Security-focused tests — injection, sanitization, input validation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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


# ---------------------------------------------------------------------------
# Task 1.6: Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_feed_documents_request_rejects_path_traversal(self):
        from app.core.path_sanitizer import validate_file_path
        with pytest.raises(Exception):
            validate_file_path("../../etc/passwd")

    def test_search_request_rejects_too_many_collection_ids(self):
        from app.models.schemas import SearchRequest
        with pytest.raises(Exception):
            SearchRequest(
                query="test",
                collection_ids=[f"col-{i}" for i in range(11)],
            )

    def test_search_request_accepts_up_to_10_collection_ids(self):
        from app.models.schemas import SearchRequest
        req = SearchRequest(
            query="test",
            collection_ids=[f"col-{i}" for i in range(10)],
        )
        assert len(req.collection_ids) == 10

    def test_feed_documents_rejects_empty_file_paths(self):
        from app.models.schemas import FeedDocumentsRequest
        with pytest.raises(Exception):
            FeedDocumentsRequest(file_paths=[])

    def test_validate_file_path_rejects_relative(self):
        from app.core.path_sanitizer import validate_file_path
        with pytest.raises(Exception):
            validate_file_path("relative/path/file.txt")


# ---------------------------------------------------------------------------
# Task 1.8: Error sanitization + collection name validation
# ---------------------------------------------------------------------------

class TestErrorSanitization:
    def test_health_check_error_no_internal_details(self):
        import inspect
        from app.main import health_check
        source = inspect.getsource(health_check)
        assert '"error": str(e)' not in source, "health_check must not expose internal error details"

    def test_collection_name_rejects_html_chars(self):
        from app.models.schemas import CollectionCreate
        for bad_name in ['<script>', 'a&b', 'he"llo', "it's"]:
            with pytest.raises(Exception):
                CollectionCreate(name=bad_name)

    def test_collection_name_accepts_clean_names(self):
        from app.models.schemas import CollectionCreate
        c = CollectionCreate(name="My Research Papers 2024")
        assert c.name == "My Research Papers 2024"


# ---------------------------------------------------------------------------
# Task 1.9: Auth rate limiting + finetune authorization
# ---------------------------------------------------------------------------

class TestRateLimitOnAuth:
    def test_auth_paths_have_stricter_limit(self):
        from app.auth.middleware import AUTH_RATE_LIMIT, _AUTH_RATE_LIMIT_PATHS
        assert AUTH_RATE_LIMIT <= 10, "Auth rate limit should be stricter (10/min)"
        assert len(_AUTH_RATE_LIMIT_PATHS) > 0, "Must define auth rate limit paths"

    def test_auth_rate_limiter_exists(self):
        from app.auth.middleware import auth_rate_limiter
        assert auth_rate_limiter.per_user_limit <= 10


class TestFinetuneAuthorization:
    def test_finetune_start_uses_require_admin(self):
        import inspect
        from app.routers.finetune import start_finetune
        source = inspect.getsource(start_finetune)
        assert "require_admin" in source, "start_finetune must use require_admin dependency"

    def test_finetune_evaluate_uses_require_admin(self):
        import inspect
        from app.routers.finetune import evaluate_models
        source = inspect.getsource(evaluate_models)
        assert "require_admin" in source, "evaluate_models must use require_admin dependency"


# ---------------------------------------------------------------------------
# Task 1.10: First-user admin race condition
# ---------------------------------------------------------------------------

class TestFirstUserRace:
    def test_first_user_admin_flag_exists(self):
        from app.config import Settings
        s = Settings(first_user_admin=True)
        assert s.first_user_admin is True

    def test_first_user_admin_can_be_disabled(self):
        from app.config import Settings
        s = Settings(first_user_admin=False)
        assert s.first_user_admin is False

    def test_first_user_promoted_only_when_flag_true(self):
        import inspect
        from app.db.lancedb_client import create_or_update_user
        source = inspect.getsource(create_or_update_user)
        assert "first_user_admin" in source, "create_or_update_user must check first_user_admin setting"