"""Merge strategy enum for entity/edge merging.

Deterministic strategies (keep_first, keep_last, field_overwrite) are handled
in Rust via IndexManager PyO3 methods. LLM strategies (llm_balanced,
llm_prefer_first, llm_prefer_last) are handled in Python by EntityMerger
which calls Ollama Cloud.
"""

from enum import Enum


class MergeStrategy(str, Enum):
    EXACT = "exact"
    KEEP_FIRST = "keep_first"
    KEEP_LAST = "keep_last"
    FIELD_OVERWRITE = "field_overwrite"
    LLM_BALANCED = "llm_balanced"
    LLM_PREFER_FIRST = "llm_prefer_first"
    LLM_PREFER_LAST = "llm_prefer_last"

    @property
    def is_deterministic(self) -> bool:
        return self in (
            MergeStrategy.EXACT,
            MergeStrategy.KEEP_FIRST,
            MergeStrategy.KEEP_LAST,
            MergeStrategy.FIELD_OVERWRITE,
        )

    @property
    def is_llm(self) -> bool:
        return self in (
            MergeStrategy.LLM_BALANCED,
            MergeStrategy.LLM_PREFER_FIRST,
            MergeStrategy.LLM_PREFER_LAST,
        )

    @property
    def rust_strategy_name(self) -> str | None:
        mapping = {
            MergeStrategy.KEEP_FIRST: "keep_first",
            MergeStrategy.KEEP_LAST: "keep_last",
            MergeStrategy.FIELD_OVERWRITE: "field_overwrite",
        }
        return mapping.get(self)