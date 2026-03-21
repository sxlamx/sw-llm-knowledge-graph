"""LLM cost tracker — per-job token counting with a max_cost_usd circuit breaker."""

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Approximate cost per 1 000 tokens (USD) for the models we use.
# Update these if pricing changes.
_COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    # OpenAI models
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "text-embedding-3-large": {"input": 0.00013, "output": 0.0},
    # Ollama / local models — treated as zero-cost
    "llama3.2": {"input": 0.0, "output": 0.0},
    "qwen3-embedding": {"input": 0.0, "output": 0.0},
}

_DEFAULT_COST = {"input": 0.005, "output": 0.015}  # conservative fallback


def _cost_for_tokens(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = _COST_PER_1K_TOKENS.get(model, _DEFAULT_COST)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1000.0


class BudgetExceededError(Exception):
    """Raised when the job's max_cost_usd cap would be exceeded."""

    def __init__(self, job_id: str, current_usd: float, max_usd: float):
        self.job_id = job_id
        self.current_usd = current_usd
        self.max_usd = max_usd
        super().__init__(
            f"Job {job_id}: LLM cost ${current_usd:.4f} would exceed cap ${max_usd:.4f}"
        )


@dataclass
class JobCostTracker:
    job_id: str
    max_cost_usd: float
    _total_usd: float = field(default=0.0, init=False)
    _total_input_tokens: int = field(default=0, init=False)
    _total_output_tokens: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def total_usd(self) -> float:
        return self._total_usd

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    async def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """
        Record token usage for a single LLM call.
        Raises BudgetExceededError if the running total would surpass max_cost_usd.
        """
        cost = _cost_for_tokens(model, input_tokens, output_tokens)
        async with self._lock:
            projected = self._total_usd + cost
            if self.max_cost_usd > 0 and projected > self.max_cost_usd:
                raise BudgetExceededError(self.job_id, projected, self.max_cost_usd)
            self._total_usd = projected
            self._total_input_tokens += input_tokens
            self._total_output_tokens += output_tokens
            logger.debug(
                "Job %s: +$%.4f (in=%d out=%d) → total=$%.4f / cap=$%.4f",
                self.job_id, cost, input_tokens, output_tokens,
                self._total_usd, self.max_cost_usd,
            )

    def summary(self) -> dict:
        return {
            "job_id": self.job_id,
            "total_usd": round(self._total_usd, 6),
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "max_cost_usd": self.max_cost_usd,
        }


# ---------------------------------------------------------------------------
# Module-level registry so ingest workers can look up their tracker
# ---------------------------------------------------------------------------

_trackers: dict[str, JobCostTracker] = {}


def create_tracker(job_id: str, max_cost_usd: float) -> JobCostTracker:
    tracker = JobCostTracker(job_id=job_id, max_cost_usd=max_cost_usd)
    _trackers[job_id] = tracker
    return tracker


def get_tracker(job_id: str) -> JobCostTracker | None:
    return _trackers.get(job_id)


def remove_tracker(job_id: str) -> None:
    _trackers.pop(job_id, None)
