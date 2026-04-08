# Bot 2 — Review: Phase 11 (Domain Template Library + Extraction Method Registry)

> **Features**: F8 + F9

---

## Review Checklist

### A. YAML Template Validity

- [ ] Every `.yaml` file in `templates/presets/` parses into `TemplateConfig` without ValidationError
- [ ] Graph templates have `entity_schema` and `relation_schema`
- [ ] Hypergraph templates have `participants_field` in `relation_schema`
- [ ] Temporal templates have `time_field` in `identifiers`
- [ ] Spatial templates have `location_field` in `identifiers`
- [ ] Spatio-temporal templates have both `time_field` and `location_field`
- [ ] Every template has `extraction.mode` explicitly set
- [ ] All field `name` values use `snake_case`
- [ ] All field `type` values are from the valid `FieldType` enum (string, integer, float, boolean, list)
- [ ] No field has `required: true` AND a `default` value
- **Severity**: HIGH — invalid templates crash at load time

### B. Domain Template Quality

- [ ] Legal templates emphasize precision: "only extract explicitly stated relationships"
- [ ] Medical templates emphasize precision: "only extract explicitly stated clinical relationships"
- [ ] Financial templates include transaction-specific predicates
- [ ] Supply chain template includes location and time fields
- [ ] Biography template includes observation_time context
- [ ] Concept graph includes hierarchy predicates (is_a, part_of)
- [ ] No template copies Hyper-Extract YAML verbatim — all are written from scratch
- [ ] All template descriptions are in English (`language: [en]`)
- **Severity**: MEDIUM — prompt quality affects extraction quality

### C. Extraction Method Registry

- [ ] `ExtractionRegistry.register()` adds method to `_methods` dict
- [ ] `ExtractionRegistry.get()` returns method by name, None if not found
- [ ] `ExtractionRegistry.list()` returns all registered methods as `MethodInfo` objects
- [ ] `StandardExtractor` calls existing `extract_from_chunk()` function
- [ ] `TwoStageExtractor` creates a `TSE` instance and calls `extract_two_stage()`
- [ ] Registry is a class-level dict (shared across instances, no re-registration on each import)
- [ ] Future methods can be registered without modifying `extraction_registry.py` internals
- **Severity**: MEDIUM

### D. Method Auto-Type Compatibility

- [ ] `StandardExtractor.auto_type` is `"graph"`
- [ ] `TwoStageExtractor.auto_type` is `"graph"`
- [ ] Before extraction, method `auto_type` is compatible with template `type`
- [ ] Incompatible method+template combinations raise a clear error (e.g., "two_stage method cannot be used with hypergraph template")
- **Severity**: MEDIUM — silent mismatch produces wrong extraction schema

### E. Template Authoring Guide

- [ ] `AUTHORING_GUIDE.md` explains all 8 template types
- [ ] Decision tree for choosing template type is present
- [ ] Field type reference documents string, integer, float, boolean, list
- [ ] Key pattern syntax explained with examples
- [ ] Custom template validation workflow documented
- [ ] Best practices: max 5 fields, bilingual descriptions, explicit predicates
- **Severity**: LOW

### F. API Endpoints

- [ ] `GET /templates/extraction-methods` returns 200 with list of methods
- [ ] `GET /templates/extraction-methods` requires authentication
- [ ] Template Picker fetches methods from `/templates/extraction-methods`
- [ ] Template Picker auto-selects `two_stage` when template type is hypergraph, temporal, or spatio-temporal
- [ ] Method selection stored in Redux state
- **Severity**: LOW — UI-only issues

### G. Template Loading

- [ ] `TemplateGallery` loads all domains (general, legal, finance, medical, industry)
- [ ] New template count: at least 12 templates across 5 domains
- [ ] `GET /templates?domain=legal` returns only legal templates
- [ ] `GET /templates?type=graph` returns only graph-type templates
- [ ] Gallery initialization does not block server startup
- **Severity**: MEDIUM

---

## Common Mistakes

1. **Copying Hyper-Extract YAML** — templates must be written from scratch using our schema format, not translated from Hyper-Extract's format
2. **Missing `identifiers` in graph templates** — graph types without `identifiers` section will fail to produce dedup keys
3. **Missing `participants_field` in hypergraph templates** — hypergraphs without this field won't know how to extract participant lists
4. **Registry re-registration on import** — `_methods` dict should be class-level, not instance-level, to avoid re-registering on every import
5. **`StandardExtractor` calling nonexistent function** — must call the actual `extract_from_chunk()` from `app/llm/extractor.py`
6. **Template field count > 5** — keep entity and relation schemas to 5 or fewer fields each for LLM extraction quality

---

## Output Format

Standard review format.