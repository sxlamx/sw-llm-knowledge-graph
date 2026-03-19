To round out your specification, we need to move beyond the core "happy path" of indexing and searching. For a production-grade Rust/Python hybrid application, you need to address data lifecycle, system resilience, and the "Human-in-the-loop" aspect of AI-generated graphs.

Here are the critical design requirements to add to your documentation.

---

## 1. Data Lifecycle & Consistency
Since you are indexing local files and (eventually) Google Drive, the system must handle a dynamic environment where files change or disappear.

* **Incremental Indexing:** The system must use a hashing mechanism (e.g., BLAKE3) to detect file changes. Only modified or new files should trigger the expensive LLM extraction pipeline.
* **Tombstoning & Garbage Collection:** If a source document is deleted, the system must "tombstone" the associated nodes and edges. A background Rust process should periodically clean up orphaned nodes (entities that no longer have any source document origins).
* **File Watching:** Use the `notify` crate in Rust to monitor local directories for real-time updates, triggering the ingestion pipeline automatically.

## 2. LLM Operations (LLMOps) & Cost Control
LLM calls are the primary bottleneck and cost driver.

* **Rate Limiting & Backoff:** The Python/Rust bridge must implement exponential backoff to handle LLM API rate limits (e.g., OpenAI `429` errors).
* **Token Budgeting:** The system should track token usage per user or per collection.
    * **Constraint:** Allow users to set a "Max Dollars/Tokens" cap for an indexing job.
* **Extraction Schema Validation:** Since LLMs can be hallucinatory, the Rust layer must validate the JSON output of the triplet extraction. If the LLM returns malformed JSON or invalid entity types, the job must be flagged for retry or manual review.

## 3. Advanced Multi-Tenancy
Given you are using Google ID for authentication, you need to define how data is isolated.

* **Namespace Isolation:** In LanceDB, leverage separate "Collections" or "Table Sharding" per user/organization to ensure one user's vector search never surfaces another user's private data.
* **Scoped JWTs:** The JWT issued after Google Login should include a `tenant_id` claim. Every Rust database query must be hard-coded to filter by this `tenant_id`.

## 4. Concurrency & Job Management
Rust's `tokio` runtime is perfect for managing the heavy lifting without blocking the UI.

* **Background Worker Queue:** Use a library like `sidekiq-rs` or a simple internal `mpsc` (Multi-Producer, Single-Consumer) channel to manage indexing jobs.
* **Status Streaming:** The backend should provide a **Server-Sent Events (SSE)** or WebSocket endpoint so the React frontend can show a real-time progress bar (e.g., "Extracting entities from Doc 4/10...").

---

## 5. Quality Attributes (Non-Functional Requirements)

| Attribute | Requirement |
| :--- | :--- |
| **Observability** | Integrate `tracing` and `OpenTelemetry` in Rust to track latency in the LLM-to-LanceDB pipeline. |
| **Portability** | The Rust core should be compiled to a static binary (using `musl`) for easy deployment in Docker or on-prem. |
| **Graph Density Control** | Implement a "Relationship Pruning" setting to avoid "hairball" visualizations (e.g., only show edges with a weight > $0.5$). |
| **Exportability** | Support exporting the graph to standard formats like `GraphML` or `JSONL` for use in Neo4j or Gephi. |

---

## 6. The "Human-in-the-Loop" Editor
LLMs aren't perfect. Your graph will eventually contain wrong connections.

* **Manual Node/Edge Override:** The React frontend should allow users to manually create an edge or edit a node’s description.
* **Feedback Loop:** When a user deletes an LLM-generated edge, store that "negative signal." Use it in future prompts to tell the LLM: *"Do not connect Entity A to Entity B."*

