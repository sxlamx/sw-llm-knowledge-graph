# 03 — Ingestion Pipeline

## 1. Overview

The ingestion pipeline transforms raw documents into enriched knowledge graph entries. It is a
multi-stage pipeline that spans both Rust (performance-critical) and Python (LLM orchestration)
layers. The pipeline is designed for maximum throughput while respecting LLM API rate limits and
cost budgets.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         INGESTION PIPELINE                               │
│                                                                          │
│  Stage 1          Stage 2          Stage 3*         Stage 4             │
│  File Discovery → Text Extraction → Doc Summary  → Contextual Chunking  │
│  (Rust/Python)    (Python)          (Python/LLM)*   (Python)            │
│                                                                          │
│  Stage 5          Stage 6          Stage 7*         Stage 8*            │
│  Embedding Gen  → NER Tagging   → LLM Entity     → Ontology Validate    │
│  (Python/HF)      (Python/spaCy)   Extract*         (Rust)              │
│                                    (Python/LLM)                         │
│                                                                          │
│  Stage 9          Stage 10         Stage 11                             │
│  Entity Resolve → Graph Construct → Index Update                        │
│  (Rust)           (Rust)            (Rust)                              │
└──────────────────────────────────────────────────────────────────────────┘

* = gated behind config flag; disabled by default in Phase 1
  Stage 3  → ENABLE_CONTEXTUAL_PREFIX=false (doc summary still runs)
  Stage 7  → part of enable_contextual_prefix pipeline
  Stage 7/8→ LLM entity extraction disabled by default; NER is always-on
```

---

## 2. Stage 1: File Discovery

**Implemented in**: `rust-core/src/ingestion/scanner.rs`

### Scanning Algorithm

```rust
pub struct FileScanner {
    root_path: PathBuf,
    max_depth: usize,      // default: 5
    max_files: usize,      // default: 10_000
    supported_extensions: HashSet<&'static str>,
}

impl FileScanner {
    pub fn scan(&self) -> impl Iterator<Item = FileEntry> {
        // Recursive WalkDir with depth limit
        // Returns entries sorted by modification time (newest first)
    }
}

pub struct FileEntry {
    pub path: PathBuf,
    pub file_type: FileType,  // PDF, DOCX, MD, TXT, HTML
    pub size_bytes: u64,
    pub modified_at: SystemTime,
    pub blake3_hash: Option<[u8; 32]>,  // computed on demand
}
```

### Supported File Types

| Extension | File Type | Extractor |
|-----------|-----------|-----------|
| `.pdf` | PDF | `pdf-extract` / `lopdf` |
| `.docx` | DOCX | `docx-rs` |
| `.md`, `.markdown` | Markdown | `pulldown-cmark` |
| `.txt` | Plain Text | Direct read |
| `.html`, `.htm` | HTML | `scraper` |
| `.rst` | reStructuredText | Plaintext fallback |

### Incremental Update (BLAKE3 Hash Check)

Before processing any file, the scanner computes its BLAKE3 hash and checks against the stored
hash in the `documents` LanceDB table. If the hash matches, the file is skipped.

```rust
pub fn check_file_changed(path: &Path, stored_hash: Option<&str>) -> bool {
    let mut hasher = blake3::Hasher::new();
    let mut file = File::open(path).expect("file open");
    let mut buf = [0u8; 65536];
    loop {
        let n = file.read(&mut buf).unwrap();
        if n == 0 { break; }
        hasher.update(&buf[..n]);
    }
    let hash = hasher.finalize().to_hex();
    stored_hash.map_or(true, |h| h != hash.as_str())
}
```

### Live File Watching (`notify` Crate)

The `notify` crate is used to watch the collection folder for changes. File system events trigger
incremental ingestion:

```rust
use notify::{RecommendedWatcher, RecursiveMode, Watcher, Event};

pub fn start_file_watcher(
    path: &Path,
    tx: mpsc::Sender<FileEvent>,
) -> Result<RecommendedWatcher> {
    let mut watcher = notify::recommended_watcher(move |res: notify::Result<Event>| {
        if let Ok(event) = res {
            let _ = tx.blocking_send(FileEvent::from(event));
        }
    })?;
    watcher.watch(path, RecursiveMode::Recursive)?;
    Ok(watcher)
}
```

Events processed:
- `Create` → queue new file for ingestion
- `Modify` → re-ingest if hash changed
- `Remove` → tombstone document and its nodes/edges

---

## 3. Stage 2: Text Extraction

**Implemented in**: `rust-core/src/ingestion/extractor.rs`

### Extractor Trait

```rust
#[async_trait]
pub trait TextExtractor: Send + Sync {
    async fn extract(&self, path: &Path) -> Result<ExtractedDocument>;
}

pub struct ExtractedDocument {
    pub title: Option<String>,
    pub raw_text: String,
    pub pages: Vec<PageContent>,   // For PDFs: per-page text
    pub metadata: HashMap<String, serde_json::Value>,
    pub file_type: FileType,
}

pub struct PageContent {
    pub page_number: i32,
    pub text: String,
}
```

### PDF Extraction

Uses `lopdf` for direct text extraction. Falls back to `pdf-extract` for complex layouts.
OCR support (via Tesseract FFI) is a placeholder for Phase 4.

```rust
pub struct PdfExtractor;

impl TextExtractor for PdfExtractor {
    async fn extract(&self, path: &Path) -> Result<ExtractedDocument> {
        let doc = lopdf::Document::load(path)?;
        let pages = doc.get_pages();
        let mut page_contents = Vec::new();

        for (page_num, page_id) in pages {
            let text = doc.extract_text(&[page_num])
                .unwrap_or_default();
            page_contents.push(PageContent {
                page_number: page_num as i32,
                text,
            });
        }

        Ok(ExtractedDocument {
            title: extract_pdf_title(&doc),
            raw_text: page_contents.iter().map(|p| p.text.as_str()).collect::<Vec<_>>().join("\n"),
            pages: page_contents,
            metadata: extract_pdf_metadata(&doc),
            file_type: FileType::Pdf,
        })
    }
}
```

### DOCX Extraction

```rust
pub struct DocxExtractor;

impl TextExtractor for DocxExtractor {
    async fn extract(&self, path: &Path) -> Result<ExtractedDocument> {
        let docx = docx_rs::read_docx(&std::fs::read(path)?)?;
        // Extract paragraphs maintaining heading structure
        let text = extract_docx_text(&docx);
        Ok(ExtractedDocument { raw_text: text, .. })
    }
}
```

### Markdown Extraction

Uses `pulldown-cmark` to parse markdown and extract text while preserving heading structure for
semantic boundary detection in the chunker.

---

## 4. Stage 3: Global Document Analysis (Python/LLM)

**Implemented in**: `python-api/app/llm/chunker.py`

On the first 4,000 tokens of the document, the LLM generates a 200-300 word summary. This summary
is stored in `documents.doc_summary` and used to prefix contextual chunks with document-level
context.

```python
async def generate_doc_summary(raw_text: str, model: str = "gpt-4o-mini") -> str:
    """Generate a 200-300 word document summary from the first 4000 tokens."""
    truncated = truncate_to_tokens(raw_text, max_tokens=4000)
    response = await openai_client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a document analyst. Provide a 200-300 word summary of the document "
                    "covering its main topics, purpose, and key entities mentioned. "
                    "Be factual and concise."
                )
            },
            {"role": "user", "content": f"Document:\n\n{truncated}"}
        ],
        temperature=0.1,
        max_tokens=400,
    )
    return response.choices[0].message.content.strip()
```

---

## 5. Stage 4: Contextual Chunking

**Rust layer**: `rust-core/src/ingestion/chunker.rs` (splitting logic)
**Python layer**: `python-api/app/llm/chunker.py` (prefix generation)

### Chunking Strategy

- **Chunk size**: 512 tokens (configurable via `IngestOptions.chunk_size_tokens`)
- **Overlap**: 50 tokens (configurable via `IngestOptions.chunk_overlap_tokens`)
- **Boundary detection**: `text-splitter` crate with semantic boundary awareness
  (splits at paragraph breaks and heading boundaries first; falls back to token boundaries)

```rust
use text_splitter::{ChunkConfig, TextSplitter};

pub struct Chunker {
    config: ChunkConfig,
}

impl Chunker {
    pub fn new(chunk_size: usize, overlap: usize) -> Self {
        let config = ChunkConfig::new(chunk_size)
            .with_overlap(overlap)
            .with_trim(true);
        Self { config: ChunkConfig::new(chunk_size) }
    }

    pub fn chunk_document(&self, doc: &ExtractedDocument) -> Vec<RawChunk> {
        let splitter = TextSplitter::new(self.config.clone());
        let mut chunks = Vec::new();
        let mut position = 0i32;

        for page in &doc.pages {
            for chunk_text in splitter.chunks(&page.text) {
                chunks.push(RawChunk {
                    text: chunk_text.to_string(),
                    position,
                    page: page.page_number,
                    token_count: estimate_tokens(chunk_text) as i32,
                });
                position += 1;
            }
        }
        chunks
    }
}
```

### Contextual Prefix Generation (Python) — Phase 2, Config-Gated

> **Config flag**: `ENABLE_CONTEXTUAL_PREFIX=false` (default). Set to `true` to enable.
> Disabled by default in Phase 1 to avoid LLM cost and latency per chunk.

For each chunk, a micro-LLM call generates a 2-sentence contextual prefix that situates the chunk
within the document. This dramatically improves embedding quality for out-of-context chunks.

```python
CONTEXTUAL_PREFIX_PROMPT = """
<document_summary>
{doc_summary}
</document_summary>

<chunk>
{chunk_text}
</chunk>

In 2 sentences, describe what this chunk is about within the context of the above document.
Be specific and mention any key entities or concepts. Output ONLY the 2 sentences, nothing else.
"""

async def generate_contextual_prefix(
    doc_summary: str,
    chunk_text: str,
    model: str = "gpt-4o-mini",
) -> str:
    response = await openai_client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": CONTEXTUAL_PREFIX_PROMPT.format(
                doc_summary=doc_summary,
                chunk_text=chunk_text[:1000],  # truncate for context
            )
        }],
        temperature=0.0,
        max_tokens=100,
    )
    prefix = response.choices[0].message.content.strip()
    return f"{prefix}\n\n{chunk_text}"  # enriched contextual_text
```

---

## 6. Stage 5: Embedding Generation (Python)

**Implemented in**: `python-api/app/llm/embedder.py`

> **Deviation from original spec**: The original spec used OpenAI `text-embedding-3-large`
> (1536-dim). The actual implementation uses a **local HuggingFace sentence-transformers model**
> (`Qwen/Qwen3-Embedding-0.6B`, 1024-dim by default). This eliminates OpenAI embedding costs and
> allows fully offline operation. The dimension is configurable via `settings.embedding_dimension`.

Embeddings are generated using `sentence-transformers` running locally. The model is loaded once
at startup (GPU-accelerated if available) and reused for all embedding calls.

```python
# python-api/app/llm/embedder.py (simplified)

from sentence_transformers import SentenceTransformer
from app.config import get_settings

settings = get_settings()

# Model: Qwen/Qwen3-Embedding-0.6B (default)
# Dimension: settings.embedding_dimension (default 1024)
# Separate prompt instructions for passages (indexing) vs queries (search)

_PASSAGE_PROMPT = ""
_QUERY_PROMPT = "Instruct: Given a search query, retrieve relevant document passages.\nQuery: "

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed document passages. Model loads once; results cached by first 100 chars."""
    ...

async def embed_query(query: str) -> list[float]:
    """Embed a search query using the query instruction prompt for Qwen3."""
    ...
```

Key properties:
- **Batch size**: 32 per inference call (GPU batch)
- **Dimension truncation**: MRL — output truncated to `settings.embedding_dimension` (default 1024)
- **In-process cache**: LRU dict keyed by first 100 chars of text
- **Fallback**: zero vector on model load failure (logged as warning)

---

## 6.5. Stage 5b: NER Tagging (spaCy)

**Implemented in**: `python-api/app/llm/ner_tagger.py`

Named entity recognition runs as a **separate pass** over all chunks after ingestion.
It is always-on (not gated by a config flag) and uses `en_core_web_trf` (transformer model).

> **CRITICAL**: Always use `en_core_web_trf`. Never fall back to `en_core_web_sm`.
> sm-tagged chunks produce significantly lower-quality entity extraction and must be
> reprocessed. If trf is not installed: `python -m spacy download en_core_web_trf`

### Label Mapping: `SPACY_TO_CANONICAL`

spaCy labels are normalized to canonical labels before storage:

```python
SPACY_TO_CANONICAL: dict[str, str] = {
    "PERSON": "PERSON",
    "ORG":    "ORGANIZATION",
    "GPE":    "LOCATION",
    "LOC":    "LOCATION",
    "FAC":    "LOCATION",
    "DATE":   "DATE",
    "TIME":   "DATE",
    "MONEY":  "MONEY",
    "PERCENT":"PERCENT",
    "LAW":    "LAW",
    "NORP":   "ORGANIZATION",  # nationalities, religious/political groups
}
```

### Legal NER Labels (LLM pass)

Domain-specific legal labels are extracted by a secondary LLM pass using the Ollama model:

```python
LEGAL_NER_LABELS = [
    "LEGISLATION_TITLE", "LEGISLATION_REFERENCE", "STATUTE_SECTION",
    "COURT_CASE", "JURISDICTION", "LEGAL_CONCEPT", "DEFINED_TERM",
    "COURT", "JUDGE", "LAWYER", "PETITIONER", "RESPONDENT", "WITNESS",
    "CASE_CITATION",
]
```

### NER Version

`NER_VERSION = 3`. Chunks store their NER version; `get_outdated_ner_chunks()` returns
all chunks below the current version for reprocessing. Increment `NER_VERSION` whenever
labels, logic, or the spaCy model changes.

### Batch Processing

NER runs in batches of 200 chunks with 16 concurrent spaCy workers. Results are written
to LanceDB in batches to minimise per-row write overhead.

```python
_NER_BATCH_SIZE = 200    # flush to LanceDB every N results
_NER_CONCURRENCY = 16    # parallel asyncio workers (each calls spaCy sync via executor)
```

### NER Tag Schema

Each chunk's `ner_tags` column stores a JSON array of tag objects:

```json
[
  { "label": "PERSON", "text": "John Smith", "start": 12, "end": 22, "score": 0.99 },
  { "label": "ORGANIZATION", "text": "OpenAI", "start": 35, "end": 41, "score": 0.95 }
]
```

---

## 7. Stage 6: LLM Entity/Relation Extraction (Python)

**Implemented in**: `python-api/app/llm/extractor.py`

Ontology-guided extraction using structured JSON output via Pydantic. The current ontology is
injected into the system prompt to constrain the LLM output.

### Pydantic Output Schema

```python
from pydantic import BaseModel, Field
from enum import Enum

class ExtractedEntity(BaseModel):
    name: str = Field(..., description="Canonical entity name")
    entity_type: str = Field(..., description="Ontology entity type")
    description: str = Field(..., description="1-2 sentence description")
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)

class ExtractedRelationship(BaseModel):
    source: str = Field(..., description="Source entity name")
    target: str = Field(..., description="Target entity name")
    predicate: str = Field(..., description="Relationship type from ontology")
    context: str = Field(..., description="Supporting sentence from text")
    confidence: float = Field(..., ge=0.0, le=1.0)

class ExtractionResult(BaseModel):
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelationship]
    topics: list[str] = Field(default_factory=list)
    summary: str = Field(..., description="1-sentence chunk summary")
```

### Extraction Prompt

```python
EXTRACTION_PROMPT = """
You are a knowledge graph extraction system. Extract entities and relationships from the text.

ALLOWED ENTITY TYPES: {entity_types}
ALLOWED RELATIONSHIP TYPES: {relationship_types}

Rules:
1. Only use entity types and relationship types from the lists above.
2. Each relationship must have valid domain and range entity types per the ontology.
3. Confidence should reflect extraction certainty (0.0-1.0).
4. Return ONLY valid JSON matching the schema. No explanation.

TEXT:
{chunk_text}

JSON SCHEMA:
{json_schema}
"""

async def extract_from_chunk(
    chunk_text: str,
    ontology: Ontology,
    model: str = "gpt-4o",
    max_retries: int = 3,
) -> ExtractionResult:
    prompt = EXTRACTION_PROMPT.format(
        entity_types=json.dumps(ontology.entity_types),
        relationship_types=json.dumps(ontology.relationship_types),
        chunk_text=chunk_text,
        json_schema=ExtractionResult.model_json_schema(),
    )

    for attempt in range(max_retries):
        try:
            response = await openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
                max_tokens=2000,
            )
            raw = response.choices[0].message.content
            return ExtractionResult.model_validate_json(raw)
        except (ValidationError, json.JSONDecodeError) as e:
            if attempt == max_retries - 1:
                raise ExtractionError(f"Failed after {max_retries} retries: {e}")
            await asyncio.sleep(2 ** attempt)  # exponential backoff
```

---

## 8. Stage 7: Rust Ontology Validation

**Implemented in**: `rust-core/src/ontology/validator.rs`

After the Python LLM extraction, the result is passed to the Rust validator via PyO3. The validator
checks each entity and relationship against the loaded ontology.

```rust
pub fn validate_extraction_result(
    result: &ExtractionResult,
    ontology: &Ontology,
) -> ValidationReport {
    let mut valid_entities = Vec::new();
    let mut valid_relationships = Vec::new();
    let mut dropped_entities = Vec::new();
    let mut dropped_relationships = Vec::new();

    for entity in &result.entities {
        match ontology.validate_entity(entity) {
            Ok(()) => valid_entities.push(entity.clone()),
            Err(e) => dropped_entities.push((entity.clone(), e)),
        }
    }

    for rel in &result.relationships {
        // Check domain (source entity type) and range (target entity type)
        match ontology.validate_relationship(rel, &valid_entities) {
            Ok(()) => valid_relationships.push(rel.clone()),
            Err(e) => dropped_relationships.push((rel.clone(), e)),
        }
    }

    ValidationReport { valid_entities, valid_relationships, dropped_entities, dropped_relationships }
}
```

---

## 9. Stage 8: Entity Resolution (Rust)

**Implemented in**: `rust-core/src/graph/builder.rs`

Entity resolution deduplicates extracted entities against existing graph nodes to prevent
fragmentation.

### Resolution Algorithm

```rust
pub struct EntityResolver {
    levenshtein_threshold: usize,   // default: 3
    embedding_threshold: f32,       // default: 0.92
}

impl EntityResolver {
    pub fn resolve(
        &self,
        candidate: &ExtractedEntity,
        existing_nodes: &[GraphNode],
        candidate_embedding: &[f32],
    ) -> Resolution {
        // Step 1: Exact name match (case-insensitive, normalized)
        let normalized = normalize_name(&candidate.name);
        if let Some(node) = existing_nodes.iter()
            .find(|n| normalize_name(&n.label) == normalized
                  || n.aliases.iter().any(|a| normalize_name(a) == normalized))
        {
            return Resolution::Merge { existing_id: node.id, strategy: MergeStrategy::ExactMatch };
        }

        // Step 2: Levenshtein distance < threshold
        for node in existing_nodes {
            let dist = strsim::levenshtein(&normalized, &normalize_name(&node.label));
            if dist < self.levenshtein_threshold && node.node_type == candidate.entity_type {
                // Verify with embedding cosine similarity
                if let Some(existing_emb) = &node.embedding {
                    let cos_sim = cosine_similarity(candidate_embedding, existing_emb);
                    if cos_sim > self.embedding_threshold {
                        return Resolution::Merge {
                            existing_id: node.id,
                            strategy: MergeStrategy::FuzzyMatch { distance: dist, cosine_sim: cos_sim },
                        };
                    }
                }
            }
        }

        Resolution::NewNode
    }
}

pub enum Resolution {
    Merge { existing_id: Uuid, strategy: MergeStrategy },
    NewNode,
}

pub enum MergeStrategy {
    ExactMatch,
    FuzzyMatch { distance: usize, cosine_sim: f32 },
}
```

### Merge Strategy

When merging two entities:
1. Keep the earliest `id` (canonical ID)
2. Merge `aliases` lists (union)
3. Average `confidence` scores
4. Keep the longer `description`
5. Merge `properties` maps (newer values win)

---

## 10. Stage 9: Graph Construction (Rust)

**Implemented in**: `rust-core/src/graph/builder.rs`

```rust
pub async fn construct_graph_batch(
    validated: &ValidationReport,
    embeddings: HashMap<String, Vec<f32>>,  // entity_name → embedding
    index_manager: &IndexManager,
    collection_id: Uuid,
    chunk_id: Uuid,
) -> Result<GraphWriteReport> {
    // 1. Convert validated entities to GraphNode structs
    let mut new_nodes = Vec::new();
    let mut node_id_map: HashMap<String, Uuid> = HashMap::new();  // name → final UUID

    let existing_nodes = index_manager.get_all_nodes(collection_id).await?;

    for entity in &validated.valid_entities {
        let embedding = embeddings.get(&entity.name).cloned().unwrap_or_default();
        let resolution = index_manager.entity_resolver.resolve(entity, &existing_nodes, &embedding);

        match resolution {
            Resolution::Merge { existing_id, .. } => {
                // Update aliases on existing node
                index_manager.merge_node_aliases(existing_id, &entity.aliases).await?;
                node_id_map.insert(entity.name.clone(), existing_id);
            }
            Resolution::NewNode => {
                let node = GraphNode {
                    id: Uuid::new_v4(),
                    node_type: entity.entity_type.parse()?,
                    label: entity.name.clone(),
                    description: Some(entity.description.clone()),
                    aliases: entity.aliases.clone(),
                    confidence: entity.confidence,
                    ontology_class: None,
                    properties: HashMap::new(),
                    collection_id,
                };
                node_id_map.insert(entity.name.clone(), node.id);
                new_nodes.push(node);
            }
        }
    }

    // 2. Convert validated relationships to GraphEdge structs
    let mut new_edges = Vec::new();
    for rel in &validated.valid_relationships {
        let source_id = node_id_map.get(&rel.source).copied().ok_or(GraphError::MissingNode)?;
        let target_id = node_id_map.get(&rel.target).copied().ok_or(GraphError::MissingNode)?;
        new_edges.push(GraphEdge {
            id: Uuid::new_v4(),
            source: source_id,
            target: target_id,
            edge_type: rel.predicate.parse()?,
            weight: rel.confidence,
            context: Some(rel.context.clone()),
            chunk_id: Some(chunk_id),
            properties: HashMap::new(),
        });
    }

    // 3. Batch upsert to LanceDB (Arrow RecordBatch)
    index_manager.upsert_nodes_batch(&new_nodes, collection_id).await?;
    index_manager.upsert_edges_batch(&new_edges, collection_id).await?;

    // 4. Update in-memory graph (brief write lock)
    {
        let mut graph = index_manager.graph.write().await;
        graph.insert_nodes_batch(new_nodes.clone());
        graph.insert_edges_batch(new_edges.clone());
        // version counter incremented inside insert methods
    }

    Ok(GraphWriteReport { added_nodes: new_nodes.len(), added_edges: new_edges.len() })
}
```

---

## 11. Stage 10: Index Update

After a batch of chunks is processed, if more than 1,000 new vectors have been added since the
last IVF-PQ rebuild, a background index compaction is triggered:

```rust
pub async fn maybe_trigger_index_rebuild(
    index_manager: &IndexManager,
    collection_id: Uuid,
    new_vector_count: usize,
) {
    let pending = index_manager.pending_writes.fetch_add(new_vector_count as u64,
                                                          Ordering::AcqRel);
    if pending + new_vector_count as u64 > INDEX_REBUILD_THRESHOLD {
        if index_manager.state.compare_exchange(
            IndexState::Active as u8,
            IndexState::Compacting as u8,
            Ordering::AcqRel,
            Ordering::Relaxed,
        ).is_ok() {
            let mgr = index_manager.clone();
            tokio::spawn(async move {
                mgr.rebuild_ivf_pq_index(collection_id).await.unwrap_or_else(|e| {
                    tracing::error!("Index rebuild failed: {}", e);
                    mgr.state.store(IndexState::Active as u8, Ordering::Release);
                });
            });
        }
    }
}
```

---

## 12. Concurrency Model

### Parallel Chunk Processing

```
Ingest Job
    │
    ├── Stage 1-2: Sequential file scan + extraction (Rayon parallel_iter across files)
    │
    ├── Stage 3: One LLM summary call per document (tokio::spawn per doc)
    │
    ├── Stages 4-6: Per-document pipeline
    │   ├── Chunk (sync, Rayon)
    │   └── tokio::JoinSet for concurrent chunk processing:
    │       ├── Task 1: generate_contextual_prefix(chunk_0) [LLM semaphore]
    │       ├── Task 2: generate_contextual_prefix(chunk_1) [LLM semaphore]
    │       ├── ...
    │       └── Task N: generate_contextual_prefix(chunk_N) [LLM semaphore]
    │
    ├── Stage 5: Batch embed all chunks (single API call per 100)
    │
    └── Stages 6-9: Per-chunk extraction pipeline
        └── tokio::JoinSet (bounded by semaphore, max 20 concurrent LLM calls)
            ├── extract(chunk_0) → validate → resolve → graph write
            ├── extract(chunk_1) → validate → resolve → graph write
            └── ...
```

### LLM Call Semaphore

```rust
const MAX_CONCURRENT_LLM_CALLS: usize = 20;
let llm_semaphore = Arc::new(Semaphore::new(MAX_CONCURRENT_LLM_CALLS));

let mut join_set = JoinSet::new();
for chunk in chunks {
    let permit = llm_semaphore.clone().acquire_owned().await.unwrap();
    let chunk = chunk.clone();
    join_set.spawn(async move {
        let _permit = permit; // dropped when task completes
        process_chunk(chunk).await
    });
}
while let Some(result) = join_set.join_next().await {
    // handle result, update progress
}
```

### MPSC Job Queue

```rust
// Job dispatch channel
let (job_tx, mut job_rx) = mpsc::channel::<(IngestJob, oneshot::Sender<JobStatus>)>(256);

// Job worker (runs in background tokio task)
tokio::spawn(async move {
    while let Some((job, status_tx)) = job_rx.recv().await {
        let status = run_ingest_pipeline(job).await
            .unwrap_or_else(|e| JobStatus::Failed { error: e.to_string() });
        let _ = status_tx.send(status);
    }
});
```

---

## 13. Cost Controls

```python
class CostTracker:
    def __init__(self, max_cost_usd: float):
        self.max_cost_usd = max_cost_usd
        self.spent_usd = 0.0
        self.token_counts = defaultdict(int)

    def record_usage(self, model: str, prompt_tokens: int, completion_tokens: int):
        cost = calculate_cost(model, prompt_tokens, completion_tokens)
        self.spent_usd += cost
        self.token_counts[model] += prompt_tokens + completion_tokens

    def check_budget(self):
        if self.spent_usd >= self.max_cost_usd:
            raise BudgetExceededError(
                f"Cost limit reached: ${self.spent_usd:.4f} / ${self.max_cost_usd:.4f}"
            )

# Approximate costs (as of early 2026)
MODEL_COSTS = {
    "gpt-4o": {"input": 2.50 / 1e6, "output": 10.00 / 1e6},
    "gpt-4o-mini": {"input": 0.15 / 1e6, "output": 0.60 / 1e6},
    "text-embedding-3-large": {"input": 0.13 / 1e6, "output": 0.0},
}
```

### Exponential Backoff on 429

```python
async def call_with_retry(fn, max_retries: int = 5):
    for attempt in range(max_retries):
        try:
            return await fn()
        except openai.RateLimitError:
            wait = min(2 ** attempt + random.uniform(0, 1), 60)
            await asyncio.sleep(wait)
    raise MaxRetriesExceeded()
```

---

## 14. Progress Reporting (SSE)

The FastAPI SSE endpoint streams ingest progress to the frontend:

```python
@router.get("/ingest/jobs/{job_id}/stream")
async def stream_job_progress(job_id: UUID, current_user: User = Depends(get_current_user)):
    async def event_generator():
        async for event in job_manager.subscribe(job_id):
            yield {
                "data": json.dumps({
                    "type": "progress",
                    "job_id": str(job_id),
                    "processed": event.processed_docs,
                    "total": event.total_docs,
                    "current_file": event.current_file,
                    "progress": event.progress,
                })
            }
    return EventSourceResponse(event_generator())
```
