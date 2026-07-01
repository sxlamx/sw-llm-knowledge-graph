"""Single entry point for all Ollama Cloud LLM API calls.

Every feature that needs LLM access (extraction, contextual prefix, doc
summary, ontology generation, two-stage extraction, knowledge chat, etc.)
MUST import and call ``call_ollama_cloud`` — never use ``httpx`` directly
against the Ollama Cloud endpoint.
"""

import httpx
import logging
from typing import Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.services.cost_tracker import get_tracker

settings = get_settings()
logger = logging.getLogger(__name__)


class OllamaCloudError(Exception):
    pass


class OllamaCloudAuthError(OllamaCloudError):
    pass


class OllamaCloudRateLimitError(OllamaCloudError):
    pass


class OllamaCloudServerError(OllamaCloudError):
    pass


_RETRIABLE = (OllamaCloudRateLimitError, OllamaCloudServerError, httpx.ConnectError, httpx.ReadTimeout)


@retry(
    retry=retry_if_exception_type(_RETRIABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def call_ollama_cloud(
    system_prompt: str,
    user_prompt: str,
    response_format: Optional[dict] = None,
    model: Optional[str] = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    job_id: Optional[str] = None,
) -> dict:
    if not settings.ollama_cloud_api_key:
        raise OllamaCloudAuthError(
            "ollama_cloud_api_key is not set. Configure OLLAMA_CLOUD_API_KEY before using LLM features."
        )

    model = model or settings.ollama_cloud_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        try:
            resp = await client.post(
                f"{settings.ollama_cloud_base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
            )
        except httpx.ConnectError as exc:
            raise OllamaCloudServerError(f"Cannot reach Ollama Cloud: {exc}") from exc
        except httpx.ReadTimeout as exc:
            raise OllamaCloudServerError(f"Ollama Cloud timed out: {exc}") from exc

    if resp.status_code == 401:
        raise OllamaCloudAuthError("Ollama Cloud returned 401 — check ollama_cloud_api_key")
    if resp.status_code == 429:
        raise OllamaCloudRateLimitError("Ollama Cloud rate limit hit (429)")
    if resp.status_code >= 500:
        raise OllamaCloudServerError(f"Ollama Cloud server error {resp.status_code}")
    resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]

    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    if job_id:
        tracker = get_tracker(job_id)
        if tracker is not None:
            await tracker.record(model, input_tokens, output_tokens)

    return {"content": content.strip(), "usage": usage}