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
