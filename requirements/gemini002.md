To build a robust, production-grade system, we need to define the low-level data structures and the interaction between the Rust core and the Python LLM orchestration layer.

-----

## 1\. Database Schema (LanceDB)

LanceDB uses a columnar format that is highly efficient for both vector and scalar data. We will maintain three primary tables to represent the knowledge graph.

### **Table: `documents`**

Stores the raw source material and its global embeddings for broad semantic search.
| Column | Type | Description |
| :--- | :--- | :--- |
| `doc_id` | UUID (PK) | Unique identifier for the document. |
| `content` | String | The text chunk or summary. |
| `vector` | FixedSizeList(F32) | Embedding of the document chunk. |
| `metadata` | JSON | File path, source (Local/GDrive), and timestamps. |

### **Table: `nodes` (Entities)**

Represents the "nouns" in your knowledge graph.
| Column | Type | Description |
| :--- | :--- | :--- |
| `node_id` | String | The entity name or a unique hash (e.g., "Rust\_Programming"). |
| `type` | String | Category (Technology, Person, Concept). |
| `description` | String | LLM-generated summary of the entity. |
| `vector` | FixedSizeList(F32) | Embedding for entity-based semantic retrieval. |
| `topics` | List\<String\> | Extracted high-level tags for filtering. |

### **Table: `edges` (Relationships)**

Represents the "verbs" or connections between nodes.
| Column | Type | Description |
| :--- | :--- | :--- |
| `source_id` | String | `node_id` of the subject. |
| `target_id` | String | `node_id` of the object. |
| `relation` | String | The type of connection (e.g., "IMPLEMENTS", "USED\_BY"). |
| `weight` | Float | Strength of connection based on frequency of occurrence. |
| `doc_origins` | List\<UUID\> | List of `doc_id`s where this relationship was found. |

-----

## 2\. The Rust-Python Bridge (PyO3/Maturin)

Since you are using Python for LLM interfacing (likely via LangChain or LlamaIndex) and Rust for the core, the integration should be handled as follows:

  * **Rust as the Library:** Develop the indexing logic and LanceDB interactions in Rust.
  * **Python as the Orchestrator:** Use Python to call the LLM (OpenAI/Anthropic) to parse chunks into JSON triplets (`Subject`, `Predicate`, `Object`).
  * **The Glue:** Python passes these triplets to the Rust library using **PyO3**.

**Workflow Example:**

1.  Python reads a file and calls the LLM.
2.  LLM returns: `{"subject": "Rust", "predicate": "uses", "object": "Cargo"}`.
3.  Python calls `rust_kg_lib.add_relation("Rust", "uses", "Cargo", doc_id)`.
4.  Rust handles the LanceDB upsert and vectorization.

-----

## 3\. Search & Navigation Logic

The application will provide a **Hybrid Search** capability to ensure the user finds exactly what they need.

### **Search Modes**

  * **Vector Search:** "Find documents similar to 'memory safety in systems languages'."
  * **Graph Traversal:** "Find all technologies that 'Rust' is 'connected to'."
  * **Topic Filtering:** The frontend sends a list of selected topics; the backend filters the `nodes` table by the `topics` array column before performing the vector search.

-----

## 4\. Frontend Architecture (React + Vite)

The frontend must manage a complex state involving both a standard search UI and a visual graph.

### **State Management & UI**

  * **Material UI (MUI):**
      * `Drawer` for topic filters (Checkboxes generated from unique tags in LanceDB).
      * `DataGrid` for listing source documents.
      * `AppBar` with Google Login/User Profile.
  * **Graph Visualization:**
      * Use `react-force-graph-2d` for the main navigation.
      * **Node Interaction:** Clicking a node triggers an API call `GET /nodes/{id}/details` to show the description and linked documents in a sidebar.
  * **Authentication:**
      * Use `@react-oauth/google` for the frontend handshake.
      * The backend validates the token and returns a signed JWT.

-----

## 5\. API Endpoints (Rust Axum/Actix)

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/auth/google` | POST | Exchanges Google ID token for a system JWT. |
| `/search` | GET | Query params for string, vector, and topic filters. |
| `/graph/data` | GET | Returns nodes and links for the visualizer (paginated). |
| `/nodes/{id}` | GET | Fetches full metadata and document links for a specific entity. |
| `/ingest/local` | POST | Triggers the scan of a specific local directory. |

-----

## 6\. Implementation Roadmap

### **Phase 1: Core (MVP)**

1.  Set up the Rust project with `lancedb` and `pyo3`.
2.  Build the CLI tool to index a local folder into LanceDB.
3.  Implement a simple "Triple Extraction" script in Python.

### **Phase 2: Backend & Auth**

1.  Develop the Axum web server in Rust.
2.  Integrate Google OAuth2 and JWT logic.
3.  Expose search and graph data endpoints.

### **Phase 3: Frontend & Visualization**

1.  Scaffold React + Vite + MUI.
2.  Implement the Force-Directed Graph.
3.  Add the Topic Filter sidebar.

**Would you like me to generate the initial `Cargo.toml` with the necessary dependencies or a sample Python script for the LLM triplet extraction?**
