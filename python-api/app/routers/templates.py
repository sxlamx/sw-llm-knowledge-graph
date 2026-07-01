"""Template management router — list, get, validate extraction templates."""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import ValidationError

from app.auth.middleware import get_current_user, require_admin
from app.models.template import TemplateConfig, TemplateSummary
from app.services.extraction_registry import REGISTRY
from app.services.template_gallery import TemplateGallery

router = APIRouter()


@router.get("", response_model=list[TemplateSummary])
async def list_templates(
    domain: Optional[str] = None,
    type_filter: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    """List available extraction templates, optionally filtered by domain or type."""
    gallery = TemplateGallery.get_instance()
    templates = gallery.list(domain=domain, type_filter=type_filter)
    return [
        TemplateSummary(
            key=f"{t.domain}/{t.name}",
            name=t.name,
            domain=t.domain,
            type=t.type.value,
            description=t.description,
        )
        for t in templates
    ]


@router.get("/{domain}/{name}")
async def get_template(
    domain: str,
    name: str,
    user: dict = Depends(get_current_user),
):
    """Get full template configuration (metadata only, no LLM prompts)."""
    gallery = TemplateGallery.get_instance()
    config = gallery.get(f"{domain}/{name}")
    if not config:
        raise HTTPException(status_code=404, detail=f"Template {domain}/{name} not found")

    return _sanitize_template(config)


@router.post("/validate")
async def validate_template(
    template: dict,
    user: dict = Depends(require_admin),
):
    """Validate a custom template configuration. Returns valid=True or error details."""
    try:
        config = TemplateConfig(**template)
    except ValidationError as e:
        return {"valid": False, "errors": str(e)}

    warnings = []
    method = config.extraction.method
    template_type = config.type.value
    if not REGISTRY.is_compatible(method, template_type):
        warnings.append(
            f"Extraction method '{method}' (auto_type='{REGISTRY.get(method).auto_type if REGISTRY.get(method) else '?'}) "
            f"may not be compatible with template type '{template_type}'"
        )
    if not REGISTRY.is_implemented(method):
        warnings.append(f"Extraction method '{method}' is not yet implemented")

    result: dict = {"valid": True}
    if warnings:
        result["warnings"] = warnings
    return result


@router.get("/extraction-methods", response_model=List[dict])
async def list_extraction_methods(
    implemented_only: bool = True,
    user: dict = Depends(get_current_user),
):
    """List available extraction methods (standard, two_stage, graph_rag, light_rag)."""
    methods = REGISTRY.list(implemented_only=implemented_only)
    return [
        {
            "name": m.name,
            "auto_type": m.auto_type,
            "description": m.description,
            "implemented": m.implemented,
        }
        for m in methods
    ]


def _sanitize_template(config: TemplateConfig) -> dict:
    """Strip LLM prompt content from template before returning to frontend.

    The frontend only needs metadata (name, type, domain, fields, keys, labels).
    Prompt strings are server-side only.
    """
    result = {
        "name": config.name,
        "type": config.type.value,
        "language": config.language,
        "domain": config.domain,
        "description": config.description,
    }

    if config.entity_schema:
        result["entity_schema"] = {
            "fields": [
                {
                    "name": f.name,
                    "type": f.type.value,
                    "description": f.description,
                    "required": f.required,
                    "default": f.default,
                }
                for f in config.entity_schema.fields
            ],
            "key": config.entity_schema.key,
            "display_label": config.entity_schema.display_label,
        }

    if config.relation_schema:
        result["relation_schema"] = {
            "fields": [
                {
                    "name": f.name,
                    "type": f.type.value,
                    "description": f.description,
                    "required": f.required,
                    "default": f.default,
                }
                for f in config.relation_schema.fields
            ],
            "key": config.relation_schema.key,
            "source_field": config.relation_schema.source_field,
            "target_field": config.relation_schema.target_field,
            "display_label": config.relation_schema.display_label,
            "participants_field": config.relation_schema.participants_field,
        }

    if config.identifiers:
        result["identifiers"] = config.identifiers.model_dump()

    result["extraction"] = {
        "mode": config.extraction.mode,
        "method": config.extraction.method,
        "merge_strategy_nodes": config.extraction.merge_strategy_nodes,
        "merge_strategy_edges": config.extraction.merge_strategy_edges,
    }

    return result