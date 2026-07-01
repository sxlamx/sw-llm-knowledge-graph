"""Ontology router — manage and generate collection ontologies."""

import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.middleware import get_current_user
from app.db.lancedb_client import get_collection, get_ontology, upsert_ontology, list_ontology_versions
from app.models.schemas import (
    OntologyResponse, UpdateOntologyRequest, GenerateOntologyRequest,
    OntologyGenerateResponse, EntityTypeDef, RelationTypeDef,
)
router = APIRouter()
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


@router.post("/generate", response_model=OntologyGenerateResponse)
async def generate_ontology_endpoint(
    body: GenerateOntologyRequest,
    current_user: dict = Depends(get_current_user),
):
    await _require_access(body.collection_id, current_user)

    from app.db.lancedb_client import get_lancedb
    from app.llm.ollama_client import call_ollama_cloud, OllamaCloudError

    # Gather sample texts from the collection's chunks
    db = await get_lancedb()
    sample_texts = []
    try:
        tbl = db.open_table(f"{body.collection_id}_chunks")
        rows = tbl.search().limit(20).to_list()
        sample_texts = [r.get("text", "") for r in rows if r.get("text")]
    except Exception:
        pass

    if not sample_texts:
        proposal = OntologyResponse(
            collection_id=body.collection_id,
            entity_types=_DEFAULT_ENTITY_TYPES,
            relationship_types=_DEFAULT_RELATION_TYPES,
        )
        return OntologyGenerateResponse(proposal=proposal)

    sample = "\n---\n".join(sample_texts[:10])
    user_prompt = f"""Analyse these document excerpts and suggest a domain-specific knowledge graph ontology.

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
        response = await call_ollama_cloud(
            system_prompt="You are an ontology engineer.",
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=1500,
        )
        content = response["content"]
        parsed = json.loads(content)
        if "entity_types" in parsed:
            generated_et.update(parsed["entity_types"])
        if "relationship_types" in parsed:
            generated_rt.update(parsed["relationship_types"])
    except (OllamaCloudError, json.JSONDecodeError) as e:
        logger.warning(f"Ontology generation LLM call failed: {e}")

    et_defs = {}
    for k, v in generated_et.items():
        et_defs[k] = EntityTypeDef(**v) if isinstance(v, dict) else EntityTypeDef()
    rt_defs = {}
    for k, v in generated_rt.items():
        rt_defs[k] = RelationTypeDef(**v) if isinstance(v, dict) else RelationTypeDef(domain=[], range=[])

    proposal = OntologyResponse(
        collection_id=body.collection_id,
        entity_types=et_defs,
        relationship_types=rt_defs,
    )
    return OntologyGenerateResponse(proposal=proposal)


@router.get("/versions")
async def list_ontology_versions_endpoint(
    collection_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)
    rows = await list_ontology_versions(collection_id, limit=limit, offset=offset)
    versions = []
    for row in rows:
        versions.append({
            "version": row.get("version", 1),
            "updated_at": row.get("updated_at"),
        })
    return {"collection_id": collection_id, "versions": versions, "total": len(rows)}


@router.post("/validate")
async def validate_ontology_endpoint(
    collection_id: str = Query(...),
    entities: list[str] = Query(default=[]),
    relationships: list[str] = Query(default=[]),
    current_user: dict = Depends(get_current_user),
):
    await _require_access(collection_id, current_user)
    row = await get_ontology(collection_id)
    if not row:
        return {"valid": True, "warnings": [], "errors": ["No active ontology found"]}

    et_data = {}
    rt_data = {}
    try:
        et_raw = row.get("entity_types")
        et_data = json.loads(et_raw) if isinstance(et_raw, str) else (et_raw or {})
    except Exception:
        pass
    try:
        rt_raw = row.get("relationship_types")
        rt_data = json.loads(rt_raw) if isinstance(rt_raw, str) else (rt_raw or {})
    except Exception:
        pass

    known_entities = set(et_data.keys())
    known_relations = set(rt_data.keys())

    warnings = []
    errors = []

    for entity in entities:
        if entity not in known_entities:
            warnings.append(f"Unknown entity type: {entity}")

    for rel in relationships:
        if rel not in known_relations:
            errors.append(f"Unknown relationship type: {rel}")

    return {"valid": len(errors) == 0, "warnings": warnings, "errors": errors}
