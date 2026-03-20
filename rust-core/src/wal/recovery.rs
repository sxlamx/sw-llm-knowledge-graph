//! WAL recovery.

use std::path::Path;

pub fn replay_wal(_path: &Path) -> Result<Vec<String>, std::io::Error> {
    Ok(Vec::new())
}

pub fn truncate_wal(_path: &Path) -> Result<(), std::io::Error> {
    Ok(())
}
