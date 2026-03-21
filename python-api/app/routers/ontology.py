"""Ontology router — manage and generate collection ontologies."""

import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection, get_ontology, upsert_ontology
from app.models.schemas import (
    OntologyResponse, UpdateOntologyRequest, GenerateOntologyRequest,
    EntityTypeDef, RelationTypeDef,
)
from app.config import get_settings

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

_DEFAULT_ENTITY_TYPES = {
    "Person": EntityTypeDef(description="A human individual", examples=["Alice", "Bob"]),
    "Organization": EntityTypeDef(description="A company, institution, or group", examples=["OpenAI", "MIT"]),
    "Location": EntityTypeDef(description="A geographical place", examples=["New York", "Paris"]),
    "Concept": EntityTypeDef(description="An abstract idea or theory", examples=["Machine Learning", "Democracy"]),
    "Event": EntityTypeDef(description="A notable occurrence", examples=["World War II", "IPO"]),
}

_DEFAULT_RELATION_TYPES = {
    "WORKS_AT": RelationTypeDef(domain=["Person"], range=["Organization"], description="Employment"),
    "FOUNDED": RelationTypeDef(domain=["Person"], range=["Organization"], description="Founded by"),
    "LOCATED_IN": RelationTypeDef(domain=["Organization", "Person", "Event"], range=["Location"], description="Located in"),
    "PARTICIPATED_IN": RelationTypeDef(domain=["Person", "Organization"], range=["Event"], description="Participated in"),
    "RELATED_TO": RelationTypeDef(domain=["Concept", "Person", "Organization"], range=["Concept", "Person", "Organization"], description="General relation"),
    "PART_OF": RelationTypeDef(domain=["Organization", "Concept"], range=["Organization", "Concept"], description="Is part of"),
    "MENTIONS": RelationTypeDef(domain=["Document"], range=["Person", "Organization", "Location", "Concept", "Event"], description="Document mentions entity"),
}


async def _require_access(collection_id: str, current_user: dict) -> dict:
    collection = await get_collection(collection_id)
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    if collection.get("user_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return collection


def _row_to_ontology(row: dict) -> OntologyResponse:
    et_raw = row.get("entity_types")
    rt_raw = row.get("relationship_types")
    entity_types = {}
    relation_types = {}
    try:
        et_data = json.loads(et_raw) if isinstance(et_raw, str) else (et_raw or {})
        for k, v in et_data.items():
            entity_types[k] = EntityTypeDef(**v) if isinstance(v, dict) else EntityTypeDef()
    except Exception:
        entity_types = {}
    try:
        rt_data = json.loads(rt_raw) if isinstance(rt_raw, str) else (rt_raw or {})
        for k, v in rt_data.items():
            relation_types[k] = RelationTypeDef(**v) if isinstance(v, dict) else RelationTypeDef(domain=[], range=[])
    except Exception:
        relation_types = {}
    return OntologyResponse(
        collection_id=row.get("collection_id", ""),
        version=row.get("version", 1),
        entity_types=entity_types,
        relationship_types=relation_types,
        updated_at=row.get("updated_at"),
    )


@router.get("", response_model=OntologyResponse)
async def get_ontology_endpoint(
    collection_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)
    row = await get_ontology(collection_id)
    if not row:
        # Return default ontology
        return OntologyResponse(
            collection_id=collection_id,
            entity_types=_DEFAULT_ENTITY_TYPES,
            relationship_types=_DEFAULT_RELATION_TYPES,
        )
    return _row_to_ontology(row)


@router.put("", response_model=OntologyResponse)
async def update_ontology_endpoint(
    collection_id: str = Query(...),
    body: UpdateOntologyRequest = ...,
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)

    existing = await get_ontology(collection_id)
    current_version = existing.get("version", 1) if existing else 1

    et = {}
    rt = {}
    if existing:
        try:
            et = json.loads(existing.get("entity_types") or "{}")
        except Exception:
            pass
        try:
            rt = json.loads(existing.get("relationship_types") or "{}")
        except Exception:
            pass

    if body.entity_types is not None:
        et.update({k: v.model_dump() for k, v in body.entity_types.items()})
    if body.relationship_types is not None:
        rt.update({k: v.model_dump() for k, v in body.relationship_types.items()})

    row = {
        "collection_id": collection_id,
        "version": current_version + 1,
        "entity_types": json.dumps(et),
        "relationship_types": json.dumps(rt),
    }
    await upsert_ontology(row)
    return _row_to_ontology(row)


@router.post("/generate", response_model=OntologyResponse)
async def generate_ontology_endpoint(
    body: GenerateOntologyRequest,
    current_user: dict = Depends(get_current_user),
):
    await _require_access(body.collection_id, current_user)

    import httpx
    from app.db.lancedb_client import get_lancedb

    # Gather sample texts from the collection's chunks
    db = await get_lancedb()
    sample_texts = []
    try:
        tbl = db.open_table(f"{body.collection_id}_chunks")
        rows = tbl.query().limit(20).to_list()
        sample_texts = [r.get("text", "") for r in rows if r.get("text")]
    except Exception:
        pass

    if not sample_texts:
        # Nothing to generate from — return defaults
        default_row = {
            "collection_id": body.collection_id,
            "version": 1,
            "entity_types": json.dumps({k: v.model_dump() for k, v in _DEFAULT_ENTITY_TYPES.items()}),
            "relationship_types": json.dumps({k: v.model_dump() for k, v in _DEFAULT_RELATION_TYPES.items()}),
        }
        await upsert_ontology(default_row)
        return _row_to_ontology(default_row)

    sample = "\n---\n".join(sample_texts[:10])
    prompt = f"""You are an ontology engineer. Analyse these document excerpts and suggest a domain-specific knowledge graph ontology.

EXCERPTS:
{sample[:4000]}

Return ONLY valid JSON matching this schema exactly:
{{
  "entity_types": {{
    "TypeName": {{"description": "...", "examples": ["...", "..."]}}
  }},
  "relationship_types": {{
    "RELATION_NAME": {{"domain": ["TypeA"], "range": ["TypeB"], "description": "..."}}
  }}
}}

Include 5-10 entity types and 5-10 relationship types relevant to the domain above."""

    generated_et = {k: v.model_dump() for k, v in _DEFAULT_ENTITY_TYPES.items()}
    generated_rt = {k: v.model_dump() for k, v in _DEFAULT_RELATION_TYPES.items()}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            resp = await client.post(
                f"{settings.ollama_cloud_base_url}/chat/completions",
                json={
                    "model": settings.ollama_cloud_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 1500,
                },
                headers={"Authorization": f"Bearer {settings.ollama_cloud_api_key}"},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            parsed = json.loads(content)
            if "entity_types" in parsed:
                generated_et.update(parsed["entity_types"])
            if "relationship_types" in parsed:
                generated_rt.update(parsed["relationship_types"])
    except Exception as e:
        logger.warning(f"Ontology generation LLM call failed: {e}")

    existing = await get_ontology(body.collection_id)
    version = (existing.get("version", 0) if existing else 0) + 1

    row = {
        "collection_id": body.collection_id,
        "version": version,
        "entity_types": json.dumps(generated_et),
        "relationship_types": json.dumps(generated_rt),
    }
    await upsert_ontology(row)
    return _row_to_ontology(row)
