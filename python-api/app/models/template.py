"""Pydantic models for YAML extraction template configuration."""

from enum import Enum
from typing import Any, Optional, List
from pydantic import BaseModel, field_validator, model_validator


class FieldType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    LIST = "list"


_VALID_MERGE_STRATEGIES = {
    "exact", "keep_first", "keep_last", "field_overwrite",
    "llm_balanced", "llm_prefer_first", "llm_prefer_last",
}


class FieldDef(BaseModel):
    name: str
    type: FieldType
    description: str = ""
    required: bool = True
    default: Any = None


class EntitySchema(BaseModel):
    fields: List[FieldDef]
    key: str
    display_label: str

    @field_validator("key")
    @classmethod
    def key_must_contain_placeholder(cls, v: str) -> str:
        if "{" not in v or "}" not in v:
            raise ValueError("entity key must contain at least one {field} placeholder")
        return v

    @field_validator("display_label")
    @classmethod
    def label_must_contain_placeholder(cls, v: str) -> str:
        if "{" not in v or "}" not in v:
            raise ValueError("display_label must contain at least one {field} placeholder")
        return v

    @model_validator(mode="after")
    def field_names_must_be_unique(self):
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            duplicates = [n for n in names if names.count(n) > 1]
            raise ValueError(f"duplicate field names in entity_schema: {set(duplicates)}")
        return self


class RelationSchema(BaseModel):
    fields: List[FieldDef]
    key: str
    source_field: str
    target_field: str
    display_label: str
    participants_field: Optional[str] = None

    @field_validator("key")
    @classmethod
    def key_must_contain_placeholder(cls, v: str) -> str:
        if "{" not in v or "}" not in v:
            raise ValueError("relation key must contain at least one {field} placeholder")
        return v

    @model_validator(mode="after")
    def field_names_must_be_unique(self):
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            duplicates = [n for n in names if names.count(n) > 1]
            raise ValueError(f"duplicate field names in relation_schema: {set(duplicates)}")
        return self


_VALID_EXTRACTION_METHODS = {"standard", "two_stage", "graph_rag", "light_rag"}


class ExtractionConfig(BaseModel):
    mode: str = "two_stage"
    method: str = "standard"
    node_prompt_extra: str = ""
    edge_prompt_extra: str = ""
    merge_strategy_nodes: str = "exact"
    merge_strategy_edges: str = "exact"

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, v: str) -> str:
        if v not in ("one_stage", "two_stage"):
            raise ValueError(f"extraction mode must be 'one_stage' or 'two_stage', got '{v}'")
        return v

    @field_validator("method")
    @classmethod
    def method_must_be_valid(cls, v: str) -> str:
        if v not in _VALID_EXTRACTION_METHODS:
            raise ValueError(
                f"extraction method must be one of {sorted(_VALID_EXTRACTION_METHODS)}, got '{v}'"
            )
        return v

    @field_validator("merge_strategy_nodes")
    @classmethod
    def merge_strategy_nodes_must_be_valid(cls, v: str) -> str:
        if v not in _VALID_MERGE_STRATEGIES:
            raise ValueError(
                f"merge_strategy_nodes must be one of {sorted(_VALID_MERGE_STRATEGIES)}, got '{v}'"
            )
        return v

    @field_validator("merge_strategy_edges")
    @classmethod
    def merge_strategy_edges_must_be_valid(cls, v: str) -> str:
        if v not in _VALID_MERGE_STRATEGIES:
            raise ValueError(
                f"merge_strategy_edges must be one of {sorted(_VALID_MERGE_STRATEGIES)}, got '{v}'"
            )
        return v


class IdentifierConfig(BaseModel):
    entity_key: str
    relation_key: str
    relation_source: str
    relation_target: str
    time_field: Optional[str] = None
    location_field: Optional[str] = None


class TemplateType(str, Enum):
    MODEL = "model"
    LIST = "list"
    SET = "set"
    GRAPH = "graph"
    HYPERGRAPH = "hypergraph"
    TEMPORAL_GRAPH = "temporal_graph"
    SPATIAL_GRAPH = "spatial_graph"
    SPATIO_TEMPORAL_GRAPH = "spatio_temporal_graph"


_GRAPH_TYPES = {"graph", "hypergraph", "temporal_graph", "spatial_graph", "spatio_temporal_graph"}


class TemplateConfig(BaseModel):
    name: str
    type: TemplateType
    language: List[str] = ["en"]
    domain: str = "general"
    description: str = ""
    entity_schema: Optional[EntitySchema] = None
    relation_schema: Optional[RelationSchema] = None
    extraction: ExtractionConfig = ExtractionConfig()
    identifiers: Optional[IdentifierConfig] = None

    @model_validator(mode="after")
    def graph_types_require_schemas_and_identifiers(self):
        if self.type.value in _GRAPH_TYPES:
            if self.entity_schema is None:
                raise ValueError(
                    f"entity_schema is required for template type '{self.type.value}'"
                )
            if self.relation_schema is None:
                raise ValueError(
                    f"relation_schema is required for template type '{self.type.value}'"
                )
            if self.identifiers is None:
                raise ValueError(
                    f"identifiers is required for template type '{self.type.value}'"
                )
        return self


class TemplateSummary(BaseModel):
    key: str
    name: str
    domain: str
    type: str
    description: str