"""Two-stage LLM extraction: entities first, then edges with entity context.

Stage 1 extracts entities using the template's entity_schema.
Stage 2 extracts relationships using the template's relation_schema plus
the known-entity list from Stage 1 — drastically reducing hallucinated edges.
"""

import json
import logging
from typing import List, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.llm.ollama_client import call_ollama_cloud, OllamaCloudError
from app.models.template import TemplateConfig
from app.services.template_factory import (
    build_entity_pydantic_model,
    build_relation_pydantic_model,
    build_entity_list_model,
    build_relation_list_model,
    _compile_key_pattern,
)

settings = get_settings()
logger = logging.getLogger(__name__)


class TwoStageExtractor:
    def __init__(self, template: TemplateConfig, job_id: Optional[str] = None):
        self.template = template
        self.job_id = job_id
        self.entity_model: Optional[Type[BaseModel]] = None
        self.relation_model: Optional[Type[BaseModel]] = None
        self.entity_list_model: Optional[Type[BaseModel]] = None
        self.relation_list_model: Optional[Type[BaseModel]] = None

        if template.entity_schema is not None:
            self.entity_model = build_entity_pydantic_model(template)
            self.entity_list_model = build_entity_list_model(self.entity_model)
        if template.relation_schema is not None:
            self.relation_model = build_relation_pydantic_model(template)
            self.relation_list_model = build_relation_list_model(self.relation_model)

        self._entity_key_fn = _compile_key_pattern(template.entity_schema.key) if template.entity_schema else None

    async def extract_entities(self, chunk_text: str) -> List[dict]:
        system_prompt = self._build_entity_system_prompt()
        user_prompt = f"Extract entities from the following text.\n\n### Source Text:\n{chunk_text}"

        response = await call_ollama_cloud(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
            job_id=self.job_id,
        )
        return self._parse_entity_response(response)

    async def extract_relations(self, chunk_text: str, known_entities: List[dict]) -> List[dict]:
        system_prompt = self._build_edge_system_prompt()
        known_nodes_str = self._format_known_entities(known_entities)
        user_prompt = (
            f"Extract relationships between the following known entities.\n\n"
            f"# Known Entities\n{known_nodes_str}\n\n"
            f"### Source Text:\n{chunk_text}"
        )

        response = await call_ollama_cloud(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
            job_id=self.job_id,
        )
        return self._parse_relation_response(response)

    async def extract_two_stage(self, chunk_text: str) -> Tuple[List[dict], List[dict]]:
        entities = await self.extract_entities(chunk_text)
        if not entities:
            return entities, []
        relations = await self.extract_relations(chunk_text, entities)
        return entities, relations

    def _build_entity_system_prompt(self) -> str:
        parts = [
            "You are an expert entity extraction specialist.",
            "Your task is to extract all important entities from the text.",
        ]
        if self.template.extraction.node_prompt_extra:
            parts.append(f"\n### Context & Instructions:\n{self.template.extraction.node_prompt_extra}")
        parts.append("\n### Output Format:")
        if self.template.entity_schema:
            for field in self.template.entity_schema.fields:
                req = "required" if field.required else "optional"
                parts.append(f"- {field.name} ({field.type.value}, {req}): {field.description}")
        parts.append("\nReturn a JSON object with key \"items\" containing an array of entity objects.")
        return "\n".join(parts)

    def _build_edge_system_prompt(self) -> str:
        parts = [
            "You are an expert relationship extraction specialist.",
            "Extract meaningful relationships between the provided entities.",
        ]
        if self.template.extraction.edge_prompt_extra:
            parts.append(f"\n### Context & Instructions:\n{self.template.extraction.edge_prompt_extra}")
        parts.append("\n### CRITICAL RULES:")
        parts.append("1. ONLY extract relationships connecting entities from the known entity list.")
        parts.append("2. DO NOT create relationships involving entities that are not listed.")
        parts.append("3. If an entity is not in the known list, exclude it from the relationship.")
        parts.append("\n### Output Format:")
        if self.template.relation_schema:
            for field in self.template.relation_schema.fields:
                req = "required" if field.required else "optional"
                parts.append(f"- {field.name} ({field.type.value}, {req}): {field.description}")
        parts.append("\nReturn a JSON object with key \"items\" containing an array of relationship objects.")
        return "\n".join(parts)

    def _format_known_entities(self, entities: List[dict]) -> str:
        if not entities:
            return "No entities identified."
        lines = []
        for i, e in enumerate(entities, 1):
            name = e.get("name", e.get("label", f"entity_{i}"))
            etype = e.get("entity_type", e.get("type", "unknown"))
            if self._entity_key_fn:
                key = self._entity_key_fn(e)
                lines.append(f"{i}. {name} ({etype}) [key={key}]")
            else:
                lines.append(f"{i}. {name} ({etype})")
        return "\n".join(lines)

    def _parse_entity_response(self, response: dict) -> List[dict]:
        content = response.get("content", "")
        if not content:
            return []
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"Entity JSON decode error: {e}, content: {content[:200]}")
            return []

        items = raw if isinstance(raw, list) else raw.get("items", raw.get("entities", []))
        if not isinstance(items, list):
            return []

        if self.entity_list_model is not None:
            try:
                validated = self.entity_list_model.model_validate({"items": items})
                return [item.model_dump() for item in validated.items]
            except ValidationError as e:
                logger.warning(f"Entity validation error: {e}")
                return items

        return items

    def _parse_relation_response(self, response: dict) -> List[dict]:
        content = response.get("content", "")
        if not content:
            return []
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"Relation JSON decode error: {e}, content: {content[:200]}")
            return []

        items = raw if isinstance(raw, list) else raw.get("items", raw.get("relationships", []))
        if not isinstance(items, list):
            return []

        if self.relation_list_model is not None:
            try:
                validated = self.relation_list_model.model_validate({"items": items})
                return [item.model_dump() for item in validated.items]
            except ValidationError as e:
                logger.warning(f"Relation validation error: {e}")
                return items

        return items