"""Tests for app.pipeline.topic_worker."""

import json
from unittest import mock

import pytest

from app.pipeline import topic_worker as tw


def _enable_topic_extraction(settings):
    settings.enable_topic_extraction = True
    settings.topic_extraction_model = "test-model"
    settings.topic_batch_size = 200
    settings.topic_extraction_concurrency = 5


class TestRunTopicExtractionPass:
    @pytest.mark.asyncio
    async def test_disabled_returns_skipped(self, mock_settings):
        mock_settings.enable_topic_extraction = False

        result = await tw._run_topic_extraction_pass("col-1")
        assert result["skipped"] is True

    @pytest.mark.asyncio
    async def test_no_chunks_returns_skipped(self, mock_settings):
        _enable_topic_extraction(mock_settings)
        with mock.patch("app.pipeline.topic_worker.get_outdated_topic_chunks", return_value=[]):
            result = await tw._run_topic_extraction_pass("col-1")

        assert result["skipped"] is True
        assert result["chunks_updated"] == 0

    @pytest.mark.asyncio
    async def test_end_to_end_aggregates_and_persists(self, mock_settings):
        _enable_topic_extraction(mock_settings)
        chunks = [
            {
                "id": "c1",
                "text": "AI and ML are great.",
                "contextual_text": "AI and ML are great.",
                "embedding": [1.0, 0.0],
            },
            {
                "id": "c2",
                "text": "Machine learning and NLP.",
                "contextual_text": "Machine learning and NLP.",
                "embedding": [1.0, 1.0],
            },
        ]

        async def fake_extract(text):
            if "c1" in text or "AI" in text:
                return {
                    "topics": [
                        {"name": "ai", "confidence": 0.9, "keywords": ["artificial intelligence"]},
                        {"name": "ml", "confidence": 0.8, "keywords": ["machine learning"]},
                    ],
                    "entity_topic_links": [],
                }
            return {
                "topics": [
                    {"name": "machine learning", "confidence": 0.9, "keywords": ["ml"]},
                    {"name": "nlp", "confidence": 0.7, "keywords": ["language"]},
                ],
                "entity_topic_links": [],
            }

        with mock.patch("app.pipeline.topic_worker.get_outdated_topic_chunks", return_value=chunks), \
             mock.patch("app.pipeline.topic_worker.extract_topics_from_chunk", new=mock.AsyncMock(side_effect=fake_extract)), \
             mock.patch("app.pipeline.topic_worker.canonicalize_topics", new=mock.AsyncMock(return_value={"machine learning": ["ml"]})), \
             mock.patch("app.pipeline.topic_worker.infer_topic_relationships", new=mock.AsyncMock(return_value=[])), \
             mock.patch("app.pipeline.topic_worker.bulk_update_chunk_topics", side_effect=lambda _col, batch: len(batch)) as mock_bulk, \
             mock.patch("app.pipeline.topic_worker.upsert_topics") as mock_upsert, \
             mock.patch("app.pipeline.topic_worker.list_graph_nodes", return_value=[]):

            result = await tw._run_topic_extraction_pass("col-1", job_id="job-1")

        assert result["chunks_updated"] == 2
        assert result["topics_added"] == 3  # ai, machine learning, nlp
        assert result["errors"] == 0

        # Bulk updates should contain canonical topics (batched into one call)
        calls = mock_bulk.call_args_list
        assert len(calls) == 1
        all_topics_in_updates = set()
        for call in calls:
            updates = call.kwargs["updates"] if "updates" in call.kwargs else call.args[1]
            for u in updates:
                all_topics_in_updates.update(json.loads(u["topics"]))
        assert all_topics_in_updates == {"ai", "machine learning", "nlp"}

        # Topics table should be written
        mock_upsert.assert_called_once()
        topic_records = mock_upsert.call_args.args[1]
        names = {r["name"] for r in topic_records}
        assert names == {"ai", "machine learning", "nlp"}

        # Machine learning embedding should be average of c1 and c2 embeddings
        ml_record = next(r for r in topic_records if r["name"] == "machine learning")
        assert ml_record["frequency"] == 2
        assert ml_record["chunk_count"] == 2

    @pytest.mark.asyncio
    async def test_relationship_inference_skipped_for_few_topics(self, mock_settings):
        _enable_topic_extraction(mock_settings)
        chunks = [
            {
                "id": "c1",
                "text": "AI.",
                "contextual_text": "AI.",
                "embedding": [1.0, 0.0],
            },
        ]

        with mock.patch("app.pipeline.topic_worker.get_outdated_topic_chunks", return_value=chunks), \
             mock.patch("app.pipeline.topic_worker.extract_topics_from_chunk", new=mock.AsyncMock(return_value={
                 "topics": [{"name": "ai", "confidence": 0.9, "keywords": []}],
                 "entity_topic_links": [],
             })), \
             mock.patch("app.pipeline.topic_worker.canonicalize_topics", new=mock.AsyncMock(return_value={})), \
             mock.patch("app.pipeline.topic_worker.infer_topic_relationships") as mock_infer, \
             mock.patch("app.pipeline.topic_worker.bulk_update_chunk_topics", side_effect=lambda _col, batch: len(batch)), \
             mock.patch("app.pipeline.topic_worker.upsert_topics"), \
             mock.patch("app.pipeline.topic_worker.list_graph_nodes", return_value=[]):

            result = await tw._run_topic_extraction_pass("col-1")

        assert result["chunks_updated"] == 1
        assert result["topic_relationships"] == 0
        mock_infer.assert_not_called()


class TestPropagateTopicsToNodes:
    @pytest.mark.asyncio
    async def test_propagation_matches_entities(self):
        nodes = [
            {
                "id": "n1",
                "label": "OpenAI",
                "entity_type": "Organization",
                "aliases": [],
                "topics": [],
            }
        ]
        topic_stats = {
            "ai": {
                "entity_links": {
                    "organization": {"OpenAI"},
                },
            },
        }

        with mock.patch("app.pipeline.topic_worker.list_graph_nodes", return_value=nodes), \
             mock.patch("app.pipeline.topic_worker.upsert_graph_nodes") as mock_upsert, \
             mock.patch("app.pipeline.topic_worker.get_index_manager", return_value=None):

            await tw._propagate_topics_to_nodes("col-1", topic_stats)

        mock_upsert.assert_called_once()
        updated = mock_upsert.call_args.args[1]
        assert updated[0]["topics"] == ["ai"]
