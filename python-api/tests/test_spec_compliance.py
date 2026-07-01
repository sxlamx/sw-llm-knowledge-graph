"""Tests that verify codebase-wide spec compliance rules from LESSONS.md."""

import pytest
from pathlib import Path


PYTHON_API = Path(__file__).parent.parent / "app"


class TestNoPostgreSQLImports:
    """LESSONS.md: No PostgreSQL/SQLAlchemy imports in app code."""

    def test_no_sqlalchemy_imports_in_app(self):
        for py_file in PYTHON_API.rglob("*.py"):
            content = py_file.read_text()
            assert "sqlalchemy" not in content.lower(), (
                f"{py_file.relative_to(PYTHON_API)} contains sqlalchemy import — "
                "LanceDB is the only metadata store (LESSONS.md rule 3)"
            )
            assert "asyncpg" not in content.lower(), (
                f"{py_file.relative_to(PYTHON_API)} contains asyncpg import"
            )
            assert "alembic" not in content.lower(), (
                f"{py_file.relative_to(PYTHON_API)} contains alembic import"
            )

    def test_no_db_models_or_postgres_files(self):
        """models.py and postgres.py must not exist in app/db/."""
        db_dir = PYTHON_API / "db"
        if db_dir.exists():
            assert not (db_dir / "models.py").exists(), "app/db/models.py must be deleted"
            assert not (db_dir / "postgres.py").exists(), "app/db/postgres.py must be deleted"

    def test_no_alembic_directory(self):
        """alembic/ directory must not exist."""
        project_dir = PYTHON_API.parent
        assert not (project_dir / "alembic").exists(), "alembic/ directory must be deleted"


class TestNoDeprecatedGetEventLoop:
    """asyncio.get_event_loop() is deprecated in Python 3.12+ within async functions."""

    def test_no_get_event_loop_in_app_code(self):
        for py_file in PYTHON_API.rglob("*.py"):
            content = py_file.read_text()
            if "get_event_loop" in content:
                assert "get_running_loop" in content or "get_event_loop_policy" in content, (
                    f"{py_file.relative_to(PYTHON_API)} uses get_event_loop — "
                    "use get_running_loop() inside async functions (Python 3.12+)"
                )
                # Ensure no bare get_event_loop calls remain
                count = content.count("get_event_loop(")
                running_count = content.count("get_running_loop(")
                # get_event_loop_policy is acceptable
                policy_count = content.count("get_event_loop_policy(")
                bare_count = count - policy_count
                assert bare_count == 0 or running_count > 0, (
                    f"{py_file.relative_to(PYTHON_API)}: replace asyncio.get_event_loop() "
                    "with asyncio.get_running_loop()"
                )


class TestNerTagSpecCompliance:
    """NerTag must match the schema in specifications/14-ner-pipeline.md."""

    def test_ner_tag_has_five_fields_no_source(self):
        from app.llm.ner_tagger import NerTag
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(NerTag)}
        assert field_names == {"label", "text", "start", "end", "score"}, (
            f"NerTag fields must be {{label, text, start, end, score}}, got {field_names}"
        )


class TestEmbedderSpecCompliance:
    """Embedder must use HuggingFace sentence-transformers, not OpenAI."""

    def test_embedder_uses_sentence_transformers(self):
        embedder_path = PYTHON_API / "llm" / "embedder.py"
        content = embedder_path.read_text()
        assert "sentence_transformers" in content
        assert "openai" not in content.lower() or "openai" not in content

    def test_embedder_does_not_call_openai_embeddings(self):
        embedder_path = PYTHON_API / "llm" / "embedder.py"
        content = embedder_path.read_text()
        assert "openai.embeddings" not in content
        assert ".embeddings.create" not in content


class TestRustBridgeImport:
    """LESSONS.md: Import IndexManager as PyIndexManager from rust_core."""

    def test_rust_bridge_imports_index_manager(self):
        rust_bridge_path = PYTHON_API / "core" / "rust_bridge.py"
        content = rust_bridge_path.read_text()
        assert "from rust_core import IndexManager as PyIndexManager" in content, (
            "Must import 'from rust_core import IndexManager as PyIndexManager' "
            "(LESSONS.md rule: PyO3 exports the Rust struct name 'IndexManager')"
        )