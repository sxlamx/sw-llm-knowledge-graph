//! Write-Ahead Log writer — append-only JSON log.
//!
//! Each line in the WAL file is a self-contained JSON object terminated by `\n`.
//! The WAL is used to replay graph updates after an unclean shutdown.
//!
//! Design:
//!   - `WalWriter::append(entry)` fsync-writes one JSON line.
//!   - `WalWriter::truncate()` replaces the file with an empty one after a
//!     successful checkpoint (called once at startup by `checkpoint_on_startup`).
//!   - The sequence counter counts committed entries; it is re-derived from the
//!     file length on restart.

use std::io::{BufWriter, Write};
use std::path::Path;

pub struct WalWriter {
    path: std::path::PathBuf,
    /// Monotonic entry count (not persisted — re-derived on open).
    pub sequence: u64,
}

impl WalWriter {
    /// Open (or create) the WAL at `path`.  Derives the initial sequence from
    /// the number of newline-terminated lines already in the file.
    pub fn new(path: &Path) -> Result<Self, std::io::Error> {
        // Ensure the file exists.
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;

        // Count existing entries by counting newlines.
        let content = std::fs::read_to_string(path).unwrap_or_default();
        let sequence = content.lines().filter(|l| !l.trim().is_empty()).count() as u64;

        Ok(Self {
            path: path.to_path_buf(),
            sequence,
        })
    }

    /// Append a JSON-encoded entry to the WAL, flushing to disk.
    ///
    /// `entry` must be a valid single-line JSON value (no embedded newlines).
    pub fn append(&mut self, entry: &str) -> std::io::Result<()> {
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        let mut writer = BufWriter::new(file);
        writer.write_all(entry.as_bytes())?;
        writer.write_all(b"\n")?;
        writer.flush()?;
        // fsync for durability guarantees
        writer.into_inner()?.sync_data()?;
        self.sequence += 1;
        Ok(())
    }

    /// Atomically truncate the WAL (replace with an empty file).
    /// Called after a successful checkpoint so the WAL does not grow unbounded.
    pub fn truncate(&mut self) -> std::io::Result<()> {
        std::fs::File::create(&self.path)?;
        self.sequence = 0;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    #[test]
    fn test_wal_writer_new_creates_file() {
        let tmp = NamedTempFile::new().unwrap();
        let writer = WalWriter::new(tmp.path()).unwrap();
        assert_eq!(writer.sequence, 0);
        assert!(tmp.path().exists());
    }

    #[test]
    fn test_wal_writer_append_increments_sequence() {
        let tmp = NamedTempFile::new().unwrap();
        let mut writer = WalWriter::new(tmp.path()).unwrap();

        writer.append(r#"{"type":"node","id":"1"}"#).unwrap();
        assert_eq!(writer.sequence, 1);

        writer.append(r#"{"type":"edge","id":"2"}"#).unwrap();
        assert_eq!(writer.sequence, 2);
    }

    #[test]
    fn test_wal_writer_append_writes_line() {
        let tmp = NamedTempFile::new().unwrap();
        let mut writer = WalWriter::new(tmp.path()).unwrap();

        writer.append(r#"{"type":"node","id":"1"}"#).unwrap();

        let content = std::fs::read_to_string(tmp.path()).unwrap();
        assert!(content.contains(r#"{"type":"node","id":"1"}"#));
        assert!(content.ends_with("\n"));
    }

    #[test]
    fn test_wal_writer_truncate_empties_file() {
        let tmp = NamedTempFile::new().unwrap();
        let mut writer = WalWriter::new(tmp.path()).unwrap();

        writer.append(r#"{"type":"node"}"#).unwrap();
        writer.append(r#"{"type":"edge"}"#).unwrap();
        assert!(writer.sequence > 0);

        writer.truncate().unwrap();

        let content = std::fs::read_to_string(tmp.path()).unwrap();
        assert!(content.is_empty(), "truncate should empty the file");
        assert_eq!(writer.sequence, 0);
    }

    #[test]
    fn test_wal_writer_truncate_allows_new_writes() {
        let tmp = NamedTempFile::new().unwrap();
        let mut writer = WalWriter::new(tmp.path()).unwrap();

        writer.append(r#"{"seq":1}"#).unwrap();
        writer.truncate().unwrap();
        writer.append(r#"{"seq":2}"#).unwrap();

        let content = std::fs::read_to_string(tmp.path()).unwrap();
        assert!(content.contains(r#"{"seq":2}"#));
        assert!(!content.contains("seq\":1"), "old entries should be gone after truncate");
    }

    #[test]
    fn test_wal_writer_derives_sequence_from_existing_lines() {
        let tmp = NamedTempFile::new().unwrap();
        std::fs::write(tmp.path(), "line1\nline2\nline3\n").unwrap();

        let writer = WalWriter::new(tmp.path()).unwrap();
        assert_eq!(writer.sequence, 3, "sequence should be derived from existing lines");
    }

    #[test]
    fn test_wal_writer_derives_sequence_from_empty_file() {
        let tmp = NamedTempFile::new().unwrap();
        std::fs::write(tmp.path(), "").unwrap();

        let writer = WalWriter::new(tmp.path()).unwrap();
        assert_eq!(writer.sequence, 0, "empty file should have sequence 0");
    }

    #[test]
    fn test_wal_writer_derives_sequence_from_file_with_blank_lines() {
        let tmp = NamedTempFile::new().unwrap();
        std::fs::write(tmp.path(), "line1\n\nline2\n   \nline3\n").unwrap();

        let writer = WalWriter::new(tmp.path()).unwrap();
        assert_eq!(writer.sequence, 3, "blank lines should not affect sequence");
    }

    #[test]
    fn test_wal_writer_flushes_to_disk() {
        let tmp = NamedTempFile::new().unwrap();
        let mut writer = WalWriter::new(tmp.path()).unwrap();

        writer.append(r#"{"flush":"test"}"#).unwrap();
        drop(writer);

        let content = std::fs::read_to_string(tmp.path()).unwrap();
        assert!(content.contains("flush"));
    }

    #[test]
    fn test_wal_writer_multiple_append_sequence_consistency() {
        let tmp = NamedTempFile::new().unwrap();
        let mut writer = WalWriter::new(tmp.path()).unwrap();

        for i in 0..100 {
            writer.append(&format!(r#"{{"id":{}}}"#, i)).unwrap();
            assert_eq!(writer.sequence, i as u64 + 1);
        }
    }
}