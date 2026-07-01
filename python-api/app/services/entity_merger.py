"""Entity merger — resolve field-level conflicts using deterministic or LLM strategies.

For deterministic strategies (exact, keep_first, keep_last, field_overwrite),
delegates to Rust via IndexManager PyO3 methods.
For LLM strategies (llm_balanced, llm_prefer_first, llm_prefer_last),
calls Ollama Cloud to reconcile conflicting field values.
"""

import json
import logging
from typing import Optional

from app.llm.ollama_client import call_ollama_cloud, OllamaCloudError
from app.models.template import TemplateConfig
from app.services.merge_strategy import MergeStrategy

logger = logging.getLogger(__name__)


class EntityMerger:
    """Merge conflicting entities/edges using deterministic or LLM strategies."""

    def __init__(self, template: Optional[TemplateConfig] = None, job_id: Optional[str] = None):
        self.template = template
        self.job_id = job_id

    async def merge(
        self,
        existing: dict,
        incoming: dict,
        strategy: MergeStrategy,
        item_type: str = "node",
    ) -> dict:
        if strategy in (MergeStrategy.EXACT, MergeStrategy.KEEP_FIRST):
            return existing
        if strategy == MergeStrategy.KEEP_LAST:
            return {**incoming, "id": existing.get("id")}
        if strategy == MergeStrategy.FIELD_OVERWRITE:
            return self._field_overwrite(existing, incoming)
        if strategy.is_llm:
            return await self._llm_merge(existing, incoming, strategy, item_type)
        return existing

    def _field_overwrite(self, existing: dict, incoming: dict) -> dict:
        merged = existing.copy()
        for key, value in incoming.items():
            if key == "id":
                continue
            if value is not None and (key not in merged or merged.get(key) is None):
                merged[key] = value
            elif isinstance(value, list) and isinstance(merged.get(key), list):
                for item in value:
                    if item not in merged[key]:
                        merged[key].append(item)
        if "confidence" in existing and "confidence" in incoming:
            merged["confidence"] = (existing["confidence"] + incoming["confidence"]) / 2.0
        return merged

    async def _llm_merge(
        self,
        existing: dict,
        incoming: dict,
        strategy: MergeStrategy,
        item_type: str,
    ) -> dict:
        schema_fields = self._get_schema_fields(item_type)
        if strategy == MergeStrategy.LLM_PREFER_FIRST:
            bias = "prefer the existing version when in doubt"
        elif strategy == MergeStrategy.LLM_PREFER_LAST:
            bias = "prefer the incoming version when in doubt"
        else:
            bias = "balance both versions equally"

        system_prompt = (
            f"You are an entity merge specialist. Reconcile two versions of the same {item_type}. "
            f"{bias}. For each conflicting field, produce a single merged value.\n\n"
            f"Output a JSON object with these fields:\n{json.dumps(schema_fields, indent=2)}"
        )

        user_prompt = (
            f"### Existing Version:\n{json.dumps(existing, indent=2, default=str)}\n\n"
            f"### Incoming Version:\n{json.dumps(incoming, indent=2, default=str)}\n\n"
            f"Produce the merged version:"
        )

        try:
            response = await call_ollama_cloud(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
                job_id=self.job_id,
            )
            merged = json.loads(response["content"])
            merged["id"] = existing.get("id")
            return merged
        except (OllamaCloudError, json.JSONDecodeError) as exc:
            logger.warning(f"LLM merge failed, falling back to keep_first: {exc}")
            return existing

    def _get_schema_fields(self, item_type: str) -> dict:
        if self.template is None:
            if item_type == "node":
                return {"label": "string", "entity_type": "string", "description": "string", "aliases": "list", "confidence": "float"}
            return {"predicate": "string", "source": "uuid", "target": "uuid", "context": "string", "weight": "float"}

        if item_type == "node" and self.template.entity_schema:
            return {f.name: f.type.value for f in self.template.entity_schema.fields}
        if item_type == "edge" and self.template.relation_schema:
            return {f.name: f.type.value for f in self.template.relation_schema.fields}
        return {}