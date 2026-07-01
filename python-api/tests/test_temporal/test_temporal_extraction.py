"""Tests for temporal and spatial dedup key patterns and extraction.

Tests cover:
- `_compile_key_pattern` for temporal/spatial/spatio-temporal keys
- Empty time/location should not produce dangling '@'
- Different times produce different keys (correct dedup)
- `IdentifierConfig.time_field` / `location_field` on templates
- Observation time/location injection into prompts (NOT YET IMPLEMENTED — skipped)
"""

import pytest
from app.services.template_factory import _compile_key_pattern


class TestTemporalDedupKeys:
    """Temporal edge key should include @time component."""

    def test_temporal_key_includes_time(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{time}")
        result = fn({"source": "A", "predicate": "cited", "target": "B", "time": "2024"})
        assert "@2024" in result

    def test_temporal_key_different_times_produce_different_keys(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{time}")
        key_2023 = fn({"source": "A", "predicate": "cited", "target": "B", "time": "2023"})
        key_2024 = fn({"source": "A", "predicate": "cited", "target": "B", "time": "2024"})
        assert key_2023 != key_2024

    def test_same_edge_same_time_produces_same_key(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{time}")
        key1 = fn({"source": "A", "predicate": "cited", "target": "B", "time": "2024"})
        key2 = fn({"source": "A", "predicate": "cited", "target": "B", "time": "2024"})
        assert key1 == key2

    def test_empty_time_no_at_symbol(self):
        """Edge with empty time should not produce dangling '@' in key."""
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{time}")
        result = fn({"source": "A", "predicate": "cited", "target": "B", "time": ""})
        result_with_none = fn({"source": "A", "predicate": "cited", "target": "B"})
        for key in [result, result_with_none]:
            assert not key.endswith("@"), f"Key ends with '@': {key!r}"
            assert "@ " not in key, f"Key has '@ ' (at-space): {key!r}"

    def test_spatial_key_includes_location(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{location}")
        result = fn({"source": "A", "predicate": "located", "target": "B", "location": "NYC"})
        assert "@NYC" in result or "NYC" in result

    def test_spatio_temporal_key(self):
        fn = _compile_key_pattern("{source}|{predicate}|{target}@{time}|{location}")
        result = fn({"source": "A", "predicate": "occurred", "target": "B", "time": "2024", "location": "DC"})
        assert "2024" in result
        assert "DC" in result

    def test_list_fields_joined_with_pipe(self):
        fn = _compile_key_pattern("{participants}|{event_type}")
        result = fn({"participants": ["Alice", "Bob"], "event_type": "meeting"})
        assert "Alice|Bob" in result


class TestTemporalTemplateConfig:
    """Template configs with time_field and location_field."""

    def test_identifier_config_with_time_field(self):
        from app.models.template import IdentifierConfig
        config = IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}@{time}",
            relation_source="source",
            relation_target="target",
            time_field="time",
        )
        assert config.time_field == "time"
        assert "@{time}" in config.relation_key

    def test_identifier_config_with_location_field(self):
        from app.models.template import IdentifierConfig
        config = IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}@{location}",
            relation_source="source",
            relation_target="target",
            location_field="location",
        )
        assert config.location_field == "location"
        assert "@{location}" in config.relation_key

    def test_identifier_config_with_both_time_and_location(self):
        from app.models.template import IdentifierConfig
        config = IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}@{time}|{location}",
            relation_source="source",
            relation_target="target",
            time_field="time",
            location_field="location",
        )
        assert config.time_field == "time"
        assert config.location_field == "location"

    def test_identifier_config_defaults_none(self):
        from app.models.template import IdentifierConfig
        config = IdentifierConfig(
            entity_key="{name}",
            relation_key="{source}|{predicate}|{target}",
            relation_source="source",
            relation_target="target",
        )
        assert config.time_field is None
        assert config.location_field is None