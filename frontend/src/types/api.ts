export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url?: string;
  /** Alias for avatar_url — used by older components */
  picture?: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface Collection {
  id: string;
  name: string;
  description?: string;
  folder_path?: string;
  status: string;
  doc_count: number;
  created_at?: number;
  updated_at?: number;
}

export interface CollectionCreate {
  name: string;
  description?: string;
  folder_path?: string;
}

export interface IngestJob {
  id: string;
  job_id: string;
  collection_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  total_docs: number;
  processed_docs: number;
  error_msg?: string;
  started_at?: number;
  completed_at?: number;
  created_at?: number;
}

export interface IngestStartRequest {
  collection_id: string;
  folder_path: string;
  options?: {
    max_files?: number;
    max_depth?: number;
    chunk_size_tokens?: number;
    chunk_overlap_tokens?: number;
  };
}

export interface SearchResult {
  id: string;
  chunk_id?: string;
  doc_id?: string;
  doc_title?: string;
  text: string;
  collection_id?: string;
  score?: number;
  final_score?: number;
  vector_score?: number;
  keyword_score?: number;
  page?: number;
  position?: number;
  highlights?: string[];
  topics?: string[];
  has_image?: boolean;
  image_b64?: string;
}

export interface SearchRequest {
  query: string;
  collection_ids: string[];
  mode?: 'vector' | 'hybrid' | 'keyword' | 'graph';
  topics?: string[];
  weights?: Record<string, number>;
  limit?: number;
  offset?: number;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  latency_ms: number;
  query: string;
}

export interface Document {
  id: string;
  title: string;
  file_type: string;
  path?: string;
  doc_summary?: string;
  created_at?: number;
  chunk_count?: number;
  status?: string;
}

export interface DocumentListResponse {
  documents: Document[];
  total: number;
}
