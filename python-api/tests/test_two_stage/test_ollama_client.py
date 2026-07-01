"""Tests for Ollama Cloud API client — centralized LLM call point."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.llm.ollama_client import (
    call_ollama_cloud,
    OllamaCloudError,
    OllamaCloudAuthError,
    OllamaCloudRateLimitError,
    OllamaCloudServerError,
)


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    return httpx.Response(status_code, json=json_data)


class TestOllamaCloudClient:
    @pytest.mark.asyncio
    async def test_successful_call_returns_content_and_usage(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"result": "ok"}'}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            result = await call_ollama_cloud("system", "user")
            assert result["content"] == '{"result": "ok"}'
            assert result["usage"]["prompt_tokens"] == 50

    @pytest.mark.asyncio
    async def test_strips_json_code_fences(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '```json\n{"items": []}\n```'}}],
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            result = await call_ollama_cloud("system", "user")
            assert not result["content"].startswith("```")

    @pytest.mark.asyncio
    async def test_strips_plain_code_fences(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '```\n{"items": []}\n```'}}],
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            result = await call_ollama_cloud("system", "user")
            assert not result["content"].startswith("```")
            assert not result["content"].endswith("```")

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": "unauthorized"}
        mock_resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "401", request=MagicMock(), response=mock_resp))

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            with pytest.raises(OllamaCloudAuthError):
                await call_ollama_cloud("system", "user")

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_auth_error(self):
        from app.llm import ollama_client as mod
        original_key = mod.settings.ollama_cloud_api_key
        try:
            mod.settings.ollama_cloud_api_key = ""
            with pytest.raises(OllamaCloudAuthError, match="ollama_cloud_api_key"):
                await mod.call_ollama_cloud("system", "user")
        finally:
            mod.settings.ollama_cloud_api_key = original_key

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            with pytest.raises(OllamaCloudRateLimitError):
                await call_ollama_cloud("system", "user")

    @pytest.mark.asyncio
    async def test_500_raises_server_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_resp))

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            with pytest.raises(OllamaCloudServerError):
                await call_ollama_cloud("system", "user")

    @pytest.mark.asyncio
    async def test_response_format_passed_to_payload(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            await call_ollama_cloud("sys", "usr", response_format={"type": "json_object"})
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
            assert "response_format" in payload

    @pytest.mark.asyncio
    async def test_cost_tracking_with_job_id(self):
        from app.services.cost_tracker import create_tracker, remove_tracker

        tracker = create_tracker("test-job-cost", max_cost_usd=10.0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            result = await call_ollama_cloud("sys", "usr", job_id="test-job-cost")
            assert tracker.total_input_tokens == 100
            assert tracker.total_output_tokens == 50

        remove_tracker("test-job-cost")

    @pytest.mark.asyncio
    async def test_no_cost_tracking_without_job_id(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 100},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            result = await call_ollama_cloud("sys", "usr", job_id=None)
            assert result["usage"]["prompt_tokens"] == 200

    @pytest.mark.asyncio
    async def test_custom_model_and_temperature(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "response"}}],
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("app.llm.ollama_client.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            await call_ollama_cloud("sys", "usr", model="custom-model", temperature=0.5, max_tokens=2000)
            call_args = mock_client.post.call_args
            payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
            assert payload["model"] == "custom-model"
            assert payload["temperature"] == 0.5
            assert payload["max_tokens"] == 2000