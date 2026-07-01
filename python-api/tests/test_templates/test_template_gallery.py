"""Tests for TemplateGallery — YAML loader, indexing, and singleton."""

import inspect
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch

from app.services.template_gallery import TemplateGallery


def _write_graph_yaml(base_dir: Path) -> None:
    general = base_dir / "general"
    general.mkdir(parents=True, exist_ok=True)
    (general / "graph.yaml").write_text(yaml.dump({
        "name": "graph",
        "type": "graph",
        "domain": "general",
        "language": ["en"],
        "description": "Test graph",
        "entity_schema": {
            "fields": [
                {"name": "name", "type": "string", "description": "Entity name", "required": True},
                {"name": "entity_type", "type": "string", "description": "Type", "required": True},
            ],
            "key": "{name}",
            "display_label": "{name} ({entity_type})",
        },
        "relation_schema": {
            "fields": [
                {"name": "source", "type": "string", "description": "Source", "required": True},
                {"name": "target", "type": "string", "description": "Target", "required": True},
                {"name": "predicate", "type": "string", "description": "Pred", "required": True},
            ],
            "key": "{source}|{predicate}|{target}",
            "source_field": "source",
            "target_field": "target",
            "display_label": "{predicate}",
        },
        "identifiers": {
            "entity_key": "{name}",
            "relation_key": "{source}|{predicate}|{target}",
            "relation_source": "source",
            "relation_target": "target",
        },
    }))


def _write_list_yaml(base_dir: Path) -> None:
    general = base_dir / "general"
    general.mkdir(parents=True, exist_ok=True)
    (general / "list.yaml").write_text(yaml.dump({
        "name": "list",
        "type": "list",
        "domain": "general",
        "language": ["en"],
        "description": "Test list",
        "entity_schema": {
            "fields": [
                {"name": "item", "type": "string", "description": "Item text", "required": True},
            ],
            "key": "{item}",
            "display_label": "{item}",
        },
    }))


def _write_legal_yaml(base_dir: Path) -> None:
    legal = base_dir / "legal"
    legal.mkdir(parents=True, exist_ok=True)
    (legal / "case_law.yaml").write_text(yaml.dump({
        "name": "case_law",
        "type": "graph",
        "domain": "legal",
        "language": ["en"],
        "description": "Legal case law",
        "entity_schema": {
            "fields": [
                {"name": "name", "type": "string", "description": "Name", "required": True},
                {"name": "entity_type", "type": "string", "description": "Type", "required": True},
            ],
            "key": "{name}",
            "display_label": "{name}",
        },
        "relation_schema": {
            "fields": [
                {"name": "source", "type": "string", "description": "Source", "required": True},
                {"name": "target", "type": "string", "description": "Target", "required": True},
                {"name": "predicate", "type": "string", "description": "Pred", "required": True},
            ],
            "key": "{source}|{predicate}|{target}",
            "source_field": "source",
            "target_field": "target",
            "display_label": "{predicate}",
        },
        "identifiers": {
            "entity_key": "{name}",
            "relation_key": "{source}|{predicate}|{target}",
            "relation_source": "source",
            "relation_target": "target",
        },
    }))


class TestTemplateGalleryLoading:
    def test_load_single_template(self, tmp_path):
        _write_graph_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert len(gallery._templates) == 1
        assert gallery.get("general/graph") is not None

    def test_load_multiple_templates(self, tmp_path):
        _write_graph_yaml(tmp_path)
        _write_list_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert len(gallery._templates) == 2

    def test_load_sub_directories(self, tmp_path):
        _write_graph_yaml(tmp_path)
        _write_legal_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert gallery.get("general/graph") is not None
        assert gallery.get("legal/case_law") is not None

    def test_empty_presets_dir(self, tmp_path):
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert len(gallery._templates) == 0

    def test_nonexistent_presets_dir(self, tmp_path):
        gallery = TemplateGallery(presets_dir=str(tmp_path / "nonexistent"))
        assert len(gallery._templates) == 0

    def test_malformed_yaml_skipped(self, tmp_path):
        general = tmp_path / "general"
        general.mkdir(parents=True, exist_ok=True)
        (general / "bad.yaml").write_text("invalid: yaml: content: [")
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert len(gallery._templates) == 0

    def test_empty_yaml_skipped(self, tmp_path):
        general = tmp_path / "general"
        general.mkdir(parents=True, exist_ok=True)
        (general / "empty.yaml").write_text("")
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert len(gallery._templates) == 0


class TestTemplateGalleryRetrieval:
    def test_get_by_full_path(self, tmp_path):
        _write_graph_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        config = gallery.get("general/graph")
        assert config is not None
        assert config.name == "graph"

    def test_get_without_domain_defaults_to_general(self, tmp_path):
        _write_graph_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        config = gallery.get("graph")
        assert config is not None
        assert config.name == "graph"

    def test_get_nonexistent_returns_none(self, tmp_path):
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        assert gallery.get("nonexistent/template") is None


class TestTemplateGalleryFiltering:
    def test_list_all(self, tmp_path):
        _write_graph_yaml(tmp_path)
        _write_list_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        results = gallery.list()
        assert len(results) == 2

    def test_list_filter_by_domain(self, tmp_path):
        _write_graph_yaml(tmp_path)
        _write_legal_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        results = gallery.list(domain="general")
        assert all(t.domain == "general" for t in results)
        assert len(results) == 1

    def test_list_filter_by_type(self, tmp_path):
        _write_graph_yaml(tmp_path)
        _write_list_yaml(tmp_path)
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        results = gallery.list(type_filter="graph")
        assert all(t.type.value == "graph" for t in results)

    def test_list_no_results_for_nonexistent_domain(self, tmp_path):
        gallery = TemplateGallery(presets_dir=str(tmp_path))
        results = gallery.list(domain="nonexistent")
        assert len(results) == 0


class TestTemplateGallerySecurity:
    def test_uses_safe_load(self):
        source = inspect.getsource(TemplateGallery._load_file)
        assert "safe_load" in source
        assert "yaml.load(" not in source.replace("safe_load", "")


class TestTemplateGallerySingleton:
    def test_singleton_returns_same_instance(self):
        TemplateGallery.reset()
        g1 = TemplateGallery.get_instance()
        g2 = TemplateGallery.get_instance()
        assert g1 is g2
        TemplateGallery.reset()

    def test_reset_clears_instance(self):
        TemplateGallery.reset()
        g1 = TemplateGallery.get_instance()
        TemplateGallery.reset()
        g2 = TemplateGallery.get_instance()
        assert g1 is not g2
        TemplateGallery.reset()


class TestTemplateGalleryRealPresets:
    def test_loads_real_presets(self):
        gallery = TemplateGallery()
        templates = gallery.list()
        assert len(templates) > 0
        names = {t.name for t in templates}
        assert "graph" in names
        assert "list" in names
        assert "set" in names
        assert "hypergraph" in names

    def test_graph_template_valid(self):
        gallery = TemplateGallery()
        config = gallery.get("general/graph")
        assert config is not None
        assert config.entity_schema is not None
        assert config.relation_schema is not None
        assert config.identifiers is not None
        assert config.extraction is not None

    def test_hypergraph_template_has_participants_field(self):
        gallery = TemplateGallery()
        config = gallery.get("general/hypergraph")
        assert config is not None
        assert config.relation_schema is not None
        assert config.relation_schema.participants_field == "participants"