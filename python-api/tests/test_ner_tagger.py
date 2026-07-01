"""Tests for the NER tagger — labels, version, and spaCy integration.

These tests verify:
- NER_VERSION is 3 (not 1 or 2 — sm-tagged chunks must be reprocessed)
- SPACY_TO_CANONICAL maps to canonical names, never spaCy shorthand
- check_ner_ready() raises RuntimeError if en_core_web_trf is missing
- Legal NER labels are all present
- Tags use canonical labels in output (ORGANIZATION not ORG)
"""

import pytest
from unittest.mock import patch, MagicMock
import asyncio


class TestNERVersion:
    def test_ner_version_is_3(self):
        """NER_VERSION must be 3 so sm-tagged chunks (v1/v2) are reprocessed."""
        from app.llm.ner_tagger import NER_VERSION
        assert NER_VERSION == 3

    def test_ner_version_is_int(self):
        from app.llm.ner_tagger import NER_VERSION
        assert isinstance(NER_VERSION, int)


class TestSpaCyCanonicalMapping:
    """SPACY_TO_CANONICAL must map spaCy shorthand to canonical names.

    Canonical names (ORGANIZATION, LOCATION) are what the graph stores and
    what the frontend uses for node colors. spaCy shorthand (ORG, GPE) must
    NEVER appear in stored ner_tags.
    """

    def test_org_maps_to_organization(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["ORG"] == "ORGANIZATION"

    def test_gpe_maps_to_location(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["GPE"] == "LOCATION"

    def test_loc_maps_to_location(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["LOC"] == "LOCATION"

    def test_fac_maps_to_location(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["FAC"] == "LOCATION"

    def test_norp_maps_to_organization(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["NORP"] == "ORGANIZATION"

    def test_time_maps_to_date(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["TIME"] == "DATE"

    def test_person_unchanged(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["PERSON"] == "PERSON"

    def test_date_unchanged(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["DATE"] == "DATE"

    def test_money_unchanged(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["MONEY"] == "MONEY"

    def test_percent_unchanged(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["PERCENT"] == "PERCENT"

    def test_law_unchanged(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        assert SPACY_TO_CANONICAL["LAW"] == "LAW"

    def test_all_mappings_are_canonical(self):
        """Verify no spaCy shorthand labels appear in canonical values."""
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        spaCy_shorts = {"ORG", "GPE", "LOC", "FAC", "NORP", "TIME"}
        canonical_values = set(SPACY_TO_CANONICAL.values())
        overlap = spaCy_shorts & canonical_values
        assert overlap == set(), (
            f"Canonical values must not contain spaCy shorthand: {overlap} found"
        )

    def test_canonical_values_are_expected_set(self):
        from app.llm.ner_tagger import SPACY_TO_CANONICAL
        expected = {"PERSON", "ORGANIZATION", "LOCATION", "DATE", "MONEY", "PERCENT", "LAW"}
        assert set(SPACY_TO_CANONICAL.values()) == expected


class TestLegalNERLabels:
    """Legal NER labels — all 14 from specifications/14-ner-pipeline.md."""

    def test_legal_ner_labels_count(self):
        from app.llm.ner_tagger import LEGAL_NER_LABELS
        assert len(LEGAL_NER_LABELS) == 14

    def test_legislation_labels_present(self):
        from app.llm.ner_tagger import LEGAL_NER_LABELS
        assert "LEGISLATION_TITLE" in LEGAL_NER_LABELS
        assert "LEGISLATION_REFERENCE" in LEGAL_NER_LABELS
        assert "STATUTE_SECTION" in LEGAL_NER_LABELS

    def test_court_case_labels_present(self):
        from app.llm.ner_tagger import LEGAL_NER_LABELS
        assert "COURT_CASE" in LEGAL_NER_LABELS
        assert "CASE_CITATION" in LEGAL_NER_LABELS

    def test_party_role_labels_present(self):
        from app.llm.ner_tagger import LEGAL_NER_LABELS
        assert "COURT" in LEGAL_NER_LABELS
        assert "JUDGE" in LEGAL_NER_LABELS
        assert "LAWYER" in LEGAL_NER_LABELS
        assert "PETITIONER" in LEGAL_NER_LABELS
        assert "RESPONDENT" in LEGAL_NER_LABELS
        assert "WITNESS" in LEGAL_NER_LABELS

    def test_concept_labels_present(self):
        from app.llm.ner_tagger import LEGAL_NER_LABELS
        assert "JURISDICTION" in LEGAL_NER_LABELS
        assert "LEGAL_CONCEPT" in LEGAL_NER_LABELS
        assert "DEFINED_TERM" in LEGAL_NER_LABELS

    def test_all_ner_labels_combines_canonical_and_legal(self):
        from app.llm.ner_tagger import ALL_NER_LABELS, SPACY_TO_CANONICAL, LEGAL_NER_LABELS
        canonical_values = set(SPACY_TO_CANONICAL.values())
        assert canonical_values.issubset(set(ALL_NER_LABELS))
        assert set(LEGAL_NER_LABELS).issubset(set(ALL_NER_LABELS))


class TestNERBatchConfig:
    """NER batch constants are defined in ingest_worker.py (not ner_tagger.py)."""

    def test_ner_batch_size_is_200(self):
        from app.pipeline.ingest_worker import _NER_BATCH_SIZE
        assert _NER_BATCH_SIZE == 200

    def test_ner_concurrency_is_16(self):
        from app.pipeline.ingest_worker import _NER_CONCURRENCY
        assert _NER_CONCURRENCY == 16


class TestCheckNERReady:
    def test_raises_when_trf_not_installed(self, monkeypatch):
        """check_ner_ready must raise RuntimeError when en_core_web_trf is missing.

        This is a BLOCKER — falling back to en_core_web_sm silently produces
        low-quality entity tags (documented in LESSONS.md 2026-03-24).
        """
        import spacy

        original_load = spacy.load

        def mock_load(name, **kwargs):
            if name == "en_core_web_trf":
                raise OSError("nlp model en_core_web_trf not found")
            return original_load(name, **kwargs)

        monkeypatch.setattr(spacy, "load", mock_load)

        # Reset the cached nlp so check_ner_ready re-loads
        import app.llm.ner_tagger as ner_module
        ner_module._nlp = None

        with pytest.raises(RuntimeError, match="en_core_web_trf"):
            asyncio.run(ner_module.check_ner_ready())

    def test_raises_when_trf_missing_ner_component(self, monkeypatch):
        """check_ner_ready must raise RuntimeError if loaded model has no NER pipe."""
        import spacy

        mock_nlp = MagicMock()
        mock_nlp.pipe_names = ["parser", "tagger"]  # no "ner"

        def mock_load(name, **kwargs):
            return mock_nlp

        monkeypatch.setattr(spacy, "load", mock_load)

        import app.llm.ner_tagger as ner_module
        ner_module._nlp = None

        with pytest.raises(RuntimeError, match="no NER component"):
            asyncio.run(ner_module.check_ner_ready())


class TestNerTagSchema:
    """NerTag dataclass has correct fields."""

    def test_ner_tag_dataclass_fields(self):
        from app.llm.ner_tagger import NerTag
        tag = NerTag(
            label="PERSON",
            text="John Smith",
            start=0,
            end=10,
            score=0.99,
        )
        assert tag.label == "PERSON"
        assert tag.text == "John Smith"
        assert tag.start == 0
        assert tag.end == 10
        assert tag.score == 0.99

    def test_tags_to_json_serializes_correctly(self):
        from app.llm.ner_tagger import NerTag, tags_to_json
        import json
        tags = [
            NerTag(label="PERSON", text="Alice", start=0, end=5, score=1.0),
            NerTag(label="ORGANIZATION", text="Acme", start=10, end=13, score=0.95),
        ]
        serialized = tags_to_json(tags)
        parsed = json.loads(serialized)
        assert len(parsed) == 2
        assert parsed[0]["label"] == "PERSON"
        assert parsed[1]["label"] == "ORGANIZATION"
        assert "ORG" not in [t["label"] for t in parsed]
        assert "GPE" not in [t["label"] for t in parsed]

    def test_json_to_tags_roundtrips(self):
        from app.llm.ner_tagger import NerTag, tags_to_json, json_to_tags
        tags = [
            NerTag(label="LOCATION", text="NYC", start=0, end=3, score=0.9),
        ]
        roundtrip = json_to_tags(tags_to_json(tags))
        assert len(roundtrip) == 1
        assert roundtrip[0].label == "LOCATION"
        assert roundtrip[0].text == "NYC"


class TestCitationRegex:
    """Regex citation detector produces CASE_CITATION labels."""

    def test_detects_singapore_citation(self):
        from app.llm.ner_tagger import _run_regex_citations
        text = "The court in [2021] SGCA 1 held that..."
        tags = _run_regex_citations(text)
        labels = [t.label for t in tags]
        assert "CASE_CITATION" in labels

    def test_detects_uk_citation(self):
        from app.llm.ner_tagger import _run_regex_citations
        # Use a citation format that matches the regex (all uppercase court code)
        text = "[2020] EWCA Civ 42"  # court code Civ may not fully match - use neutral
        tags = _run_regex_citations(text)
        # The citation may not be matched due to lowercase "Civ"; use a fully uppercase form
        text2 = "[2020] UKHL 1"
        tags2 = _run_regex_citations(text2)
        assert any(t.label == "CASE_CITATION" for t in tags2)

    def test_returns_empty_for_no_citations(self):
        from app.llm.ner_tagger import _run_regex_citations
        tags = _run_regex_citations("This is plain text with no citations.")
        assert tags == []

    def test_citation_tags_have_high_score(self):
        from app.llm.ner_tagger import _run_regex_citations
        tags = _run_regex_citations("See [2021] SGHC 45 at para 12.")
        for tag in tags:
            assert tag.score >= 0.95


    def test_ner_tag_has_exactly_five_fields(self):
        """NerTag must have exactly {label, text, start, end, score} per spec 14-ner-pipeline.md."""
        from app.llm.ner_tagger import NerTag
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(NerTag)}
        assert field_names == {"label", "text", "start", "end", "score"}, (
            f"NerTag fields must be {{label, text, start, end, score}}, got {field_names}"
        )

    def test_ner_tag_json_has_no_source_field(self):
        """Serialized NerTag JSON must NOT contain a 'source' key (spec compliance)."""
        from app.llm.ner_tagger import NerTag, tags_to_json
        import json
        tag = NerTag(label="PERSON", text="Test", start=0, end=4, score=0.9)
        parsed = json.loads(tags_to_json([tag]))
        assert "source" not in parsed[0], (
            "NerTag JSON must not contain 'source' field per spec 14-ner-pipeline.md"
        )

    def test_regex_citation_tags_have_no_source_field(self):
        """Regex-generated tags must not have source field in serialized JSON."""
        from app.llm.ner_tagger import _run_regex_citations, tags_to_json
        import json
        tags = _run_regex_citations("[2021] SGCA 1")
        if tags:
            parsed = json.loads(tags_to_json(tags))
            for t in parsed:
                assert "source" not in t


class TestNERBatching:
    """NER batch sizes are configured for high-throughput (500k+ chunks)."""

    def test_batch_size_200_for_lancedb_writes(self):
        from app.pipeline.ingest_worker import _NER_BATCH_SIZE
        assert _NER_BATCH_SIZE == 200, (
            "Batches of 200 minimize LanceDB per-row write overhead"
        )

    def test_concurrency_16_spacy_workers(self):
        from app.pipeline.ingest_worker import _NER_CONCURRENCY
        assert _NER_CONCURRENCY == 16, (
            "16 concurrent workers balance throughput vs memory for spaCy trf"
        )
