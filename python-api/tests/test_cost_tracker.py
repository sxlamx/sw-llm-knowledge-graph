"""Tests for the LLM cost tracker."""

import asyncio
import pytest
from app.services.cost_tracker import (
    JobCostTracker, BudgetExceededError,
    create_tracker, get_tracker, remove_tracker,
)


class TestJobCostTracker:
    @pytest.fixture
    def tracker(self):
        return JobCostTracker(job_id="job-1", max_cost_usd=1.0)

    @pytest.mark.asyncio
    async def test_records_cost(self, tracker):
        await tracker.record("gpt-4o-mini", input_tokens=1000, output_tokens=200)
        assert tracker.total_usd > 0
        assert tracker.total_input_tokens == 1000
        assert tracker.total_output_tokens == 200

    @pytest.mark.asyncio
    async def test_raises_when_budget_exceeded(self, tracker):
        with pytest.raises(BudgetExceededError) as exc_info:
            # gpt-4o: $0.005/1k input; 201k tokens = $1.005 > $1.00 cap
            await tracker.record("gpt-4o", input_tokens=201_000, output_tokens=0)
        assert exc_info.value.max_usd == 1.0

    @pytest.mark.asyncio
    async def test_no_cap_when_max_cost_zero(self):
        tracker = JobCostTracker(job_id="j", max_cost_usd=0.0)
        # Should never raise regardless of usage
        await tracker.record("gpt-4o", input_tokens=10_000_000, output_tokens=10_000_000)

    @pytest.mark.asyncio
    async def test_local_model_zero_cost(self, tracker):
        before = tracker.total_usd
        await tracker.record("llama3.2", input_tokens=50_000, output_tokens=50_000)
        assert tracker.total_usd == before  # zero cost model

    def test_summary_shape(self, tracker):
        s = tracker.summary()
        assert set(s.keys()) == {"job_id", "total_usd", "total_input_tokens", "total_output_tokens", "max_cost_usd"}

    @pytest.mark.asyncio
    async def test_cumulative_cost_accurate(self):
        tracker = JobCostTracker(job_id="j", max_cost_usd=10.0)
        # gpt-4o-mini: $0.00015/1k input, $0.0006/1k output
        await tracker.record("gpt-4o-mini", input_tokens=1000, output_tokens=0)
        assert tracker.total_usd == pytest.approx(0.00015, rel=1e-3)


class TestTrackerRegistry:
    def setup_method(self):
        remove_tracker("reg-test")

    def test_create_and_get(self):
        t = create_tracker("reg-test", max_cost_usd=5.0)
        assert get_tracker("reg-test") is t

    def test_remove(self):
        create_tracker("reg-test", max_cost_usd=1.0)
        remove_tracker("reg-test")
        assert get_tracker("reg-test") is None

    def test_get_nonexistent_returns_none(self):
        assert get_tracker("nonexistent-job-xyz") is None
