"""LLM fine-tuning export service.

Workflow
--------
1. Fetch accepted ``user_feedback`` records (action = "accept" | "edit") from LanceDB.
2. For each feedback record, reconstruct the original chunk + ontology context and
   the corrected extraction as a training example.
3. Write the dataset as a JSONL file in OpenAI fine-tuning format
   (``{"messages": [{"role": "system", ...}, {"role": "user", ...}, {"role": "assistant", ...}]}``).
4. Upload the file to OpenAI and start a fine-tuning job.
5. Return the job ID for polling.

Training example format
-----------------------
Each example re-frames the extraction task as a chat turn:

  system:    "You are a knowledge graph extraction system. ..."
  user:      "Extract entities and relationships from this text:\n<chunk_text>"
  assistant: "<corrected JSON extraction>"

The ``corrected JSON extraction`` comes from the ``after`` field of the feedback record
(which stores the user-edited entity / edge JSON).
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a knowledge graph extraction system. Extract entities and relationships "
    "from the provided text using the ontology context. Return ONLY valid JSON matching "
    "the schema: {\"entities\": [...], \"relationships\": [...]}."
)

# ---------------------------------------------------------------------------
# Dataset assembly
# ---------------------------------------------------------------------------

async def build_training_dataset(
    collection_id: str,
    min_confidence: float = 0.0,
    max_examples: int = 5_000,
) -> list[dict]:
    """Return a list of OpenAI chat fine-tuning examples from user feedback."""
    from app.db.lancedb_client import get_lancedb

    db = await get_lancedb()

    # Fetch feedback records
    try:
        tbl = await db.open_table("user_feedback")
        rows = await tbl.search().where(
            f"collection_id = '{collection_id}' AND (action = 'accept' OR action = 'edit')"
        ).limit(max_examples).to_list()
    except Exception as exc:
        logger.warning(f"Could not read user_feedback table: {exc}")
        return []

    if not rows:
        return []

    # Build a chunk lookup for context
    chunk_lookup: dict[str, str] = {}
    try:
        ctbl = await db.open_table(f"{collection_id}_chunks")
        chunks = await ctbl.search().limit(10_000).to_list()
        chunk_lookup = {c["id"]: c.get("text", "") for c in chunks}
    except Exception:
        pass

    # Fetch collection ontology for the system prompt context
    ontology_context = ""
    try:
        otbl = await db.open_table("ontologies")
        onts = await otbl.search().where(f"collection_id = '{collection_id}'").limit(1).to_list()
        if onts:
            ont = onts[0]
            entity_types = json.loads(ont.get("entity_types", "{}"))
            rel_types = json.loads(ont.get("relationship_types", "{}"))
            ontology_context = (
                f"Entity types: {', '.join(entity_types.keys())}. "
                f"Relationship types: {', '.join(rel_types.keys())}."
            )
    except Exception:
        pass

    system_msg = _SYSTEM_PROMPT
    if ontology_context:
        system_msg += f"\n\n{ontology_context}"

    examples: list[dict] = []
    for row in rows:
        target_id = row.get("target_id", "")
        after = row.get("after")
        if not after:
            continue

        # after is stored as JSON string
        try:
            corrected = json.loads(after) if isinstance(after, str) else after
        except Exception:
            continue

        chunk_text = chunk_lookup.get(target_id, "")
        if not chunk_text:
            # Fall back to the before/after diff as context
            chunk_text = row.get("before", "")

        if not chunk_text:
            continue

        examples.append({
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": f"Extract entities and relationships from this text:\n{chunk_text}"},
                {"role": "assistant", "content": json.dumps(corrected, ensure_ascii=False)},
            ]
        })

    logger.info(f"Built {len(examples)} fine-tuning examples for collection {collection_id}")
    return examples


def write_jsonl(examples: list[dict], path: str) -> int:
    """Write examples to a JSONL file. Returns number of lines written."""
    count = 0
    with open(path, "w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
            count += 1
    return count


# ---------------------------------------------------------------------------
# OpenAI fine-tuning API
# ---------------------------------------------------------------------------

async def upload_and_start_finetune(
    jsonl_path: str,
    base_model: str = "gpt-4o-mini-2024-07-18",
    suffix: str = "kg-extraction",
    n_epochs: int = 3,
) -> dict:
    """Upload a JSONL file to OpenAI and start a fine-tuning job."""
    if not settings.openai_api_key:
        raise ValueError(
            "OPENAI_API_KEY is not configured. Set it via the openai_api_key setting."
        )

    import openai  # type: ignore

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    # Upload training file
    with open(jsonl_path, "rb") as fh:
        file_resp = await client.files.create(file=fh, purpose="fine-tune")

    file_id = file_resp.id
    logger.info(f"Uploaded fine-tuning file: {file_id}")

    # Start fine-tuning job
    job = await client.fine_tuning.jobs.create(
        training_file=file_id,
        model=base_model,
        suffix=suffix,
        hyperparameters={"n_epochs": n_epochs},
    )

    logger.info(f"Fine-tuning job created: {job.id} (status={job.status})")
    return {
        "job_id": job.id,
        "file_id": file_id,
        "status": job.status,
        "model": base_model,
        "suffix": suffix,
    }


async def get_finetune_job_status(job_id: str) -> dict:
    """Retrieve the current status of an OpenAI fine-tuning job."""
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    import openai  # type: ignore

    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    job = await client.fine_tuning.jobs.retrieve(job_id)

    return {
        "job_id": job.id,
        "status": job.status,
        "fine_tuned_model": getattr(job, "fine_tuned_model", None),
        "trained_tokens": getattr(job, "trained_tokens", None),
        "error": getattr(job, "error", None),
    }


# ---------------------------------------------------------------------------
# All-in-one export + upload
# ---------------------------------------------------------------------------

async def export_and_finetune(
    collection_id: str,
    base_model: str = "gpt-4o-mini-2024-07-18",
    suffix: str = "kg-extraction",
    n_epochs: int = 3,
    max_examples: int = 5_000,
) -> dict:
    """Build dataset, write JSONL, upload to OpenAI, start fine-tuning job."""
    examples = await build_training_dataset(collection_id, max_examples=max_examples)

    if not examples:
        return {
            "status": "skipped",
            "reason": "No accepted feedback records found for this collection.",
            "example_count": 0,
        }

    with tempfile.NamedTemporaryFile(
        suffix=".jsonl", delete=False, mode="w", encoding="utf-8"
    ) as tmp:
        tmp_path = tmp.name

    try:
        count = write_jsonl(examples, tmp_path)
        result = await upload_and_start_finetune(
            tmp_path, base_model=base_model, suffix=suffix, n_epochs=n_epochs
        )
        result["example_count"] = count
        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)
