//! WAL recovery — replay and checkpoint.
//!
//! On startup the system calls `checkpoint_on_startup`:
//!   1. Reads all JSON lines from the WAL file into memory.
//!   2. Truncates the WAL (so it starts fresh after a clean boot).
//!   3. Returns the entries to the caller, which replays them into the
//!      in-memory KnowledgeGraph.
//!
//! If the WAL file does not exist or is empty, the function returns `Ok(vec![])`.
//!
//! Each WAL entry is expected to contain `sequence` (u64) and `timestamp` (u64)
//! fields injected by `WalWriter::append`. The recovery functions parse these
//! fields to return `WalEntry` structs with proper ordering information.

use std::io::{BufRead, BufReader};
use std::path::Path;

/// A single WAL entry with sequence number and timestamp.
#[derive(Debug, Clone)]
pub struct WalEntry {
    pub sequence: u64,
    pub timestamp: u64,
    pub operation: serde_json::Value,
}

/// Read every JSON-encoded line from `path`, parse each into a `WalEntry`
/// containing `sequence`, `timestamp`, and the full operation payload.
///
/// Lines that are empty, consist only of whitespace, or fail JSON parse are
/// skipped.
/// Does NOT truncate — the caller must call `truncate_wal` after successful
/// replay to complete the checkpoint.
pub fn read_wal_for_recovery(path: &Path) -> Result<Vec<WalEntry>, std::io::Error> {
    if !path.exists() {
        return Ok(Vec::new());
    }

    let file = std::fs::File::open(path)?;
    let reader = BufReader::new(file);

    let mut entries = Vec::new();
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        let trimmed = line.trim().to_string();
        if trimmed.is_empty() {
            continue;
        }
        let value: serde_json::Value = match serde_json::from_str(&trimmed) {
            Ok(v) => v,
            Err(_) => continue,
        };

        let sequence = value.get("sequence")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);
        let timestamp = value.get("timestamp")
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        entries.push(WalEntry {
            sequence,
            timestamp,
            operation: value,
        });
    }

    entries.sort_by_key(|e| e.sequence);
    Ok(entries)
}

/// Truncate the WAL file to zero bytes, leaving it in place.
pub fn truncate_wal(path: &Path) -> Result<(), std::io::Error> {
    let tmp_path = path.with_extension("wal.tmp");
    {
        let f = std::fs::File::create(&tmp_path)?;
        f.sync_all()?;
    }
    std::fs::rename(&tmp_path, path)?;
    Ok(())
}

/// Legacy API retained for backwards compatibility.
///
/// Prefer `read_wal_for_recovery` + explicit `truncate_wal` after
/// successful replay so that a crash during replay doesn't lose WAL
/// entries.
pub fn checkpoint_on_startup(path: &Path) -> Result<Vec<String>, std::io::Error> {
    let entries = replay_wal(path)?;
    if !entries.is_empty() {
        truncate_wal(path)?;
    }
    Ok(entries)
}

pub fn replay_wal(path: &Path) -> Result<Vec<String>, std::io::Error> {
    if !path.exists() {
        return Ok(Vec::new());
    }

    let file = std::fs::File::open(path)?;
    let reader = BufReader::new(file);

    let entries: Vec<String> = reader
        .lines()
        .filter_map(|line| {
            line.ok().and_then(|l| {
                let trimmed = l.trim().to_string();
                if trimmed.is_empty() { None } else { Some(trimmed) }
            })
        })
        .filter(|line| serde_json::from_str::<serde_json::Value>(line).is_ok())
        .collect();

    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_replay_empty_file() {
        let tmp = NamedTempFile::new().unwrap();
        let entries = replay_wal(tmp.path()).unwrap();
        assert!(entries.is_empty());
    }

    #[test]
    fn test_replay_reads_all_lines() {
        let mut tmp = NamedTempFile::new().unwrap();
        writeln!(tmp, r#"{{"type":"node","id":"1"}}"#).unwrap();
        writeln!(tmp, r#"{{"type":"edge","id":"2"}}"#).unwrap();

        let entries = replay_wal(tmp.path()).unwrap();
        assert_eq!(entries.len(), 2);
        assert!(entries[0].contains("node"));
        assert!(entries[1].contains("edge"));
    }

    #[test]
    fn test_replay_skips_blank_lines() {
        let mut tmp = NamedTempFile::new().unwrap();
        writeln!(tmp, r#"{{"type":"node"}}"#).unwrap();
        writeln!(tmp, "").unwrap();
        writeln!(tmp, "   ").unwrap();
        writeln!(tmp, r#"{{"type":"edge"}}"#).unwrap();

        let entries = replay_wal(tmp.path()).unwrap();
        assert_eq!(entries.len(), 2);
    }

    #[test]
    fn test_truncate_wal_empties_file() {
        let mut tmp = NamedTempFile::new().unwrap();
        writeln!(tmp, r#"{{"type":"node"}}"#).unwrap();

        truncate_wal(tmp.path()).unwrap();

        let content = std::fs::read_to_string(tmp.path()).unwrap();
        assert!(content.is_empty());
    }

    #[test]
    fn test_checkpoint_on_startup_replays_then_truncates() {
        let mut tmp = NamedTempFile::new().unwrap();
        writeln!(tmp, r#"{{"seq":1}}"#).unwrap();
        writeln!(tmp, r#"{{"seq":2}}"#).unwrap();

        let entries = checkpoint_on_startup(tmp.path()).unwrap();
        assert_eq!(entries.len(), 2);

        // WAL should be empty after checkpoint
        let remaining = replay_wal(tmp.path()).unwrap();
        assert!(remaining.is_empty());
    }

    #[test]
    fn test_checkpoint_on_empty_wal_does_not_truncate() {
        let tmp = NamedTempFile::new().unwrap();
        let entries = checkpoint_on_startup(tmp.path()).unwrap();
        assert!(entries.is_empty());
        // File should still exist (not errored)
        assert!(tmp.path().exists());
    }

    #[test]
    fn test_nonexistent_wal_returns_empty() {
        let path = std::path::PathBuf::from("/tmp/nonexistent_wal_12345.log");
        let entries = replay_wal(&path).unwrap();
        assert!(entries.is_empty());
    }
}
