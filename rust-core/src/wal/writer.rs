//! Write-Ahead Log writer.

use std::path::Path;

pub struct WalWriter {
    path: std::path::PathBuf,
    sequence: u64,
}

impl WalWriter {
    pub fn new(path: &Path) -> Result<Self, std::io::Error> {
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)?;
        let file_len = file.metadata()?.len();
        let sequence = file_len / 256;
        Ok(Self {
            path: path.to_path_buf(),
            sequence,
        })
    }

    pub fn append(&mut self, _entry: &str) -> std::io::Result<()> {
        self.sequence += 1;
        Ok(())
    }

    pub fn truncate(&mut self) -> std::io::Result<()> {
        std::fs::File::create(&self.path)?;
        self.sequence = 0;
        Ok(())
    }
}
