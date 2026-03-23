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

def _entity_labels(extraction_json: dict) -> set[str]:
    """Return a normalised set of entity labels from an extraction JSON dict."""
    labels: set[str] = set()
    for ent in extraction_json.get("entities", []):
        label = ent.get("label") or ent.get("name") or ent.get("text") or ""
        if label:
            labels.add(label.strip().lower())
    return labels


def _prf(predicted: set[str], ground_truth: set[str]) -> dict:
    """Compute precision, recall, and F1 between two label sets."""
    if not predicted and not ground_truth:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    tp = len(predicted & ground_truth)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(ground_truth) if ground_truth else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


async def _call_model(client, model: str, chunk_text: str, system_msg: str) -> dict:
    """Call an OpenAI chat model and parse the JSON extraction result."""
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": f"Extract entities and relationships from this text:\n{chunk_text}"},
            ],
            temperature=0,
            max_tokens=512,
        )
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)
    except Exception:
        return {}


async def run_ab_evaluation(
    collection_id: str,
    fine_tuned_model: str,
    base_model: str = "gpt-4o-mini-2024-07-18",
    n_samples: int = 20,
) -> dict:
    """Run A/B evaluation comparing fine-tuned vs base model extraction quality.

    Uses ground-truth entity labels from the stored knowledge graph nodes as the
    reference, then computes precision/recall/F1 for both models on the same chunk
    sample. Returns per-sample results and aggregate averages.
    """
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured.")

    import openai  # type: ignore
    import random

    from app.db.lancedb_client import get_lancedb, list_graph_nodes

    db = await get_lancedb()
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    # Load chunk sample
    try:
        ctbl = db.open_table(f"{collection_id}_chunks")
        all_chunks = ctbl.search().limit(10_000).to_list()
    except Exception as exc:
        raise ValueError(f"Could not read chunks table: {exc}")

    if not all_chunks:
        raise ValueError("No chunks found in this collection.")

    sample = random.sample(all_chunks, min(n_samples, len(all_chunks)))

    # Build ground-truth: chunk_id → set of entity labels from graph nodes
    all_nodes = await list_graph_nodes(collection_id)
    chunk_to_gt: dict[str, set[str]] = {}
    for node in all_nodes:
        label = (node.get("label") or "").strip().lower()
        if not label:
            continue
        for cid in node.get("source_chunk_ids") or []:
            chunk_to_gt.setdefault(cid, set()).add(label)

    # Build system prompt (same as training)
    ontology_context = ""
    try:
        otbl = db.open_table("ontologies")
        onts = otbl.search().where(f"collection_id = '{collection_id}'", prefilter=True).limit(1).to_list()
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

    # Evaluate both models concurrently per chunk
    per_sample: list[dict] = []
    for chunk in sample:
        cid = chunk.get("id", "")
        text = chunk.get("text", "")
        if not text:
            continue

        gt_labels = chunk_to_gt.get(cid, set())

        ft_result, base_result = await asyncio.gather(
            _call_model(client, fine_tuned_model, text, system_msg),
            _call_model(client, base_model, text, system_msg),
        )

        ft_labels = _entity_labels(ft_result)
        base_labels = _entity_labels(base_result)

        per_sample.append({
            "chunk_id": cid,
            "ground_truth_count": len(gt_labels),
            "fine_tuned": _prf(ft_labels, gt_labels),
            "base": _prf(base_labels, gt_labels),
        })

    if not per_sample:
        raise ValueError("No valid chunks with text found in sample.")

    # Compute per-metric averages (only over chunks that have ground-truth)
    def _mean(metric: str, model_key: str) -> float:
        vals = [s[model_key][metric] for s in per_sample if s["ground_truth_count"] > 0]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "collection_id": collection_id,
        "fine_tuned_model": fine_tuned_model,
        "base_model": base_model,
        "n_samples": len(per_sample),
        "aggregate": {
            "fine_tuned": {
                "precision": _mean("precision", "fine_tuned"),
                "recall": _mean("recall", "fine_tuned"),
                "f1": _mean("f1", "fine_tuned"),
            },
            "base": {
                "precision": _mean("precision", "base"),
                "recall": _mean("recall", "base"),
                "f1": _mean("f1", "base"),
            },
        },
        "per_sample": per_sample,
    }


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
