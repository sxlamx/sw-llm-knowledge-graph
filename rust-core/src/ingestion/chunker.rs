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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ingestion::extractor::{ExtractedDocument, PageContent};

    fn make_doc(text: &str, pages: Vec<PageContent>) -> ExtractedDocument {
        ExtractedDocument {
            title: Some("Test Document".to_string()),
            raw_text: text.to_string(),
            pages,
            metadata: std::collections::HashMap::new(),
        }
    }

    #[test]
    fn test_chunker_produces_expected_count() {
        let chunker = Chunker::new(500, 50);
        let text = "word ".repeat(200);  // 1000 chars total
        let doc = make_doc(&text, vec![PageContent { page_number: 1, text: text.clone() }]);
        let chunks = chunker.chunk_document(&doc);
        assert!(
            chunks.len() >= 2 && chunks.len() <= 3,
            "1000 chars with 500 char chunks should produce 2-3 chunks, got {}",
            chunks.len()
        );
    }

    #[test]
    fn test_chunker_respects_chunk_size() {
        let chunker = Chunker::new(100, 20);
        let text = "word ".repeat(500);
        let doc = make_doc(&text, vec![PageContent { page_number: 1, text: text.clone() }]);
        let chunks = chunker.chunk_document(&doc);
        for chunk in &chunks {
            let estimated = estimate_tokens(&chunk.text);
            assert!(
                estimated <= 130,
                "chunk token count {} should be within chunk_size + tolerance",
                estimated
            );
        }
    }

    #[test]
    fn test_chunker_increments_position() {
        let chunker = Chunker::new(50, 10);
        let text = "word ".repeat(200);
        let doc = make_doc(&text, vec![PageContent { page_number: 1, text: text.clone() }]);
        let chunks = chunker.chunk_document(&doc);
        for (i, chunk) in chunks.iter().enumerate() {
            assert_eq!(
                chunk.position, i as i32,
                "position should increment sequentially"
            );
        }
    }

    #[test]
    fn test_chunker_preserves_page_numbers() {
        let chunker = Chunker::new(50, 10);
        let doc = make_doc(
            "some text",
            vec![
                PageContent { page_number: 1, text: "page 1 content".to_string() },
                PageContent { page_number: 3, text: "page 3 content".to_string() },
            ],
        );
        let chunks = chunker.chunk_document(&doc);
        assert!(chunks.iter().all(|c| c.page == 1 || c.page == 3));
    }

    #[test]
    fn test_chunker_handles_empty_document() {
        let chunker = Chunker::new(512, 50);
        let doc = ExtractedDocument {
            title: None,
            raw_text: String::new(),
            pages: vec![],
            metadata: std::collections::HashMap::new(),
        };
        let chunks = chunker.chunk_document(&doc);
        assert!(chunks.is_empty(), "empty document should produce no chunks");
    }

    #[test]
    fn test_chunker_handles_single_page() {
        let chunker = Chunker::new(512, 50);
        let doc = make_doc("short text", vec![PageContent { page_number: 1, text: "short text".to_string() }]);
        let chunks = chunker.chunk_document(&doc);
        assert!(!chunks.is_empty(), "single page document should produce at least one chunk");
    }

    #[test]
    fn test_chunker_trims_whitespace() {
        let chunker = Chunker::new(512, 50);
        let doc = make_doc(
            "   lots of whitespace   ",
            vec![PageContent { page_number: 1, text: "   lots of whitespace   ".to_string() }],
        );
        let chunks = chunker.chunk_document(&doc);
        for chunk in &chunks {
            assert_eq!(
                chunk.text.trim(), chunk.text,
                "chunk text should be trimmed"
            );
        }
    }

    #[test]
    fn test_estimate_tokens_scaling() {
        assert_eq!(estimate_tokens("abcd"), 1);
        assert_eq!(estimate_tokens("abcdefgh"), 2);
        assert_eq!(estimate_tokens("a"), 1);
        assert_eq!(estimate_tokens(""), 0);
        assert_eq!(estimate_tokens("aaaa bbbb cccc"), 4);  // 15 chars / 4 = 3.75 -> ceil = 4
    }

    #[test]
    fn test_chunker_small_text_produces_single_chunk() {
        let chunker = Chunker::new(512, 50);
        let doc = make_doc("hello world", vec![PageContent { page_number: 1, text: "hello world".to_string() }]);
        let chunks = chunker.chunk_document(&doc);
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].text, "hello world");
    }

    #[test]
    fn test_chunker_multiple_pages_all_processed() {
        let chunker = Chunker::new(50, 10);
        let doc = make_doc(
            "combined",
            vec![
                PageContent { page_number: 1, text: "page1".to_string() },
                PageContent { page_number: 2, text: "page2".to_string() },
                PageContent { page_number: 3, text: "page3".to_string() },
            ],
        );
        let chunks = chunker.chunk_document(&doc);
        assert!(
            chunks.iter().any(|c| c.page == 1),
            "should have chunks from page 1"
        );
        assert!(
            chunks.iter().any(|c| c.page == 2),
            "should have chunks from page 2"
        );
        assert!(
            chunks.iter().any(|c| c.page == 3),
            "should have chunks from page 3"
        );
    }
}
