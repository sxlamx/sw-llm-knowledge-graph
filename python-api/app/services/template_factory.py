"""Template factory — converts TemplateConfig into runtime artifacts.

Produces:
  - Pydantic schema for entity/relation validation
  - LLM prompt strings (node_prompt, edge_prompt)
  - Key extractor callables (entity_key_fn, relation_key_fn)
  - Display label renderer callables (entity_label_fn, relation_label_fn)
  - Dynamic Pydantic models for structured LLM output validation
"""

import re
from typing import Callable, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, Field, create_model

from app.models.template import (
    TemplateConfig,
    EntitySchema,
    RelationSchema,
    FieldType,
)


class TemplateArtifacts:
    """Runtime artifacts produced from a TemplateConfig."""

    def __init__(
        self,
        config: TemplateConfig,
        entity_schema: Optional[dict] = None,
        relation_schema: Optional[dict] = None,
        node_prompt: str = "",
        edge_prompt: str = "",
        entity_key_fn: Optional[Callable[[dict], str]] = None,
        relation_key_fn: Optional[Callable[[dict], str]] = None,
        entity_label_fn: Optional[Callable[[dict], str]] = None,
        relation_label_fn: Optional[Callable[[dict], str]] = None,
    ):
        self.config = config
        self.entity_schema = entity_schema
        self.relation_schema = relation_schema
        self.node_prompt = node_prompt
        self.edge_prompt = edge_prompt
        self.entity_key_fn = entity_key_fn
        self.relation_key_fn = relation_key_fn
        self.entity_label_fn = entity_label_fn
        self.relation_label_fn = relation_label_fn


class TemplateFactory:
    """Converts TemplateConfig into schemas, prompts, keys, and label renderers."""

    @staticmethod
    def create(config: TemplateConfig, language: str = "en") -> TemplateArtifacts:
        entity_schema = TemplateFactory._build_entity_schema(config)
        relation_schema = TemplateFactory._build_relation_schema(config)
        prompts = TemplateFactory._build_prompts(config, language)
        keys = TemplateFactory._build_key_extractors(config)
        display = TemplateFactory._build_display_renderers(config)

        return TemplateArtifacts(
            config=config,
            entity_schema=entity_schema,
            relation_schema=relation_schema,
            node_prompt=prompts[0],
            edge_prompt=prompts[1],
            entity_key_fn=keys[0],
            relation_key_fn=keys[1],
            entity_label_fn=display[0],
            relation_label_fn=display[1],
        )

    @staticmethod
    def _build_entity_schema(config: TemplateConfig) -> Optional[dict]:
        if config.entity_schema is None:
            return None
        schema = config.entity_schema
        properties = {}
        required = []
        for field in schema.fields:
            prop_type = _field_type_to_json_schema(field.type)
            properties[field.name] = {
                "type": prop_type,
                "description": field.description,
            }
            if field.required:
                required.append(field.name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    @staticmethod
    def _build_relation_schema(config: TemplateConfig) -> Optional[dict]:
        if config.relation_schema is None:
            return None
        schema = config.relation_schema
        properties = {}
        required = []
        for field in schema.fields:
            prop_type = _field_type_to_json_schema(field.type)
            properties[field.name] = {
                "type": prop_type,
                "description": field.description,
            }
            if field.required:
                required.append(field.name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    @staticmethod
    def _build_prompts(
        config: TemplateConfig, language: str
    ) -> Tuple[str, str]:
        lang_note = f" Respond in {language}." if language != "en" else ""
        extra_node = config.extraction.node_prompt_extra
        extra_edge = config.extraction.edge_prompt_extra

        node_prompt = (
            f"Extract all entities from the following text as a JSON array. "
            f"Each entity must have these fields: "
        )
        if config.entity_schema:
            field_descs = ", ".join(
                f'"{f.name}" ({f.type.value})'
                + (" (required)" if f.required else " (optional)")
                for f in config.entity_schema.fields
            )
            node_prompt += field_descs + "."
        else:
            node_prompt += '"name" (string), "entity_type" (string), "description" (string, optional).'

        node_prompt += (
            f" Return the result as a JSON object with key \"entities\".{lang_note}"
        )
        if extra_node:
            node_prompt += f" {extra_node}"

        edge_prompt = (
            f"Extract all relationships between entities from the following text as a JSON array. "
            f"Each relationship must have these fields: "
        )
        if config.relation_schema:
            field_descs = ", ".join(
                f'"{f.name}" ({f.type.value})'
                + (" (required)" if f.required else " (optional)")
                for f in config.relation_schema.fields
            )
            edge_prompt += field_descs + "."
        else:
            edge_prompt += (
                '"source" (string, entity name), "target" (string, entity name), '
                '"predicate" (string), "context" (string, optional).'
            )

        edge_prompt += (
            f' Return the result as a JSON object with key "relationships".{lang_note}'
        )
        if extra_edge:
            edge_prompt += f" {extra_edge}"

        return node_prompt, edge_prompt

    @staticmethod
    def _build_key_extractors(
        config: TemplateConfig,
    ) -> Tuple[Optional[Callable], Optional[Callable]]:
        entity_key_fn = None
        relation_key_fn = None

        if config.identifiers:
            entity_key_fn = _compile_key_pattern(config.identifiers.entity_key)
            relation_key_fn = _compile_key_pattern(config.identifiers.relation_key)
        elif config.entity_schema:
            entity_key_fn = _compile_key_pattern(config.entity_schema.key)
            if config.relation_schema:
                relation_key_fn = _compile_key_pattern(config.relation_schema.key)

        return entity_key_fn, relation_key_fn

    @staticmethod
    def _build_display_renderers(
        config: TemplateConfig,
    ) -> Tuple[Optional[Callable], Optional[Callable]]:
        entity_label_fn = None
        relation_label_fn = None

        if config.entity_schema:
            entity_label_fn = _compile_label_pattern(config.entity_schema.display_label)
        if config.relation_schema:
            relation_label_fn = _compile_label_pattern(config.relation_schema.display_label)

        return entity_label_fn, relation_label_fn


def _compile_key_pattern(pattern: str) -> Callable[[dict], str]:
    """Compile a key pattern like '{source}|{predicate}|{target}@{time}' into a function.

    Renders the pattern by substituting {field} placeholders with values from data.
    Missing fields are replaced with empty strings. List fields are joined with '|'.
    Literal characters in the pattern (e.g. '@' between target and time) are preserved.

    After rendering, any trailing '@' or '|' that precedes an empty value is removed
    to avoid producing keys like 'A|cited|B@' when time is empty.
    """
    placeholders = re.findall(r'\{(\w+)\}', pattern)

    def extractor(data: dict) -> str:
        if not placeholders:
            return pattern
        format_kwargs = {}
        for p in placeholders:
            value = data.get(p)
            if value is None or value == "":
                format_kwargs[p] = ""
            elif isinstance(value, list):
                format_kwargs[p] = "|".join(str(v) for v in value)
            else:
                format_kwargs[p] = str(value)
        try:
            result = pattern.format(**format_kwargs)
        except (KeyError, IndexError):
            return pattern
        result = re.sub(r'[@|]+$', '', result)
        return result

    return extractor


def _compile_label_pattern(pattern: str) -> Callable[[dict], str]:
    """Compile a display label pattern like '{name} ({entity_type})' into a function."""
    placeholders = re.findall(r'\{(\w+)\}', pattern)

    def renderer(data: dict) -> str:
        try:
            format_kwargs = {}
            for p in placeholders:
                if p in data:
                    format_kwargs[p] = data[p]
                else:
                    format_kwargs[p] = data.get("name", data.get("label", "unknown"))
            return pattern.format(**format_kwargs)
        except (KeyError, IndexError):
            return str(data.get("name", data.get("label", "unknown")))

    return renderer


def _field_type_to_json_schema(field_type: FieldType) -> str:
    mapping = {
        FieldType.STRING: "string",
        FieldType.INTEGER: "integer",
        FieldType.FLOAT: "number",
        FieldType.BOOLEAN: "boolean",
        FieldType.LIST: "array",
    }
    return mapping.get(field_type, "string")


_FIELD_TYPE_MAP: Dict[FieldType, type] = {
    FieldType.STRING: str,
    FieldType.INTEGER: int,
    FieldType.FLOAT: float,
    FieldType.BOOLEAN: bool,
    FieldType.LIST: List[str],
}


def build_entity_pydantic_model(template: TemplateConfig) -> Type[BaseModel]:
    if template.entity_schema is None:
        raise ValueError(f"Template '{template.name}' has no entity_schema")
    fields = {}
    for f in template.entity_schema.fields:
        field_type = _FIELD_TYPE_MAP[f.type]
        if f.required:
            fields[f.name] = (field_type, Field(description=f.description))
        else:
            fields[f.name] = (Optional[field_type], Field(default=None, description=f.description))
    return create_model(f"{template.name}_Entity", **fields)


def build_relation_pydantic_model(template: TemplateConfig) -> Type[BaseModel]:
    if template.relation_schema is None:
        raise ValueError(f"Template '{template.name}' has no relation_schema")
    fields = {}
    for f in template.relation_schema.fields:
        field_type = _FIELD_TYPE_MAP[f.type]
        if f.required:
            fields[f.name] = (field_type, Field(description=f.description))
        else:
            fields[f.name] = (Optional[field_type], Field(default=None, description=f.description))
    return create_model(f"{template.name}_Relation", **fields)


def build_entity_list_model(entity_model: Type[BaseModel]) -> Type[BaseModel]:
    return create_model(
        f"{entity_model.__name__}List",
        items=(List[entity_model], Field(default_factory=list)),
    )


def build_relation_list_model(relation_model: Type[BaseModel]) -> Type[BaseModel]:
    return create_model(
        f"{relation_model.__name__}List",
        items=(List[relation_model], Field(default_factory=list)),
    )