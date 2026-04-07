//! File discovery, BLAKE3 hashing, path sanitization.

use crate::errors::CoreError;
use blake3::Hasher;
use notify::{Event, RecommendedWatcher, RecursiveMode, Watcher};

use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

const BLOCKED_EXTENSIONS: &[&str] = &[
    "exe", "sh", "bat", "cmd", "ps1", "py", "rb", "pl", "key", "pem", "p12", "pfx", "env",
    "sqlite", "db",
];

#[allow(dead_code)]
const SUPPORTED_EXTENSIONS: &[&str] =
    &["pdf", "docx", "md", "markdown", "txt", "html", "htm", "rst"];

#[derive(Debug, Clone)]
pub struct FileEntry {
    pub path: PathBuf,
    pub file_type: FileType,
    pub size_bytes: u64,
    pub modified_at: std::time::SystemTime,
    pub blake3_hash: Option<[u8; 32]>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FileType {
    Pdf,
    Docx,
    Markdown,
    Text,
    Html,
    Rst,
    Unknown,
}

impl FileType {
    pub fn from_extension(ext: &str) -> Option<Self> {
        match ext.to_lowercase().as_str() {
            "pdf" => Some(FileType::Pdf),
            "docx" => Some(FileType::Docx),
            "md" | "markdown" => Some(FileType::Markdown),
            "txt" => Some(FileType::Text),
            "html" | "htm" => Some(FileType::Html),
            "rst" => Some(FileType::Rst),
            _ => None,
        }
    }
}

pub struct FileScanner {
    root_path: PathBuf,
    max_depth: usize,
    max_files: usize,
    allowed_roots: Vec<PathBuf>,
}

impl FileScanner {
    pub fn new(root_path: PathBuf, allowed_roots: Vec<PathBuf>) -> Self {
        Self {
            root_path,
            max_depth: 5,
            max_files: 10_000,
            allowed_roots,
        }
    }

    pub fn with_max_depth(mut self, depth: usize) -> Self {
        self.max_depth = depth;
        self
    }

    pub fn with_max_files(mut self, files: usize) -> Self {
        self.max_files = files;
        self
    }

    pub fn scan(&self) -> Result<Vec<FileEntry>, CoreError> {
        let mut entries = Vec::new();
        let walker = WalkDir::new(&self.root_path)
            .max_depth(self.max_depth)
            .follow_links(false);

        for entry in walker.into_iter().filter_map(|e| e.ok()) {
            if entries.len() >= self.max_files {
                break;
            }

            let path = entry.path();
            if !path.is_file() {
                continue;
            }

            if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                if BLOCKED_EXTENSIONS.contains(&ext.to_lowercase().as_str()) {
                    continue;
                }
                if let Some(file_type) = FileType::from_extension(ext) {
                    let metadata = entry.metadata()?;
                    entries.push(FileEntry {
                        path: path.to_path_buf(),
                        file_type,
                        size_bytes: metadata.len(),
                        modified_at: metadata.modified()?,
                        blake3_hash: None,
                    });
                }
            }
        }

        entries.sort_by(|a, b| b.modified_at.cmp(&a.modified_at));
        Ok(entries)
    }

    pub fn validate_path(&self, path: &Path) -> Result<(), CoreError> {
        let canonical = path
            .canonicalize()
            .map_err(|_| CoreError::InvalidPath(path.display().to_string()))?;

        let allowed = self.allowed_roots.iter().any(|root| {
            canonical.starts_with(root.canonicalize().unwrap_or_else(|_| root.clone()))
        });

        if !allowed {
            return Err(CoreError::PathTraversal {
                path: canonical.display().to_string(),
                allowed_root: self
                    .allowed_roots
                    .iter()
                    .map(|p| p.display().to_string())
                    .collect::<Vec<_>>()
                    .join(", "),
            });
        }

        if let Some(filename) = path.file_name().and_then(|n| n.to_str()) {
            if filename.starts_with('.') {
                return Err(CoreError::InvalidPath(format!(
                    "hidden file not allowed: {}",
                    filename
                )));
            }
        }

        if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
            if BLOCKED_EXTENSIONS.contains(&ext.to_lowercase().as_str()) {
                return Err(CoreError::InvalidPath(format!(
                    "blocked extension: {}",
                    ext
                )));
            }
        }

        Ok(())
    }
}

pub fn compute_blake3_hash(path: &Path) -> Result<[u8; 32], CoreError> {
    let mut file = File::open(path)?;
    let mut hasher = Hasher::new();
    let mut buf = [0u8; 65536];

    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }

    let digest = hasher.finalize();
    let mut hash = [0u8; 32];
    hash.copy_from_slice(digest.as_bytes());
    Ok(hash)
}

pub fn hash_matches(stored: Option<&str>, computed: &[u8; 32]) -> bool {
    match stored {
        Some(s) => {
            let stored_hash = blake3::Hash::from_bytes(*computed);
            stored_hash.to_hex().to_string() == s
        }
        None => false,
    }
}

pub fn start_file_watcher(
    path: &Path,
    tx: std::sync::mpsc::Sender<FileWatchEvent>,
) -> Result<RecommendedWatcher, CoreError> {
    let tx = std::sync::Mutex::new(tx);
    let mut watcher = notify::recommended_watcher(move |res: notify::Result<Event>| {
        if let Ok(event) = res {
            let _ = tx.lock().unwrap().send(FileWatchEvent::from(event));
        }
    })?;
    watcher.watch(path, RecursiveMode::Recursive)?;
    Ok(watcher)
}

#[derive(Debug, Clone)]
pub enum FileWatchEvent {
    Created(PathBuf),
    Modified(PathBuf),
    Removed(PathBuf),
}

impl From<notify::Event> for FileWatchEvent {
    fn from(event: notify::Event) -> Self {
        let path = event.paths.first().cloned().unwrap_or_default();
        match event.kind {
            notify::EventKind::Create(_) => FileWatchEvent::Created(path),
            notify::EventKind::Modify(_) => FileWatchEvent::Modified(path),
            notify::EventKind::Remove(_) => FileWatchEvent::Removed(path),
            _ => FileWatchEvent::Modified(path),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn make_scanner(tmp: &TempDir) -> FileScanner {
        FileScanner::new(tmp.path().to_path_buf(), vec![tmp.path().to_path_buf()])
    }

    #[test]
    fn test_validate_path_allows_file_within_root() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("document.pdf");
        std::fs::write(&file_path, b"test").unwrap();

        let scanner = make_scanner(&tmp);
        assert!(scanner.validate_path(&file_path).is_ok());
    }

    #[test]
    fn test_validate_path_blocks_traversal_outside_root() {
        let tmp = TempDir::new().unwrap();
        let scanner = make_scanner(&tmp);

        let traversal_path = tmp.path().join("..").join("..").join("etc").join("passwd");
        let result = scanner.validate_path(&traversal_path);
        assert!(result.is_err(), "path traversal should be blocked");
    }

    #[test]
    fn test_validate_path_blocks_exe_extension() {
        let tmp = TempDir::new().unwrap();
        let exe_path = tmp.path().join("tool.exe");
        std::fs::write(&exe_path, b"binary").unwrap();

        let scanner = make_scanner(&tmp);
        let result = scanner.validate_path(&exe_path);
        assert!(result.is_err(), ".exe files should be blocked");
    }

    #[test]
    fn test_validate_path_blocks_pem_extension() {
        let tmp = TempDir::new().unwrap();
        let pem_path = tmp.path().join("key.pem");
        std::fs::write(&pem_path, b"cert").unwrap();

        let scanner = make_scanner(&tmp);
        let result = scanner.validate_path(&pem_path);
        assert!(result.is_err(), ".pem files should be blocked");
    }

    #[test]
    fn test_validate_path_blocks_sh_extension() {
        let tmp = TempDir::new().unwrap();
        let sh_path = tmp.path().join("script.sh");
        std::fs::write(&sh_path, b"#!/bin/bash").unwrap();

        let scanner = make_scanner(&tmp);
        let result = scanner.validate_path(&sh_path);
        assert!(result.is_err(), ".sh files should be blocked");
    }

    #[test]
    fn test_validate_path_blocks_py_extension() {
        let tmp = TempDir::new().unwrap();
        let py_path = tmp.path().join("script.py");
        std::fs::write(&py_path, b"print('hello')").unwrap();

        let scanner = make_scanner(&tmp);
        let result = scanner.validate_path(&py_path);
        assert!(result.is_err(), ".py files should be blocked");
    }

    #[test]
    fn test_validate_path_allows_pdf() {
        let tmp = TempDir::new().unwrap();
        let pdf_path = tmp.path().join("document.pdf");
        std::fs::write(&pdf_path, b"%PDF-1.4 test").unwrap();

        let scanner = make_scanner(&tmp);
        assert!(scanner.validate_path(&pdf_path).is_ok());
    }

    #[test]
    fn test_validate_path_allows_docx() {
        let tmp = TempDir::new().unwrap();
        let docx_path = tmp.path().join("document.docx");
        std::fs::write(&docx_path, b"PK\x03\x04").unwrap();

        let scanner = make_scanner(&tmp);
        assert!(scanner.validate_path(&docx_path).is_ok());
    }

    #[test]
    fn test_validate_path_blocks_sqlite_db() {
        let tmp = TempDir::new().unwrap();
        let db_path = tmp.path().join("data.sqlite");
        std::fs::write(&db_path, b"SQLite format 3").unwrap();

        let scanner = make_scanner(&tmp);
        let result = scanner.validate_path(&db_path);
        assert!(result.is_err(), ".sqlite files should be blocked");
    }

    #[test]
    fn test_validate_path_blocks_env_file() {
        let tmp = TempDir::new().unwrap();
        let env_path = tmp.path().join(".env");
        std::fs::write(&env_path, b"API_KEY=secret").unwrap();

        let scanner = make_scanner(&tmp);
        let result = scanner.validate_path(&env_path);
        assert!(result.is_err(), ".env files should be blocked");
    }

    #[test]
    fn test_compute_blake3_hash_computes_deterministic_hash() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.txt");
        std::fs::write(&file_path, b"Hello world").unwrap();

        let hash1 = compute_blake3_hash(&file_path).unwrap();
        let hash2 = compute_blake3_hash(&file_path).unwrap();

        assert_eq!(hash1, hash2, "hash should be deterministic");
        assert_eq!(hash1.len(), 32, "BLAKE3 produces 32-byte hash");
    }

    #[test]
    fn test_compute_blake3_hash_different_content_different_hash() {
        let tmp = TempDir::new().unwrap();

        let file1 = tmp.path().join("a.txt");
        let file2 = tmp.path().join("b.txt");
        std::fs::write(&file1, b"content A").unwrap();
        std::fs::write(&file2, b"content B").unwrap();

        let hash1 = compute_blake3_hash(&file1).unwrap();
        let hash2 = compute_blake3_hash(&file2).unwrap();

        assert_ne!(hash1, hash2, "different content should produce different hash");
    }

    #[test]
    fn test_hash_matches_with_matching_stored_hash() {
        let tmp = TempDir::new().unwrap();
        let file_path = tmp.path().join("test.txt");
        std::fs::write(&file_path, b"Hello world").unwrap();

        let hash = compute_blake3_hash(&file_path).unwrap();
        let hash_hex = blake3::Hash::from_bytes(hash).to_hex().to_string();

        assert!(hash_matches(Some(&hash_hex), &hash));
    }

    #[test]
    fn test_hash_matches_with_none_stored_returns_false() {
        let hash = [0u8; 32];
        assert!(!hash_matches(None, &hash));
    }

    #[test]
    fn test_hash_matches_with_mismatched_hash() {
        let stored = "0000000000000000000000000000000000000000000000000000000000000000";
        let computed = [1u8; 32];
        assert!(!hash_matches(Some(stored), &computed));
    }

    #[test]
    fn test_scan_finds_supported_files_only() {
        let tmp = TempDir::new().unwrap();

        std::fs::write(tmp.path().join("doc.pdf"), b"pdf").unwrap();
        std::fs::write(tmp.path().join("doc.md"), b"markdown").unwrap();
        std::fs::write(tmp.path().join("readme.txt"), b"text").unwrap();
        std::fs::write(tmp.path().join("script.py"), b"python").unwrap();
        std::fs::write(tmp.path().join("data.db"), b"database").unwrap();

        let scanner = FileScanner::new(
            tmp.path().to_path_buf(),
            vec![tmp.path().to_path_buf()],
        );
        let entries = scanner.scan().unwrap();

        assert_eq!(entries.len(), 3);
        assert!(entries.iter().all(|e| e.file_type != FileType::Unknown));
        assert!(entries.iter().all(|e| {
            matches!(e.file_type, FileType::Pdf | FileType::Markdown | FileType::Text)
        }));
    }

    #[test]
    fn test_scan_respects_max_files_limit() {
        let tmp = TempDir::new().unwrap();

        for i in 0..20 {
            std::fs::write(tmp.path().join(format!("doc_{}.txt", i)), b"content").unwrap();
        }

        let scanner = FileScanner::new(
            tmp.path().to_path_buf(),
            vec![tmp.path().to_path_buf()],
        )
        .with_max_files(5);

        let entries = scanner.scan().unwrap();
        assert_eq!(entries.len(), 5, "should respect max_files limit");
    }

    #[test]
    fn test_scan_respects_max_depth() {
        let tmp = TempDir::new().unwrap();

        std::fs::create_dir_all(tmp.path().join("level1").join("level2").join("level3")).unwrap();
        std::fs::write(tmp.path().join("root.txt"), b"root").unwrap();
        std::fs::write(tmp.path().join("level1").join("l1.txt"), b"level1").unwrap();
        std::fs::write(tmp.path().join("level1").join("level2").join("l2.txt"), b"level2").unwrap();
        std::fs::write(
            tmp.path().join("level1").join("level2").join("level3").join("l3.txt"),
            b"level3",
        )
        .unwrap();

        let scanner_depth2 = FileScanner::new(
            tmp.path().to_path_buf(),
            vec![tmp.path().to_path_buf()],
        )
        .with_max_depth(2);

        let entries = scanner_depth2.scan().unwrap();
        assert!(entries.iter().any(|e| e.path.file_name().unwrap() == "root.txt"));
        assert!(entries.iter().any(|e| e.path.file_name().unwrap() == "l1.txt"));
        assert!(!entries.iter().any(|e| e.path.file_name().unwrap() == "l2.txt"));
        assert!(!entries.iter().any(|e| e.path.file_name().unwrap() == "l3.txt"));
    }

    #[test]
    fn test_scan_sorts_by_modified_at_descending() {
        let tmp = TempDir::new().unwrap();

        std::fs::write(tmp.path().join("old.txt"), b"old").unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        std::fs::write(tmp.path().join("new.txt"), b"new").unwrap();

        let scanner = FileScanner::new(
            tmp.path().to_path_buf(),
            vec![tmp.path().to_path_buf()],
        );
        let entries = scanner.scan().unwrap();

        assert_eq!(entries.len(), 2);
        assert_eq!(
            entries[0].path.file_name().unwrap().to_str().unwrap(),
            "new.txt",
            "newest file should be first"
        );
    }

    #[test]
    fn test_file_type_from_extension() {
        assert_eq!(FileType::from_extension("pdf"), Some(FileType::Pdf));
        assert_eq!(FileType::from_extension("PDF"), Some(FileType::Pdf));
        assert_eq!(FileType::from_extension("docx"), Some(FileType::Docx));
        assert_eq!(FileType::from_extension("md"), Some(FileType::Markdown));
        assert_eq!(FileType::from_extension("markdown"), Some(FileType::Markdown));
        assert_eq!(FileType::from_extension("txt"), Some(FileType::Text));
        assert_eq!(FileType::from_extension("html"), Some(FileType::Html));
        assert_eq!(FileType::from_extension("htm"), Some(FileType::Html));
        assert_eq!(FileType::from_extension("rst"), Some(FileType::Rst));
        assert_eq!(FileType::from_extension("unknown"), None);
        assert_eq!(FileType::from_extension("exe"), None);
    }

    #[test]
    fn test_file_watch_event_from_notify_created() {
        use notify::EventKind;
        let path = std::path::PathBuf::from("/tmp/test.txt");
        let event = notify::Event::new(EventKind::Create(notify::event::CreateKind::File))
            .add_path(path.clone());
        let watch_event = FileWatchEvent::from(event);
        assert!(matches!(watch_event, FileWatchEvent::Created(p) if p == path));
    }

    #[test]
    fn test_file_watch_event_from_notify_removed() {
        use notify::EventKind;
        let path = std::path::PathBuf::from("/tmp/test.txt");
        let event = notify::Event::new(EventKind::Remove(notify::event::RemoveKind::File))
            .add_path(path.clone());
        let watch_event = FileWatchEvent::from(event);
        assert!(matches!(watch_event, FileWatchEvent::Removed(p) if p == path));
    }
}
