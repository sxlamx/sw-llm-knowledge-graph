//! WAL module — write-ahead log for crash recovery.

pub mod writer;
pub mod recovery;

pub use writer::*;
pub use recovery::*;
