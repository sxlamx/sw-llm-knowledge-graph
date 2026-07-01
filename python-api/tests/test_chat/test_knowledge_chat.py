"""Tests for Knowledge Chat service and endpoint.

NOTE: The KnowledgeChatService and /chat endpoint are NOT YET IMPLEMENTED.
These tests are written against the spec and will pass once the implementation
is added. Tests that require the service are skipped until then.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport


FAKE_USER = {"id": "test-user-id", "email": "test@example.com", "name": "Test User"}


class TestChatEndpointNotImplemented:
    """These tests verify the chat endpoint exists and returns correct status codes.
    They will FAIL until the /chat endpoint is implemented."""

    @pytest.fixture
    def app(self):
        _app = FastAPI()
        try:
            from app.routers.chat import router
            _app.include_router(router, prefix="/api/v1")
            _app.dependency_overrides[
                __import__("app.auth.middleware", fromlist=["get_current_user"]).get_current_user
            ] = lambda: FAKE_USER
        except ImportError:
            pass
        return _app

    @pytest.mark.skip(reason="chat router not yet implemented")
    @pytest.mark.asyncio
    async def test_chat_returns_answer(self, app):
        from app.auth.middleware import get_current_user
        from app.routers.chat import router as chat_router
        _app = FastAPI()
        _app.include_router(chat_router, prefix="/api/v1")
        _app.dependency_overrides[get_current_user] = lambda: FAKE_USER

        with (
            patch("app.services.knowledge_chat.KnowledgeChatService.search_knowledge",
                  new_callable=AsyncMock,
                  return_value=([{"label": "Alice", "entity_type": "Person"}],
                                 [{"predicate": "works_at", "source": "Alice", "target": "Google"}])),
            patch("app.services.knowledge_chat.call_ollama_cloud",
                  new_callable=AsyncMock,
                  return_value={"content": "Alice works at Google.", "usage": {}}),
        ):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/api/v1/collections/test-col/chat",
                    json={"query": "Who works at Google?"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert "nodes" in data
        assert "edges" in data

    @pytest.mark.skip(reason="chat router not yet implemented")
    @pytest.mark.asyncio
    async def test_chat_requires_auth(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/collections/test-col/chat",
                json={"query": "test"},
            )
        assert resp.status_code in (401, 403)

    @pytest.mark.skip(reason="chat router not yet implemented")
    @pytest.mark.asyncio
    async def test_chat_nonexistent_collection_404(self, app):
        from app.auth.middleware import get_current_user
        _app = FastAPI()
        from app.routers.chat import router as chat_router
        _app.include_router(chat_router, prefix="/api/v1")
        _app.dependency_overrides[get_current_user] = lambda: FAKE_USER

        with patch("app.routers.chat.get_collection", new_callable=AsyncMock, return_value=None):
            async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as ac:
                resp = await ac.post(
                    f"/api/v1/collections/{uuid.uuid4()}/chat",
                    json={"query": "test"},
                )
        assert resp.status_code == 404


class TestKnowledgeChatServiceNotImplemented:
    """These tests verify the KnowledgeChatService behavior.
    They will FAIL until the service is implemented."""

    @pytest.mark.skip(reason="knowledge_chat service not yet implemented")
    @pytest.mark.asyncio
    async def test_search_knowledge_returns_nodes_and_edges(self):
        from app.services.knowledge_chat import KnowledgeChatService
        chat_service = KnowledgeChatService(collection_id="test-col-123")
        with patch("app.services.knowledge_chat.get_index_manager") as mock_im:
            mock_instance = MagicMock()
            mock_instance.search_nodes.return_value = '[{"label": "Alice", "score": 0.9}]'
            mock_instance.search_edges.return_value = '[{"predicate": "works_at", "score": 0.85}]'
            mock_im.return_value = mock_instance
            with patch("app.services.knowledge_chat.embed_query",
                        new_callable=AsyncMock, return_value=[0.1] * 1024):
                nodes, edges = await chat_service.search_knowledge("Who works at Google?")
                assert len(nodes) == 1
                assert len(edges) == 1

    @pytest.mark.skip(reason="knowledge_chat service not yet implemented")
    @pytest.mark.asyncio
    async def test_chat_returns_answer_and_retrieved_items(self):
        from app.services.knowledge_chat import KnowledgeChatService
        chat_service = KnowledgeChatService(collection_id="test-col-123")
        with patch.object(chat_service, "search_knowledge",
                          new_callable=AsyncMock,
                          return_value=([{"label": "Alice", "entity_type": "Person"}],
                                        [{"predicate": "works_at", "source": "Alice", "target": "Google"}])):
            with patch("app.services.knowledge_chat.call_ollama_cloud",
                        new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = {
                    "content": "Alice works at Google.",
                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                }
                result = await chat_service.chat("Who works at Google?")
                assert "answer" in result
                assert "nodes" in result
                assert "edges" in result
                assert result["answer"] == "Alice works at Google."

    @pytest.mark.skip(reason="knowledge_chat service not yet implemented")
    @pytest.mark.asyncio
    async def test_chat_no_results_returns_info_message(self):
        from app.services.knowledge_chat import KnowledgeChatService
        chat_service = KnowledgeChatService(collection_id="test-col-123")
        with patch.object(chat_service, "search_knowledge",
                          new_callable=AsyncMock,
                          return_value=([], [])):
            with patch("app.services.knowledge_chat.call_ollama_cloud",
                        new_callable=AsyncMock) as mock_llm:
                mock_llm.return_value = {
                    "content": "I don't have information about that.",
                    "usage": {},
                }
                result = await chat_service.chat("Unknown topic")
                assert "answer" in result


class TestChatRequestSchema:
    """Test that ChatRequest schema validates correctly once implemented."""

    @pytest.mark.skip(reason="ChatRequest schema not yet implemented")
    def test_chat_request_defaults(self):
        from app.models.schemas import ChatRequest
        req = ChatRequest(query="test")
        assert req.top_k_nodes == 5
        assert req.top_k_edges == 5

    @pytest.mark.skip(reason="ChatRequest schema not yet implemented")
    def test_chat_request_custom_top_k(self):
        from app.models.schemas import ChatRequest
        req = ChatRequest(query="test", top_k_nodes=3, top_k_edges=10)
        assert req.top_k_nodes == 3
        assert req.top_k_edges == 10

    @pytest.mark.skip(reason="ChatRequest schema not yet implemented")
    def test_chat_request_requires_query(self):
        from app.models.schemas import ChatRequest
        with pytest.raises(Exception):
            ChatRequest()