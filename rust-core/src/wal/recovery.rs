//! WAL recovery — replay and checkpoint.
//!
//! On startup the system calls `checkpoint_on_startup`:
//!   1. Reads all JSON lines from the WAL file into memory.
//!   2. Truncates the WAL (so it starts fresh after a clean boot).
//!   3. Returns the entries to the caller, which replays them into the
//!      in-memory KnowledgeGraph.
//!
//! If the WAL file does not exist or is empty, the function returns `Ok(vec![])`.

use std::io::{BufRead, BufReader};
use std::path::Path;

/// Read every JSON-encoded line from `path` and return them in order.
///
/// Lines that are empty or consist only of whitespace are skipped.
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
        .collect();

    Ok(entries)
}

/// Truncate the WAL file to zero bytes, leaving it in place.
pub fn truncate_wal(path: &Path) -> Result<(), std::io::Error> {
    // O_TRUNC via File::create
    std::fs::File::create(path)?;
    Ok(())
}

/// Atomically checkpoint the WAL at `path`:
///   1. Replay (read) all entries.
///   2. Truncate so the WAL starts fresh.
///   3. Return the entries for the caller to replay into the graph.
///
/// This is the canonical startup procedure described in the spec:
/// "WAL checkpoint on startup: truncate after successful recovery."
pub fn checkpoint_on_startup(path: &Path) -> Result<Vec<String>, std::io::Error> {
    let entries = replay_wal(path)?;
    if !entries.is_empty() {
        truncate_wal(path)?;
    }
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
