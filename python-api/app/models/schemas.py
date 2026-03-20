"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field
from uuid import UUID


class ErrorResponse(BaseModel):
    error: str
    code: str
    details: dict = Field(default_factory=dict)


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: Optional[str] = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class RefreshResponse(BaseModel):
    access_token: str
    expires_in: int


class CollectionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    folder_path: Optional[str] = None


class CollectionResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    folder_path: Optional[str] = None
    status: str
    doc_count: int = 0
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


class CollectionListResponse(BaseModel):
    collections: list[CollectionResponse]


class IngestOptions(BaseModel):
    max_cost_usd: Optional[float] = None
    ocr_enabled: bool = False
    max_files: int = 10_000
    max_depth: int = 5
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 50


class IngestFolderRequest(BaseModel):
    collection_id: str
    folder_path: str
    options: IngestOptions = Field(default_factory=IngestOptions)


class IngestJobResponse(BaseModel):
    id: str
    collection_id: str
    status: str
    progress: float = 0.0
    total_docs: int = 0
    processed_docs: int = 0
    current_file: Optional[str] = None
    error_msg: Optional[str] = None
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    created_at: Optional[int] = None


class IngestJobListResponse(BaseModel):
    jobs: list[IngestJobResponse]
    total: int


class SearchRequest(BaseModel):
    query: str
    collection_ids: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    depth: int = 2
    limit: int = 20
    offset: int = 0
    mode: str = "hybrid"
    weights: dict = Field(default_factory=lambda: {"vector": 0.6, "keyword": 0.3, "graph": 0.1})
    timeout_ms: int = 800


class SearchResultItem(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: Optional[str] = None
    text: str
    page: Optional[int] = None
    vector_score: float = 0.0
    keyword_score: float = 0.0
    graph_proximity_score: float = 0.0
    final_score: float = 0.0
    topics: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int
    offset: int
    limit: int
    latency_ms: int = 0
    search_mode: str = "hybrid"


class SuggestionResponse(BaseModel):
    suggestions: list[str]


class DocumentResponse(BaseModel):
    id: str
    title: str
    file_type: str
    path: Optional[str] = None
    doc_summary: Optional[str] = None
    created_at: Optional[int] = None
    metadata: Optional[dict] = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int


class DocumentDetailResponse(BaseModel):
    document: DocumentResponse
    chunks: list[dict]
    chunk_count: int
