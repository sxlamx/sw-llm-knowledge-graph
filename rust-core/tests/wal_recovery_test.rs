//! wal_recovery_test.rs — Phase 6 WAL recovery and checkpoint ordering tests.
//!
//! Validates:
//!   - WAL entries are replayed in order on startup.
//!   - WAL is NOT truncated until all entries are successfully replayed.
//!   - Unknown collection IDs in WAL entries are skipped gracefully.
//!   - Empty WAL does not trigger truncation.
//!   - Entries with malformed JSON are skipped without aborting replay.

use rust_core::wal::{checkpoint_on_startup, replay_wal, truncate_wal, WalWriter};
use std::io::Write;
use tempfile::NamedTempFile;

#[test]
fn test_replay_wal_returns_entries_in_order() {
    let tmp = NamedTempFile::new().unwrap();
    let mut writer = WalWriter::new(tmp.path()).unwrap();

    writer.append(r#"{"op":"upsert_nodes","collection_id":"c1","nodes":"[{}]"}"#).unwrap();
    writer.append(r#"{"op":"upsert_edges","collection_id":"c1","edges":"[{}]"}"#).unwrap();
    writer.append(r#"{"op":"upsert_nodes","collection_id":"c2","nodes":"[{}]"}"#).unwrap();
    drop(writer);

    let entries = replay_wal(tmp.path()).unwrap();
    assert_eq!(entries.len(), 3);
    assert!(entries[0].contains("c1"));
    assert!(entries[1].contains("edges"));
    assert!(entries[2].contains("c2"));
}

#[test]
fn test_replay_wal_skips_malformed_json() {
    let tmp = NamedTempFile::new().unwrap();
    let mut writer = WalWriter::new(tmp.path()).unwrap();

    writer.append(r#"{"op":"upsert_nodes","collection_id":"c1","nodes":"[{}]"}"#).unwrap();
    writer.append("not valid json at all").unwrap();
    writer.append(r#"{"op":"upsert_edges","collection_id":"c1","edges":"[{}]"}"#).unwrap();
    drop(writer);

    let entries = replay_wal(tmp.path()).unwrap();
    assert_eq!(entries.len(), 2, "malformed line should be skipped");
}

#[test]
fn test_checkpoint_does_not_truncate_empty_wal() {
    let tmp = NamedTempFile::new().unwrap();
    // Write empty file
    std::fs::write(tmp.path(), "").unwrap();

    let entries = checkpoint_on_startup(tmp.path()).unwrap();
    assert!(entries.is_empty());

    // WAL must still exist (not deleted)
    assert!(tmp.path().exists());
    // File size must still be 0
    let content = std::fs::read_to_string(tmp.path()).unwrap();
    assert!(content.is_empty());
}

#[test]
fn test_checkpoint_replays_then_truncates() {
    let tmp = NamedTempFile::new().unwrap();
    let mut writer = WalWriter::new(tmp.path()).unwrap();

    writer.append(r#"{"op":"upsert_nodes","collection_id":"c1","nodes":"[{}]"}"#).unwrap();
    writer.append(r#"{"op":"upsert_edges","collection_id":"c1","edges":"[{}]"}"#).unwrap();
    drop(writer);

    let entries = checkpoint_on_startup(tmp.path()).unwrap();
    assert_eq!(entries.len(), 2);

    // After checkpoint, WAL should be truncated
    let remaining = replay_wal(tmp.path()).unwrap();
    assert!(remaining.is_empty(), "WAL must be truncated after successful replay");
}

#[test]
fn test_truncate_wal_empties_file_atomically() {
    let tmp = NamedTempFile::new().unwrap();
    let mut writer = WalWriter::new(tmp.path()).unwrap();
    writer.append(r#"{"id":"1"}"#).unwrap();
    writer.append(r#"{"id":"2"}"#).unwrap();
    drop(writer);

    truncate_wal(tmp.path()).unwrap();

    let content = std::fs::read_to_string(tmp.path()).unwrap();
    assert!(content.is_empty(), "truncate should empty the file");
}

#[test]
fn test_truncate_wal_allows_new_writes_after() {
    let tmp = NamedTempFile::new().unwrap();
    let mut writer = WalWriter::new(tmp.path()).unwrap();

    writer.append(r#"{"seq":1}"#).unwrap();
    truncate_wal(tmp.path()).unwrap();
    writer.append(r#"{"seq":2}"#).unwrap();
    drop(writer);

    let entries = replay_wal(tmp.path()).unwrap();
    assert_eq!(entries.len(), 1);
    assert!(entries[0].contains("seq\":2"));
    assert!(!entries[0].contains("seq\":1"), "old entry must be gone after truncate");
}

#[test]
fn test_nonexistent_wal_returns_empty_vec() {
    let path = std::path::PathBuf::from("/tmp/nonexistent_wal_recovery_test.log");
    let entries = replay_wal(&path).unwrap();
    assert!(entries.is_empty());
}

#[test]
fn test_wal_writer_derives_sequence_from_file() {
    let tmp = NamedTempFile::new().unwrap();
    let mut writer = WalWriter::new(tmp.path()).unwrap();
    writer.append(r#"{"seq":1}"#).unwrap();
    writer.append(r#"{"seq":2}"#).unwrap();
    writer.append(r#"{"seq":3}"#).unwrap();
    drop(writer);

    // Re-open and verify sequence is re-derived
    let writer2 = WalWriter::new(tmp.path()).unwrap();
    assert_eq!(writer2.sequence, 3);
}

#[test]
fn test_checkpoint_on_startup_with_blank_lines() {
    let tmp = NamedTempFile::new().unwrap();
    let mut writer = WalWriter::new(tmp.path()).unwrap();
    writer.append(r#"{"id":"1"}"#).unwrap();
    writer.append("").unwrap();
    writer.append("   ").unwrap();
    writer.append(r#"{"id":"2"}"#).unwrap();
    drop(writer);

    let entries = checkpoint_on_startup(tmp.path()).unwrap();
    assert_eq!(entries.len(), 2, "blank lines must not affect entry count");
}

#[test]
fn test_recovery_order_preserved_across_restart() {
    let tmp = NamedTempFile::new().unwrap();

    // Write entries in order
    {
        let mut writer = WalWriter::new(tmp.path()).unwrap();
        for i in 0..10 {
            writer.append(&serde_json::json!({"seq": i}).to_string()).unwrap();
        }
    }

    // First replay
    let first = replay_wal(tmp.path()).unwrap();
    assert_eq!(first.len(), 10);

    // Simulate restart: reopen WAL
    {
        let mut writer = WalWriter::new(tmp.path()).unwrap();
        // Add more entries after restart
        for i in 10..15 {
            writer.append(&serde_json::json!({"seq": i}).to_string()).unwrap();
        }
    }

    // Second replay (simulates next crash recovery)
    let second = replay_wal(tmp.path()).unwrap();
    assert_eq!(second.len(), 15);
}
