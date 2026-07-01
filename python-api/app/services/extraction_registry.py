"""Extraction method registry — pluggable extraction algorithms.

Each registered method encapsulates a distinct strategy for extracting
entities and relations from text.  The registry is populated at import
time with the built-in methods.  Domain templates may specify
``extraction.method`` to select a method, or callers can override it
via the API.

Only ``standard`` and ``two_stage`` are implemented today.  Future
methods (graph_rag, light_rag, etc.) will be registered here when
their algorithm classes are written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, runtime_checkable


_COMPATIBLE_TYPES: Dict[str, set[str]] = {
    "graph": {
        "graph", "hypergraph", "temporal_graph", "spatial_graph",
        "spatio_temporal_graph",
    },
}


@runtime_checkable
class ExtractionMethod(Protocol):
    """Protocol that every extraction method must satisfy."""

    name: str
    auto_type: str
    description: str

    def extract(
        self,
        text: str,
        template: "TemplateConfig",  # noqa: F821
        **kwargs,
    ) -> dict:
        """Run extraction and return ``{"entities": [...], "relations": [...]}``."""
        ...


@dataclass(frozen=True)
class RegisteredMethod:
    """Immutable descriptor stored in the registry."""

    name: str
    auto_type: str
    description: str
    implemented: bool = True


class _StandardExtractor:
    """Current production path: spaCy NER + single-pass LLM extraction."""

    name = "standard"
    auto_type = "graph"
    description = (
        "Single-pass extraction using spaCy NER candidates followed by "
        "a single LLM call that extracts entities and relations together."
    )

    async def extract(self, text: str, template: "TemplateConfig", **kwargs) -> dict:
        from app.llm.extractor import extract_from_chunk, ExtractionError
        job_id = kwargs.get("job_id")
        try:
            return await extract_from_chunk(text, job_id=job_id)
        except Exception as e:
            raise ExtractionError(f"Standard extraction failed: {e}") from e


class _TwoStageExtractor:
    """F2 two-stage extraction: nodes first, then edges with entity context."""

    name = "two_stage"
    auto_type = "graph"
    description = (
        "Two-stage extraction — first extracts entities, then extracts "
        "relations with the known-entity list injected into the prompt. "
        "Reduces hallucinated edges."
    )

    async def extract(self, text: str, template: "TemplateConfig", **kwargs) -> dict:
        from app.llm.two_stage_extractor import TwoStageExtractor
        from app.llm.extractor import ExtractionError
        job_id = kwargs.get("job_id")
        try:
            extractor = TwoStageExtractor(template, job_id=job_id)
            entities, relations = await extractor.extract_two_stage(text)
            return {"entities": entities, "relations": relations}
        except Exception as e:
            raise ExtractionError(f"Two-stage extraction failed: {e}") from e


class ExtractionRegistry:
    """Global registry of extraction methods."""

    def __init__(self) -> None:
        self._methods: Dict[str, RegisteredMethod] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        builtins: List[RegisteredMethod] = [
            RegisteredMethod(
                name="standard",
                auto_type="graph",
                description=_StandardExtractor.description,
                implemented=True,
            ),
            RegisteredMethod(
                name="two_stage",
                auto_type="graph",
                description=_TwoStageExtractor.description,
                implemented=True,
            ),
            RegisteredMethod(
                name="graph_rag",
                auto_type="graph",
                description=(
                    "GraphRAG — community detection on extracted entities, "
                    "then hierarchical summarization. Not yet implemented."
                ),
                implemented=False,
            ),
            RegisteredMethod(
                name="light_rag",
                auto_type="graph",
                description=(
                    "LightRAG — lightweight binary-edge extraction with "
                    "co-reasoning. Not yet implemented."
                ),
                implemented=False,
            ),
        ]
        for m in builtins:
            self._methods[m.name] = m

    def register(self, method: RegisteredMethod) -> None:
        self._methods[method.name] = method

    def get(self, name: str) -> Optional[RegisteredMethod]:
        return self._methods.get(name)

    def list(
        self,
        auto_type: Optional[str] = None,
        implemented_only: bool = False,
    ) -> List[RegisteredMethod]:
        results = list(self._methods.values())
        if auto_type:
            results = [m for m in results if m.auto_type == auto_type]
        if implemented_only:
            results = [m for m in results if m.implemented]
        return sorted(results, key=lambda m: m.name)

    def is_valid(self, name: str) -> bool:
        return name in self._methods

    def is_implemented(self, name: str) -> bool:
        m = self._methods.get(name)
        return m is not None and m.implemented

    def is_compatible(self, method_name: str, template_type: str) -> bool:
        """Check whether an extraction method is compatible with a template type.

        Returns True if the method's ``auto_type`` is listed as compatible
        with the given ``template_type``.  Returns False for unknown methods
        or incompatible combinations.
        """
        m = self._methods.get(method_name)
        if m is None:
            return False
        compatible = _COMPATIBLE_TYPES.get(m.auto_type, set())
        return template_type in compatible

    @property
    def valid_names(self) -> set[str]:
        return set(self._methods.keys())


REGISTRY = ExtractionRegistry()