"""Tests for app.llm.topic_extractor."""

import json
import pytest
from unittest import mock

from app.llm import topic_extractor as te


class TestJsonHelpers:
    def test_strip_markdown_fences_json(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert te._strip_markdown_fences(raw) == '{"a": 1}'

    def test_strip_markdown_fences_plain(self):
        raw = "```\n{\"a\": 1}\n```"
        assert te._strip_markdown_fences(raw) == '{"a": 1}'

    def test_safe_json_loads_valid(self):
        assert te._safe_json_loads('{"topics": []}') == {"topics": []}

    def test_safe_json_loads_wrapped(self):
        assert te._safe_json_loads('Some intro ```json\n{"topics": []}\n```') == {"topics": []}

    def test_safe_json_loads_extract_objects(self):
        raw = '{"topics": [{"name": "ai"}, {"name": "ml"}], "entity_topic_links": []}'
        parsed = te._safe_json_loads(raw)
        assert parsed["topics"][0]["name"] == "ai"

    def test_extract_json_objects(self):
        raw = '{"topics": [{"name": "ai"}, {"name": "ml"}]}'
        objs = te._extract_json_objects(raw, "topics")
        assert [o["name"] for o in objs] == ["ai", "ml"]


class TestExtractTopicsFromChunk:
    @pytest.mark.asyncio
    async def test_empty_text_returns_empty(self):
        result = await te.extract_topics_from_chunk("")
        assert result == {"topics": [], "entity_topic_links": []}

    @pytest.mark.asyncio
    async def test_extract_topics_parsing(self):
        fake_response = {
            "topics": [
                {"name": "Machine Learning", "confidence": 0.9, "keywords": ["neural networks"]},
                {"name": "NLP", "confidence": 0.8, "keywords": ["language"]},
            ],
            "entity_topic_links": [
                {"entity_name": "Transformer", "topic": "NLP", "role": "concept"},
            ],
        }
        with mock.patch.object(te, "_call_ollama", new=mock.AsyncMock(return_value=fake_response)):
            result = await te.extract_topics_from_chunk("Text about machine learning and NLP.")

        topics = result["topics"]
        assert len(topics) == 2
        assert topics[0]["name"] == "machine learning"
        assert topics[1]["confidence"] == 0.8

        links = result["entity_topic_links"]
        assert len(links) == 1
        assert links[0]["entity_name"] == "Transformer"
        assert links[0]["role"] == "concept"

    @pytest.mark.asyncio
    async def test_extract_topics_handles_invalid_response(self):
        with mock.patch.object(te, "_call_ollama", new=mock.AsyncMock(side_effect=te.TopicExtractionError("boom"))):
            result = await te.extract_topics_from_chunk("Some text")
        assert result == {"topics": [], "entity_topic_links": []}


class TestCanonicalizeTopics:
    @pytest.mark.asyncio
    async def test_canonicalize(self):
        fake_response = {
            "artificial intelligence": ["ai", "a.i."],
            "machine learning": ["ml"],
        }
        with mock.patch.object(te, "_call_ollama", new=mock.AsyncMock(return_value=fake_response)):
            mapping = await te.canonicalize_topics(["ai", "ml", "a.i.", "deep learning"])

        assert mapping["artificial intelligence"] == ["ai", "a.i."]
        assert mapping["machine learning"] == ["ml"]
        assert "deep learning" not in mapping

    @pytest.mark.asyncio
    async def test_canonicalize_empty(self):
        result = await te.canonicalize_topics([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_canonicalize_single_name(self):
        result = await te.canonicalize_topics(["only one"])
        assert result == {}


class TestInferTopicRelationships:
    @pytest.mark.asyncio
    async def test_infer(self):
        fake_response = [
            {"subject": "deep learning", "predicate": "subtopic_of", "object": "machine learning", "confidence": 0.9},
            {"subject": "machine learning", "predicate": "related_to", "object": "machine learning", "confidence": 0.7},
        ]
        with mock.patch.object(te, "_call_ollama", new=mock.AsyncMock(return_value=fake_response)):
            triples = await te.infer_topic_relationships(
                ["machine learning", "deep learning"],
                [("deep learning", "machine learning", 5)],
            )

        assert len(triples) == 1
        assert triples[0]["subject"] == "deep learning"
        assert triples[0]["predicate"] == "subtopic_of"
        assert triples[0]["object"] == "machine learning"

    @pytest.mark.asyncio
    async def test_infer_too_few_topics(self):
        result = await te.infer_topic_relationships(["only"], [])
        assert result == []

    @pytest.mark.asyncio
    async def test_infer_invalid_predicate_filtered(self):
        fake_response = [
            {"subject": "a", "predicate": "unknown", "object": "b", "confidence": 0.9},
        ]
        with mock.patch.object(te, "_call_ollama", new=mock.AsyncMock(return_value=fake_response)):
            triples = await te.infer_topic_relationships(["a", "b"], [("a", "b", 3)])
        assert triples == []

    @pytest.mark.asyncio
    async def test_infer_self_reference_filtered(self):
        fake_response = [
            {"subject": "a", "predicate": "related_to", "object": "a", "confidence": 0.9},
        ]
        with mock.patch.object(te, "_call_ollama", new=mock.AsyncMock(return_value=fake_response)):
            triples = await te.infer_topic_relationships(["a"], [])
        assert triples == []
