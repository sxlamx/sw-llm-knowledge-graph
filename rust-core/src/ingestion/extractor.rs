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
