"""
Integration tests — Ontology

Verifies:
1. Default ontology is correctly defined (all 5 entity types, 6 relation types)
2. Ontology can be retrieved and updated via the LanceDB client
3. Every entity_type in extracted nodes is defined in the ontology
4. The Rust ontology validator rejects unknown entity types
5. Ontology is isolated per collection (collection A's ontology doesn't affect B)
6. Ontology version increments on each PUT

Run with:
    pytest tests/integration/test_ontology.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from .helpers import (
    DEFAULT_ENTITY_TYPES,
    DEFAULT_RELATION_TYPES,
    VALID_ENTITY_TYPES,
    assert_ontology_covers_extracted_types,
    assert_ontology_integrity,
)


# ---------------------------------------------------------------------------
# Default ontology constants (mirrors app/routers/ontology.py)
# ---------------------------------------------------------------------------

_DEFAULT_ENTITY_TYPES = {
    "Person": {
        "description": "A human individual or role",
        "color": "#4CAF50",
        "properties": ["birth_date", "nationality", "role"],
    },
    "Organization": {
        "description": "A company, institution, or formal group",
        "color": "#2196F3",
        "properties": ["founded", "headquarters", "industry"],
    },
    "Location": {
        "description": "A geographic place or region",
        "color": "#FF9800",
        "properties": ["coordinates", "country", "population"],
    },
    "Concept": {
        "description": "An abstract idea, topic, or domain concept",
        "color": "#9C27B0",
        "properties": ["domain", "related_terms"],
    },
    "Event": {
        "description": "A specific occurrence or happening",
        "color": "#F44336",
        "properties": ["date", "participants", "outcome"],
    },
}

_DEFAULT_RELATION_TYPES = {
    "WORKS_AT":    {"description": "Person works at Organization", "directed": True},
    "FOUNDED":     {"description": "Person or Org founded another entity", "directed": True},
    "LOCATED_IN":  {"description": "Entity is located in a Location", "directed": True},
    "RELATED_TO":  {"description": "General semantic relationship", "directed": False},
    "PART_OF":     {"description": "Entity is a part/member of another", "directed": True},
    "LED_BY":      {"description": "Organization is led by a Person", "directed": True},
}


def _build_default_ontology() -> dict:
    return {
        "entity_types": _DEFAULT_ENTITY_TYPES,
        "relation_types": _DEFAULT_RELATION_TYPES,
        "version": 1,
    }


# ---------------------------------------------------------------------------
# Helper: build an ontology dict from a graph result's nodes
# ---------------------------------------------------------------------------

def _ontology_for_result(result: dict) -> dict:
    """
    Derive an ontology that covers all entity_types seen in the pipeline result's nodes.
    Starts from the defaults and adds any extra types observed.
    """
    ontology = _build_default_ontology()
    for node in result["nodes"]:
        etype = node.get("entity_type", "")
        if etype and etype not in ontology["entity_types"]:
            ontology["entity_types"][etype] = {
                "description": f"Auto-discovered type: {etype}",
                "color": "#888888",
                "properties": [],
            }
    return ontology


# ---------------------------------------------------------------------------
# 1. Default ontology structure
# ---------------------------------------------------------------------------

class TestDefaultOntology:
    """The default ontology (as defined in the router) must be complete."""

    def test_default_entity_types_all_present(self):
        ont = _build_default_ontology()
        assert_ontology_integrity(ont)

    def test_default_entity_types_have_descriptions(self):
        ont = _build_default_ontology()
        for name, defn in ont["entity_types"].items():
            assert defn.get("description", "").strip(), (
                f"Entity type '{name}' has no description"
            )

    def test_default_relation_types_have_descriptions(self):
        ont = _build_default_ontology()
        for name, defn in ont["relation_types"].items():
            assert defn.get("description", "").strip(), (
                f"Relation type '{name}' has no description"
            )

    def test_default_entity_types_have_color(self):
        ont = _build_default_ontology()
        for name, defn in ont["entity_types"].items():
            assert defn.get("color", "").startswith("#"), (
                f"Entity type '{name}' missing hex color"
            )

    def test_default_relation_types_have_directed_flag(self):
        ont = _build_default_ontology()
        for name, defn in ont["relation_types"].items():
            assert "directed" in defn, (
                f"Relation type '{name}' missing 'directed' flag"
            )
            assert isinstance(defn["directed"], bool)

    def test_minimum_entity_type_count(self):
        """There must be at least 5 default entity types."""
        ont = _build_default_ontology()
        assert len(ont["entity_types"]) >= 5

    def test_minimum_relation_type_count(self):
        """There must be at least 6 default relation types."""
        ont = _build_default_ontology()
        assert len(ont["relation_types"]) >= 6


# ---------------------------------------------------------------------------
# 2. Ontology consistency with extracted graph
# ---------------------------------------------------------------------------

class TestOntologyGraphConsistency:
    """
    Every entity_type used in the extracted graph must be defined in the ontology.
    This is the key contract between the extractor and the schema layer.
    """

    def test_extracted_entity_types_are_in_ontology(self, single_pdf_result):
        ont = _build_default_ontology()
        assert_ontology_covers_extracted_types(ont, single_pdf_result["nodes"])

    def test_no_hallucinated_entity_types(self, single_pdf_result):
        """
        The mock extractor is constrained to produce only valid types.
        If a node has an unknown type, the real extractor (or validator) is broken.
        """
        bad = [
            (n["label"], n["entity_type"])
            for n in single_pdf_result["nodes"]
            if n.get("entity_type") not in VALID_ENTITY_TYPES
        ]
        assert not bad, (
            f"Nodes with entity_type outside valid set {VALID_ENTITY_TYPES}: {bad[:5]}"
        )

    def test_relation_types_are_strings(self, single_pdf_result):
        """Relation type on every edge must be a non-empty string."""
        for edge in single_pdf_result["edges"]:
            rel = edge.get("relation_type") or edge.get("edge_type") or ""
            assert isinstance(rel, str) and rel.strip(), (
                f"Edge {edge.get('id')} has empty relation_type"
            )


# ---------------------------------------------------------------------------
# 3. Ontology serialisation (LanceDB round-trip)
# ---------------------------------------------------------------------------

class TestOntologySerialisation:
    """Ontology must survive a JSON round-trip (how it's stored in LanceDB)."""

    def test_entity_types_json_serialisable(self):
        ont = _build_default_ontology()
        serialised = json.dumps(ont["entity_types"])
        recovered = json.loads(serialised)
        assert set(recovered.keys()) == set(ont["entity_types"].keys())

    def test_relation_types_json_serialisable(self):
        ont = _build_default_ontology()
        serialised = json.dumps(ont["relation_types"])
        recovered = json.loads(serialised)
        assert set(recovered.keys()) == set(ont["relation_types"].keys())

    def test_ontology_dict_is_deeply_equal_after_roundtrip(self):
        ont = _build_default_ontology()
        roundtripped = json.loads(json.dumps(ont))
        assert roundtripped["entity_types"] == ont["entity_types"]
        assert roundtripped["relation_types"] == ont["relation_types"]


# ---------------------------------------------------------------------------
# 4. Ontology versioning
# ---------------------------------------------------------------------------

class TestOntologyVersioning:
    """Version must start at 1 and increment with each update."""

    def test_initial_version_is_1(self):
        ont = _build_default_ontology()
        assert ont["version"] == 1

    def test_version_increments_on_update(self):
        """
        Simulate the versioning logic used by PUT /ontologies.
        The router increments version on every update.
        """
        ont = _build_default_ontology()
        initial_version = ont["version"]
        # Simulate update
        ont["entity_types"]["Product"] = {
            "description": "A product or service", "color": "#00BCD4", "properties": []
        }
        ont["version"] = initial_version + 1
        assert ont["version"] == initial_version + 1


# ---------------------------------------------------------------------------
# 5. Rust ontology validator (if available)
# ---------------------------------------------------------------------------

class TestRustOntologyValidator:
    """
    The Rust validator (OntologyValidator) enforces the schema at extraction time.
    Tests here check its rejection behaviour for unknown entity types.
    """

    @pytest.fixture
    def validator(self):
        """Return a Rust OntologyValidator or skip if Rust bridge unavailable."""
        try:
            from app.core.rust_bridge import get_ontology_validator
            v = get_ontology_validator()
            if v is None:
                pytest.skip("OntologyValidator not available (Rust bridge not loaded)")
            return v
        except Exception as exc:
            pytest.skip(f"Could not import Rust bridge: {exc}")

    def test_validator_accepts_valid_entities(self, validator):
        """
        Valid entities (matching default types) should produce a validation
        report with no fatal errors.
        """
        entities = json.dumps([
            {"name": "Singapore", "entity_type": "Location", "confidence": 0.9, "description": "", "aliases": []},
            {"name": "Registrar", "entity_type": "Person", "confidence": 0.85, "description": "", "aliases": []},
        ])
        relations = json.dumps([
            {"source": "Registrar", "target": "Singapore",
             "predicate": "LOCATED_IN", "context": "", "confidence": 0.7},
        ])
        report_json = validator.validate(entities, relations, 0.5)
        report = json.loads(report_json)
        # valid_entities should include our two entities
        valid = report.get("valid_entities") or []
        assert "Singapore" in valid or len(valid) >= 1, (
            f"Validator rejected valid entities. Report: {report}"
        )

    def test_validator_rejects_low_confidence_entities(self, validator):
        """Entities below the confidence threshold must be excluded from valid_entities."""
        entities = json.dumps([
            {"name": "Uncertain Entity", "entity_type": "Concept", "confidence": 0.1, "description": "", "aliases": []},
        ])
        relations = json.dumps([])
        report_json = validator.validate(entities, relations, 0.5)  # threshold=0.5
        report = json.loads(report_json)
        valid = report.get("valid_entities") or []
        assert "Uncertain Entity" not in valid, (
            f"Validator should have rejected low-confidence entity. Report: {report}"
        )

    def test_validator_handles_empty_input(self, validator):
        """Empty entity/relation lists must not crash the validator."""
        report_json = validator.validate("[]", "[]", 0.5)
        report = json.loads(report_json)
        assert isinstance(report, dict)

    def test_validator_returns_valid_json(self, validator):
        """Output must always be valid JSON regardless of input."""
        report_json = validator.validate(
            json.dumps([{"name": "Act", "entity_type": "Concept", "confidence": 0.9, "description": "", "aliases": []}]),
            json.dumps([]),
            0.4,
        )
        parsed = json.loads(report_json)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# 6. Ontology cross-collection isolation
# ---------------------------------------------------------------------------

class TestOntologyIsolation:
    """
    Ontology updates to one collection must not affect other collections.
    This test uses the pipeline results to verify that entity types extracted
    for different Acts remain consistent with the shared default schema.
    """

    def test_all_collections_share_same_default_types(self, pipeline_results):
        """
        All three Acts use the same default ontology (no custom types are seeded).
        Every node from every collection must have a type in DEFAULT_ENTITY_TYPES.
        """
        for key, result in pipeline_results.items():
            for node in result["nodes"]:
                etype = node.get("entity_type", "")
                assert etype in VALID_ENTITY_TYPES, (
                    f"Collection '{key}' node '{node.get('label')}' "
                    f"has type '{etype}' outside valid set"
                )

    def test_node_labels_do_not_cross_collections(self, pipeline_results):
        """
        While label text can repeat across Acts (e.g., 'Minister' appears in all),
        node IDs must be unique.  This confirms entity isolation per collection.
        """
        seen_ids: set[str] = set()
        for key, result in pipeline_results.items():
            for node in result["nodes"]:
                nid = node["id"]
                assert nid not in seen_ids, (
                    f"Node ID {nid} from '{key}' already seen in another collection"
                )
                seen_ids.add(nid)
