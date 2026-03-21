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
        let sequence = content.lines().count() as u64;

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
        // Create (truncate) the file — atomic on POSIX via O_TRUNC.
        std::fs::File::create(&self.path)?;
        self.sequence = 0;
        Ok(())
    }
}
