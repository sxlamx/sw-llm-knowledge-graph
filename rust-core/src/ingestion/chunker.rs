//! Text chunking with semantic boundary awareness.

use crate::ingestion::extractor::ExtractedDocument;
use text_splitter::{ChunkConfig, TextSplitter};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RawChunk {
    pub text: String,
    pub position: i32,
    pub page: i32,
    pub token_count: i32,
}

pub struct Chunker {
    chunk_size: usize,
    chunk_overlap: usize,
}

impl Chunker {
    pub fn new(chunk_size: usize, chunk_overlap: usize) -> Self {
        Self {
            chunk_size,
            chunk_overlap,
        }
    }

    pub fn chunk_document(&self, doc: &ExtractedDocument) -> Vec<RawChunk> {
        let config = ChunkConfig::new(self.chunk_size)
            .with_overlap(self.chunk_overlap)
            .unwrap()
            .with_trim(true);

        let splitter = TextSplitter::new(config);
        let mut chunks = Vec::new();
        let mut position = 0i32;

        for page in &doc.pages {
            if page.text.trim().is_empty() {
                continue;
            }

            let text_chunks = splitter.chunks(page.text.as_str());
            for chunk_text in text_chunks {
                let chunk_text = chunk_text.trim();
                if !chunk_text.is_empty() {
                    chunks.push(RawChunk {
                        text: chunk_text.to_string(),
                        position,
                        page: page.page_number,
                        token_count: estimate_tokens(chunk_text) as i32,
                    });
                    position += 1;
                }
            }
        }

        chunks
    }
}

pub fn estimate_tokens(text: &str) -> usize {
    (text.len() as f64 / 4.0).ceil() as usize
}
