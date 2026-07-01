"""Tests for ExtractionRegistry — method registration, lookup, and validation."""

import pytest

from app.services.extraction_registry import (
    ExtractionRegistry,
    ExtractionMethod,
    RegisteredMethod,
    REGISTRY,
)


class TestExtractionRegistryBuiltins:
    def test_registry_has_standard(self):
        m = REGISTRY.get("standard")
        assert m is not None
        assert m.name == "standard"
        assert m.implemented is True

    def test_registry_has_two_stage(self):
        m = REGISTRY.get("two_stage")
        assert m is not None
        assert m.name == "two_stage"
        assert m.implemented is True

    def test_registry_has_future_methods(self):
        for name in ("graph_rag", "light_rag"):
            m = REGISTRY.get(name)
            assert m is not None, f"Missing future method: {name}"
            assert m.implemented is False

    def test_registry_builtin_count(self):
        methods = REGISTRY.list(implemented_only=False)
        assert len(methods) >= 4


class TestExtractionRegistryList:
    def test_list_all_includes_future(self):
        methods = REGISTRY.list(implemented_only=False)
        names = {m.name for m in methods}
        assert "graph_rag" in names
        assert "light_rag" in names

    def test_list_implemented_only(self):
        methods = REGISTRY.list(implemented_only=True)
        for m in methods:
            assert m.implemented is True

    def test_list_sorted_by_name(self):
        methods = REGISTRY.list(implemented_only=False)
        names = [m.name for m in methods]
        assert names == sorted(names)

    def test_list_filter_by_auto_type(self):
        methods = REGISTRY.list(auto_type="graph")
        for m in methods:
            assert m.auto_type == "graph"


class TestExtractionRegistryValidation:
    def test_is_valid(self):
        assert REGISTRY.is_valid("standard") is True
        assert REGISTRY.is_valid("two_stage") is True
        assert REGISTRY.is_valid("graph_rag") is True
        assert REGISTRY.is_valid("nonexistent") is False

    def test_is_implemented(self):
        assert REGISTRY.is_implemented("standard") is True
        assert REGISTRY.is_implemented("two_stage") is True
        assert REGISTRY.is_implemented("graph_rag") is False
        assert REGISTRY.is_implemented("light_rag") is False

    def test_valid_names_set(self):
        names = REGISTRY.valid_names
        assert "standard" in names
        assert "two_stage" in names
        assert "graph_rag" in names

    def test_is_compatible_graph_method_with_graph_type(self):
        assert REGISTRY.is_compatible("standard", "graph") is True
        assert REGISTRY.is_compatible("two_stage", "graph") is True

    def test_is_compatible_graph_method_with_hypergraph(self):
        assert REGISTRY.is_compatible("standard", "hypergraph") is True
        assert REGISTRY.is_compatible("two_stage", "hypergraph") is True

    def test_is_compatible_graph_method_with_temporal(self):
        assert REGISTRY.is_compatible("standard", "temporal_graph") is True

    def test_is_compatible_invalid_method(self):
        assert REGISTRY.is_compatible("nonexistent", "graph") is False


class TestExtractionRegistryRegister:
    def test_register_custom_method(self):
        reg = ExtractionRegistry()
        custom = RegisteredMethod(
            name="custom_test",
            auto_type="graph",
            description="Test method",
            implemented=True,
        )
        reg.register(custom)
        assert reg.get("custom_test") is not None
        assert reg.get("custom_test").name == "custom_test"

    def test_register_overwrites_existing(self):
        reg = ExtractionRegistry()
        updated = RegisteredMethod(
            name="standard",
            auto_type="graph",
            description="Updated",
            implemented=True,
        )
        reg.register(updated)
        assert reg.get("standard").description == "Updated"


class TestExtractionMethodProtocol:
    def test_standard_extractor_satisfies_protocol(self):
        from app.services.extraction_registry import _StandardExtractor
        extractor = _StandardExtractor()
        assert isinstance(extractor, ExtractionMethod)

    def test_two_stage_extractor_satisfies_protocol(self):
        from app.services.extraction_registry import _TwoStageExtractor
        extractor = _TwoStageExtractor()
        assert isinstance(extractor, ExtractionMethod)

    def test_standard_extractor_has_required_attrs(self):
        from app.services.extraction_registry import _StandardExtractor
        extractor = _StandardExtractor()
        assert extractor.name == "standard"
        assert extractor.auto_type == "graph"
        assert extractor.description

    def test_two_stage_extractor_has_required_attrs(self):
        from app.services.extraction_registry import _TwoStageExtractor
        extractor = _TwoStageExtractor()
        assert extractor.name == "two_stage"
        assert extractor.auto_type == "graph"
        assert extractor.description

    def test_standard_extract_returns_dict(self):
        from app.services.extraction_registry import _StandardExtractor
        extractor = _StandardExtractor()
        result = extractor.extract("test text", None)
        assert "entities" in result
        assert "relations" in result

    def test_two_stage_extract_returns_dict(self):
        from app.services.extraction_registry import _TwoStageExtractor
        extractor = _TwoStageExtractor()
        result = extractor.extract("test text", None)
        assert "entities" in result
        assert "relations" in result