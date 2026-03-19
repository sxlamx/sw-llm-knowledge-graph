You can use multimodal embedding models to enrich your knowledge graph at three layers: how you **construct** it, how you **store/represent** it, and how you **query** it.[1][2]

## 1. Turning images and layouts into graph facts

Multimodal models let you extract entities and relations not just from text but from diagrams, UI screens, slides, and scanned docs.[3][4]

- Run a vision–language model (VLM) on each figure or page to get:
  - Objects and labels (e.g. “butter brioche”, “price $3.50”, “queue counter”).[4]
  - Relations like “X is part of Y”, “X connected_to Y”, “X above Y” from scene-graph style outputs.[2][4]
- Convert these into triples:
  - `(Product_123, has_label, "Butter brioche")`  
  - `(Product_123, has_price, 3.50)`  
  - `(QueueCounter_1, located_near, Product_123)`  
- Attach them to the same entity IDs you use for text-extracted facts, so text and visuals jointly populate the same node.[2]

Effect: your KG now captures information that only existed in images/layouts (e.g. table headers, arrows, architectural diagrams).

## 2. Using shared embedding space for entity alignment

Modern multimodal encoders (e.g. CLIP-like, VLMs) give you a **shared vector space** where text and images that “mean the same thing” are close.[5][2]

- For each node:
  - Store a text embedding (from its name/description).  
  - Store one or more visual embeddings (from product photos, slide figures, UI screenshots).  
- For each new image or text mention, embed it and:
  - Link it to the closest existing node if similarity > threshold.  
  - Otherwise, create a new candidate node to be reviewed.[3][2]

Effect: you get better entity resolution across modalities (e.g. the product in a catalog photo is auto-attached to the product node created from a PDF spec).

## 3. Building a true multimodal knowledge graph (MMKG)

Rather than treating images as mere attachments, you can model them as **first-class nodes** with their own features.[2]

- Node types:
  - `TextEntity`, `Image`, `Figure`, `Table`, `AudioSegment`, `Slide`.  
- Edge types:
  - `describes`, `visualizes`, `derived_from`, `appears_in`, `part_of`.  
- Each node carries a modality-specific embedding plus an optional fused multimodal embedding (e.g. figure image + caption).[5][2]

You can then run multimodal GNNs / message-passing over this graph to learn richer node representations (e.g. “this method node is strongly linked to failure diagrams and warning icons → probably high-risk step”).[6][5]

## 4. Enhancing retrieval and RAG over the graph

In a RAG setup like MegaRAG, multimodal embeddings improve both retrieval and answer grounding.[7][1]

- Retrieval:
  - Encode the user’s question with a text encoder.  
  - Search over both text and multimodal embeddings (e.g. slide figures, flowcharts), constrained by graph neighborhoods.[8][1]
  - Use the KG to expand candidates (follow edges to related nodes, then pull associated images and sections).  
- Answering:
  - Feed LLM a bundle of:
    - Text snippets (nodes/edges descriptions).  
    - Selected images (or their captions/features) tied to those nodes.[9][1]
  - Use adapters or projection layers so visual embeddings can condition the LLM alongside KG node embeddings.[6][9]

Effect: for questions about “architecture on slide 15” or “what does this dashboard show”, the system can retrieve the right **figure + text** and reason over both.[1][7]

## 5. Practical design hooks for your system

Here’s how to bake this into the modules you already scoped:

- Preprocessing:
  - Page-level multimodal units: `(page text, all figures on page, captions, layout coordinates)`.  
  - Save consistent IDs so each figure/table is addressable.  
- Embedding & Storage:
  - For each unit, store:
    - `text_embedding`, `image_embedding`, `fused_embedding`, plus modality tags in metadata.  
- KG Assembly:
  - Use multimodal similarity for:
    - Entity disambiguation (are these two product photos the same SKU?).  
    - Graph completion (if two images are similar and their text mentions differ slightly, consider adding `same_as` or `variant_of` edges).[2]
- Search:
  - Dual-mode queries:
    - Text-only (standard semantic + KG retrieval).  
    - Image-anchored (user uploads screenshot → embed → find nearest graph nodes and documents).[1][2]

If you tell me your main domain (e.g. slide decks, engineering drawings, medical images, financial reports), I can sketch a more concrete multimodal pipeline tailored to that use case.

Sources
[1] MegaRAG: Multimodal Knowledge Graph-Based Retrieval Augmented Generation https://arxiv.org/abs/2512.20626
[2] Multimodal Knowledge Graphs (MMKGs) https://www.emergentmind.com/topics/multimodal-knowledge-graphs-mmkgs
[3] Multimodal knowledge graph construction for intelligent ... https://www.tandfonline.com/doi/full/10.1080/1448837X.2024.2448376
[4] Text-Free Multimodal Knowledge Graph Construction for ... https://arxiv.org/html/2503.12972v1
[5] End-to-End Learning on Multimodal Knowledge Graphs https://www.semantic-web-journal.net/system/files/swj2727.pdf
[6] Multimodal Reasoning with Multimodal Knowledge Graph https://arxiv.org/html/2406.02030v1
[7] MegaRAG: Multimodal Knowledge Graph-Based Retrieval ... https://chatpaper.com/paper/221598
[8] MegaRAG: Multimodal Knowledge Graph-Based Retrieval ... https://arxiv.org/html/2512.20626v1
[9] Multimodal Reasoning with Multimodal Knowledge Graph https://arxiv.org/html/2406.02030v2
[10] Text-Free Multimodal Knowledge Graph Construction for ... https://huggingface.co/papers/2503.12972
