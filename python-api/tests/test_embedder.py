"""Tests for the HuggingFace embedder — sentence-transformers, not OpenAI.

These tests verify:
- embed_texts / embed_query use sentence_transformers (not openai.embeddings)
- Output dimension matches settings.embedding_dimension (1024)
- Empty list returns empty list (no crash)
- Zero-vector fallback on error (not an exception)
- Different prompts used for passage vs query embeddings
"""

import pytest
from unittest.mock import patch, MagicMock
import numpy as np

import app.llm.embedder as embedder_module


class TestEmbedderModelSource:
    """Embedder must use HuggingFace sentence-transformers, not OpenAI."""

    def test_uses_sentence_transformers_not_openai(self):
        """Verify the embedder imports SentenceTransformer, not openai module."""
        import importlib.util
        spec = importlib.util.find_spec("sentence_transformers")
        assert spec is not None, "sentence-transformers must be installed"

        # Verify embedder module uses sentence_transformers
        source = str(embedder_module.__file__)
        with open(source) as f:
            content = f.read()
        assert "openai" not in content.lower(), "embedder must not use openai"

    def test_embedder_query_prompt_differs_from_passage_prompt(self):
        """Query and passage embeddings must use different instruction prompts."""
        assert embedder_module._PASSAGE_PROMPT == ""
        assert embedder_module._QUERY_PROMPT.startswith("Instruct:")


class TestEmbedderDimension:
    """Output vectors must match settings.embedding_dimension (default 1024)."""

    @pytest.mark.asyncio
    async def test_embed_texts_returns_correct_dimension(self, monkeypatch):
        """Each vector must have exactly embedding_dimension floats."""
        expected_dim = embedder_module.settings.embedding_dimension

        # Mock the underlying encode to return predictable vectors
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = expected_dim
        mock_encode = MagicMock(
            return_value=np.array([[0.1] * expected_dim])
        )
        mock_model.encode = mock_encode

        # Clear caches
        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            results = await embedder_module.embed_texts(["test sentence"])
            assert len(results) == 1
            assert len(results[0]) == expected_dim, (
                f"Expected {expected_dim}d vectors, got {len(results[0])}"
            )

    @pytest.mark.asyncio
    async def test_embed_query_returns_correct_dimension(self, monkeypatch):
        """embed_query must also return embedding_dimension floats."""
        expected_dim = embedder_module.settings.embedding_dimension

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = expected_dim
        mock_encode = MagicMock(return_value=np.array([[0.2] * expected_dim]))
        mock_model.encode = mock_encode

        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            result = await embedder_module.embed_query("what is machine learning?")
            assert len(result) == expected_dim, (
                f"Expected {expected_dim}d query vector, got {len(result)}"
            )

    @pytest.mark.asyncio
    async def test_embed_multiple_texts_returns_list(self, monkeypatch):
        """embed_texts with N texts must return N vectors, each of correct dimension."""
        expected_dim = embedder_module.settings.embedding_dimension

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = expected_dim
        mock_encode = MagicMock(
            return_value=np.array([[0.1] * expected_dim, [0.2] * expected_dim, [0.3] * expected_dim])
        )
        mock_model.encode = mock_encode

        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            texts = ["doc one", "doc two", "doc three"]
            results = await embedder_module.embed_texts(texts)
            assert len(results) == 3
            for r in results:
                assert len(r) == expected_dim


class TestEmbedderEdgeCases:
    """Edge cases: empty input, model load failure."""

    @pytest.mark.asyncio
    async def test_embed_empty_list_returns_empty_list(self):
        """embed_texts([]) must return [], not raise or return [[]]."""
        result = await embedder_module.embed_texts([])
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_texts_fallback_to_zero_vector_on_error(self, monkeypatch):
        """On model encode error, return zero vectors (not an exception)."""
        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("GPU out of memory")

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            results = await embedder_module.embed_texts(["some text"])
            assert len(results) == 1
            assert len(results[0]) == embedder_module.settings.embedding_dimension
            assert all(v == 0.0 for v in results[0])

    @pytest.mark.asyncio
    async def test_embed_query_fallback_to_zero_vector_on_error(self, monkeypatch):
        """On query encode error, return zero vector (warning logged, not exception)."""
        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("model load failed")

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            result = await embedder_module.embed_query("search query")
            assert len(result) == embedder_module.settings.embedding_dimension
            assert all(v == 0.0 for v in result)


class TestEmbedderCaching:
    """Results are cached by first 100 chars of text."""

    @pytest.mark.asyncio
    async def test_identical_text_returns_same_vector(self, monkeypatch):
        """Calling embed_texts with the same text twice must return the same vector."""
        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        call_count = [0]

        def mock_encode(texts, **kwargs):
            call_count[0] += len(texts)
            return np.array([[0.5] * embedder_module.settings.embedding_dimension] * len(texts))

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = embedder_module.settings.embedding_dimension
        mock_model.encode = mock_encode

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            first = await embedder_module.embed_texts(["hello world"])
            second = await embedder_module.embed_texts(["hello world"])

            assert call_count[0] == 1, "Cache should prevent duplicate encode calls"
            assert first == second

    @pytest.mark.asyncio
    async def test_different_texts_both_encoded(self, monkeypatch):
        """Two different texts must both be encoded."""
        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        call_count = [0]

        def mock_encode(texts, **kwargs):
            call_count[0] += len(texts)
            return np.array([[0.1] * embedder_module.settings.embedding_dimension] * len(texts))

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = embedder_module.settings.embedding_dimension
        mock_model.encode = mock_encode

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            await embedder_module.embed_texts(["text alpha", "text beta"])
            assert call_count[0] == 2


class TestEmbedderPromptInstructions:
    """Passage vs query prompts are correctly set."""

    def test_passage_prompt_is_empty(self):
        """Passage embedding uses no instruction prefix (verbatim indexing)."""
        assert embedder_module._PASSAGE_PROMPT == ""

    def test_query_prompt_contains_instruct(self):
        """Query embedding uses the Instruct: prefix for search tasks."""
        assert "Instruct:" in embedder_module._QUERY_PROMPT
        assert "Query:" in embedder_module._QUERY_PROMPT

    @pytest.mark.asyncio
    async def test_passage_embedding_uses_no_prompt(self, monkeypatch):
        """Passage embeddings pass empty prompt to encode()."""
        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        captured_kwargs = []

        def capture_encode(texts, **kwargs):
            captured_kwargs.append(kwargs.get("prompt", ""))
            return np.array([[0.0] * embedder_module.settings.embedding_dimension] * len(texts))

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = embedder_module.settings.embedding_dimension
        mock_model.encode = capture_encode

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            await embedder_module.embed_texts(["passage text"])
            assert captured_kwargs[0] == "", "Passage embedding should use empty prompt"

    @pytest.mark.asyncio
    async def test_query_embedding_uses_query_prompt(self, monkeypatch):
        """Query embedding passes _QUERY_PROMPT to encode()."""
        embedder_module._cache.clear()
        if hasattr(embedder_module._get_model, 'cache_clear'):
            embedder_module._get_model.cache_clear()

        captured_kwargs = []

        def capture_encode(texts, **kwargs):
            captured_kwargs.append(kwargs.get("prompt", ""))
            return np.array([[0.0] * embedder_module.settings.embedding_dimension])

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = embedder_module.settings.embedding_dimension
        mock_model.encode = capture_encode

        with patch.object(embedder_module, "_get_model", return_value=mock_model):
            await embedder_module.embed_query("search query")
            assert captured_kwargs[0] == embedder_module._QUERY_PROMPT, (
                f"Query embedding should use _QUERY_PROMPT, got: {captured_kwargs[0]!r}"
            )
