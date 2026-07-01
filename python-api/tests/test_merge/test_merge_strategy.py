import pytest
from app.services.merge_strategy import MergeStrategy


class TestMergeStrategyEnum:
    def test_all_seven_strategies_exist(self):
        strategies = [s.value for s in MergeStrategy]
        assert "exact" in strategies
        assert "keep_first" in strategies
        assert "keep_last" in strategies
        assert "field_overwrite" in strategies
        assert "llm_balanced" in strategies
        assert "llm_prefer_first" in strategies
        assert "llm_prefer_last" in strategies

    def test_is_deterministic_property(self):
        assert MergeStrategy.EXACT.is_deterministic is True
        assert MergeStrategy.KEEP_FIRST.is_deterministic is True
        assert MergeStrategy.KEEP_LAST.is_deterministic is True
        assert MergeStrategy.FIELD_OVERWRITE.is_deterministic is True
        assert MergeStrategy.LLM_BALANCED.is_deterministic is False
        assert MergeStrategy.LLM_PREFER_FIRST.is_deterministic is False
        assert MergeStrategy.LLM_PREFER_LAST.is_deterministic is False

    def test_is_llm_property(self):
        assert MergeStrategy.EXACT.is_llm is False
        assert MergeStrategy.KEEP_FIRST.is_llm is False
        assert MergeStrategy.LLM_BALANCED.is_llm is True
        assert MergeStrategy.LLM_PREFER_FIRST.is_llm is True
        assert MergeStrategy.LLM_PREFER_LAST.is_llm is True

    def test_rust_strategy_name(self):
        assert MergeStrategy.EXACT.rust_strategy_name is None
        assert MergeStrategy.KEEP_FIRST.rust_strategy_name == "keep_first"
        assert MergeStrategy.KEEP_LAST.rust_strategy_name == "keep_last"
        assert MergeStrategy.FIELD_OVERWRITE.rust_strategy_name == "field_overwrite"
        assert MergeStrategy.LLM_BALANCED.rust_strategy_name is None
        assert MergeStrategy.LLM_PREFER_FIRST.rust_strategy_name is None
        assert MergeStrategy.LLM_PREFER_LAST.rust_strategy_name is None

    def test_str_values(self):
        assert MergeStrategy.EXACT.value == "exact"
        assert MergeStrategy.KEEP_FIRST.value == "keep_first"
        assert MergeStrategy.KEEP_LAST.value == "keep_last"
        assert MergeStrategy.FIELD_OVERWRITE.value == "field_overwrite"

    def test_enum_membership(self):
        assert MergeStrategy("exact") == MergeStrategy.EXACT
        assert MergeStrategy("llm_balanced") == MergeStrategy.LLM_BALANCED