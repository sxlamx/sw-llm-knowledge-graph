//! Text extraction from various file formats.

use crate::errors::CoreError;
use crate::ingestion::scanner::{FileEntry, FileType};
use std::collections::HashMap;
use std::path::Path;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ExtractedDocument {
    pub title: Option<String>,
    pub raw_text: String,
    pub pages: Vec<PageContent>,
    pub metadata: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PageContent {
    pub page_number: i32,
    pub text: String,
}

pub struct DocumentExtractor;

impl DocumentExtractor {
    pub async fn extract(&self, entry: &FileEntry) -> Result<ExtractedDocument, CoreError> {
        match entry.file_type {
            FileType::Pdf => self.extract_pdf(&entry.path).await,
            FileType::Docx => self.extract_docx(&entry.path).await,
            FileType::Markdown => self.extract_markdown(&entry.path).await,
            FileType::Text => self.extract_text(&entry.path).await,
            FileType::Html => self.extract_html(&entry.path).await,
            FileType::Rst => self.extract_text(&entry.path).await,
            FileType::Unknown => Err(CoreError::UnsupportedFileType(
                entry.path.display().to_string(),
            )),
        }
    }

    async fn extract_pdf(&self, path: &Path) -> Result<ExtractedDocument, CoreError> {
        let doc = lopdf::Document::load(path)
            .map_err(|e| CoreError::IoError(format!("PDF load error: {}", e)))?;

        let pages = doc.get_pages();
        let mut page_contents = Vec::new();

        for (page_num, _page_id) in pages {
            let text = doc.extract_text(&[page_num])
                .unwrap_or_default();
            page_contents.push(PageContent {
                page_number: page_num as i32,
                text: text.trim().to_string(),
            });
        }

        let title = extract_pdf_title(&doc);
        let metadata = extract_pdf_metadata(&doc);

        let raw_text = page_contents.iter()
            .map(|p| p.text.as_str())
            .collect::<Vec<_>>()
            .join("\n\n");

        Ok(ExtractedDocument {
            title,
            raw_text,
            pages: page_contents,
            metadata,
        })
    }

    async fn extract_docx(&self, path: &Path) -> Result<ExtractedDocument, CoreError> {
        let bytes = tokio::fs::read(path).await?;
        let docx = docx_rs::read_docx(&bytes)
            .map_err(|e| CoreError::IoError(format!("DOCX error: {}", e)))?;

        let mut paragraphs = Vec::new();
        let title = None;

        for child in docx.document.children {
            match child {
                docx_rs::DocumentChild::Paragraph(p) => {
                    let text = extract_paragraph_text(&p);
                    if !text.trim().is_empty() {
                        paragraphs.push(text);
                    }
                }
                _ => {}
            }
        }

        Ok(ExtractedDocument {
            title,
            raw_text: paragraphs.join("\n\n"),
            pages: vec![PageContent {
                page_number: 1,
                text: paragraphs.join("\n\n"),
            }],
            metadata: HashMap::new(),
        })
    }

    async fn extract_markdown(&self, path: &Path) -> Result<ExtractedDocument, CoreError> {
        let content = tokio::fs::read_to_string(path).await?;

        let title = path.file_stem()
            .and_then(|s| s.to_str())
            .map(|s| s.to_string());

        let mut pages = Vec::new();
        let mut current_page = Vec::new();
        let mut page_num = 1;

        for line in content.lines() {
            current_page.push(line);
            if current_page.len() > 100 {
                pages.push(PageContent {
                    page_number: page_num,
                    text: current_page.join("\n"),
                });
                page_num += 1;
                current_page = Vec::new();
            }
        }

        if !current_page.is_empty() {
            pages.push(PageContent {
                page_number: page_num,
                text: current_page.join("\n"),
            });
        }

        if pages.is_empty() {
            pages.push(PageContent { page_number: 1, text: content.clone() });
        }

        Ok(ExtractedDocument {
            title,
            raw_text: content,
            pages,
            metadata: HashMap::new(),
        })
    }

    async fn extract_text(&self, path: &Path) -> Result<ExtractedDocument, CoreError> {
        let content = tokio::fs::read_to_string(path).await?;

        let title = path.file_stem()
            .and_then(|s| s.to_str())
            .map(|s| s.to_string());

        Ok(ExtractedDocument {
            title,
            raw_text: content.clone(),
            pages: vec![PageContent {
                page_number: 1,
                text: content,
            }],
            metadata: HashMap::new(),
        })
    }

    async fn extract_html(&self, path: &Path) -> Result<ExtractedDocument, CoreError> {
        let content = tokio::fs::read_to_string(path).await?;

        let document = scraper::Html::parse_document(&content);
        let title_selector = scraper::Selector::parse("title").unwrap();
        let body_selector = scraper::Selector::parse("body").unwrap();

        let title = document.select(&title_selector)
            .next()
            .map(|el| el.text().collect::<String>());

        let body_text = document.select(&body_selector)
            .next()
            .map(|el| el.text().collect::<String>())
            .unwrap_or_default();

        Ok(ExtractedDocument {
            title,
            raw_text: body_text.clone(),
            pages: vec![PageContent {
                page_number: 1,
                text: body_text,
            }],
            metadata: HashMap::new(),
        })
    }
}

fn extract_pdf_title(doc: &lopdf::Document) -> Option<String> {
    doc.trailer.get(b"Info")
        .ok()
        .and_then(|info| {
            let info_dict = info.as_reference().ok()?;
            doc.get_object(info_dict).ok()
        })
        .and_then(|obj| {
            if let lopdf::Object::Dictionary(ref dict) = obj {
                dict.get(b"Title").ok().and_then(|t| {
                    if let lopdf::Object::String(ref s, _) = t {
                        Some(String::from_utf8_lossy(s).to_string())
                    } else {
                        None
                    }
                })
            } else {
                None
            }
        })
}

fn extract_pdf_metadata(doc: &lopdf::Document) -> HashMap<String, serde_json::Value> {
    let mut metadata = HashMap::new();

    if let Ok(info_ref) = doc.trailer.get(b"Info") {
        if let Ok(info_id) = info_ref.as_reference() {
            if let Ok(obj) = doc.get_object(info_id) {
                if let lopdf::Object::Dictionary(dict) = obj {
                    for (key, val) in dict.iter() {
                        let key_str = String::from_utf8_lossy(key).to_string();
                        if let Ok(json_val) = serde_json::to_value(format!("{:?}", val)) {
                            metadata.insert(key_str, json_val);
                        }
                    }
                }
            }
        }
    }

    metadata
}

fn extract_paragraph_text(paragraph: &docx_rs::Paragraph) -> String {
    let mut text = String::new();
    for child in &paragraph.children {
        match child {
            docx_rs::ParagraphChild::Run(run) => {
                for child in &run.children {
                    if let docx_rs::RunChild::Text(t) = child {
                        text.push_str(&t.text);
                    }
                }
                text.push(' ');
            }
            _ => {}
        }
    }
    text.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[tokio::test]
    async fn test_extract_text_returns_content() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.txt");
        std::fs::write(&file_path, "Hello world\nTest content").unwrap();

        let entry = FileEntry {
            path: file_path,
            file_type: FileType::Text,
            size_bytes: 17,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = extractor.extract(&entry).await.unwrap();

        assert_eq!(result.raw_text, "Hello world\nTest content");
        assert_eq!(result.pages.len(), 1);
        assert_eq!(result.pages[0].page_number, 1);
        assert_eq!(result.pages[0].text, "Hello world\nTest content");
    }

    #[tokio::test]
    async fn test_extract_text_uses_filename_as_title() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("my_document.txt");
        std::fs::write(&file_path, "content").unwrap();

        let entry = FileEntry {
            path: file_path,
            file_type: FileType::Text,
            size_bytes: 7,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = extractor.extract(&entry).await.unwrap();

        assert_eq!(result.title, Some("my_document".to_string()));
    }

    #[tokio::test]
    async fn test_extract_markdown_splits_into_pages() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.md");
        let lines: Vec<String> = (0..250).map(|i| format!("Line {}", i)).collect();
        std::fs::write(&file_path, lines.join("\n")).unwrap();

        let entry = FileEntry {
            path: file_path,
            file_type: FileType::Markdown,
            size_bytes: 1000,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = extractor.extract(&entry).await.unwrap();

        assert!(result.pages.len() >= 2, "long markdown should produce multiple pages");
        assert!(result.title.is_some());
    }

    #[tokio::test]
    async fn test_extract_html_extracts_body_text() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.html");
        let html = r#"<!DOCTYPE html>
<html>
<head><title>Test Title</title></head>
<body><p>Hello from HTML</p></body>
</html>"#;
        std::fs::write(&file_path, html).unwrap();

        let entry = FileEntry {
            path: file_path,
            file_type: FileType::Html,
            size_bytes: html.len() as u64,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = extractor.extract(&entry).await.unwrap();

        assert!(result.title.is_some());
        assert!(result.raw_text.contains("Hello from HTML"));
    }

    #[tokio::test]
    async fn test_extract_unsupported_file_type_returns_error() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.unknown");
        std::fs::write(&file_path, "content").unwrap();

        let entry = FileEntry {
            path: file_path,
            file_type: FileType::Unknown,
            size_bytes: 7,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = extractor.extract(&entry).await;

        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_page_content_has_correct_structure() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.txt");
        std::fs::write(&file_path, "Single page content").unwrap();

        let entry = FileEntry {
            path: file_path,
            file_type: FileType::Text,
            size_bytes: 18,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = extractor.extract(&entry).await.unwrap();

        assert_eq!(result.pages.len(), 1);
        let page = &result.pages[0];
        assert_eq!(page.page_number, 1);
        assert_eq!(page.text, "Single page content");
    }

    #[tokio::test]
    async fn test_extracted_document_metadata_is_empty_for_text() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.txt");
        std::fs::write(&file_path, "content").unwrap();

        let entry = FileEntry {
            path: file_path,
            file_type: FileType::Text,
            size_bytes: 7,
            modified_at: std::time::SystemTime::now(),
            blake3_hash: None,
        };

        let extractor = DocumentExtractor;
        let result = extractor.extract(&entry).await.unwrap();

        assert!(result.metadata.is_empty());
    }
}
