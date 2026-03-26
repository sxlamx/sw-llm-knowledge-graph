#!/usr/bin/env python3
"""
LanceDB migration runner.

Usage:
    python sql/migrate.py               # apply all pending migrations
    python sql/migrate.py --status      # show applied / pending migrations
    python sql/migrate.py --dry-run     # list what would be applied, without running

Migrations are Python files in sql/ named V<NNN>__<YYYYMMDD>_<description>.py.
Each file must define:
    DESCRIPTION: str        — one-line description logged in the _migrations table
    up(db) -> None          — called with a lancedb.LanceDBConnection; must be idempotent

Applied migrations are tracked in the _migrations table in LanceDB.
Running from scratch (empty DB) applies all migrations in version order.
Re-running against an existing DB skips already-applied versions.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import time
from pathlib import Path

# Ensure we can import app config for LANCEDB_PATH
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python-api"))

import lancedb
import pyarrow as pa

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SQL_DIR = Path(__file__).parent
_VERSION_RE = re.compile(r"^V(\d+)__(\d{8})_(.+)\.py$")

_MIGRATIONS_SCHEMA = pa.schema([
    pa.field("version", pa.int32()),
    pa.field("filename", pa.string()),
    pa.field("description", pa.string()),
    pa.field("applied_at", pa.int64()),   # epoch microseconds
    pa.field("duration_ms", pa.float32()),
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_lancedb() -> lancedb.LanceDBConnection:
    """Open LanceDB using LANCEDB_PATH env var or app config."""
    db_path = os.environ.get("LANCEDB_PATH")
    if not db_path:
        # Fall back to loading app config
        try:
            from app.config import get_settings
            db_path = str(get_settings().lancedb_path)
        except Exception:
            db_path = str(_REPO_ROOT / ".data" / "lancedb")
    return lancedb.connect(db_path)


def _get_migration_files() -> list[tuple[int, str, Path]]:
    """Return sorted list of (version, filename, path) for all migration files."""
    results = []
    for f in sorted(SQL_DIR.glob("V*.py")):
        if f.name == "migrate.py":
            continue
        m = _VERSION_RE.match(f.name)
        if m:
            version = int(m.group(1))
            results.append((version, f.name, f))
    return sorted(results, key=lambda x: x[0])


def _get_applied_versions(db: lancedb.LanceDBConnection) -> set[int]:
    """Return set of already-applied migration version numbers."""
    try:
        tbl = db.open_table("_migrations")
        rows = tbl.search().select(["version"]).limit(10_000).to_list()
        return {r["version"] for r in rows}
    except Exception:
        return set()


def _record_migration(db, version: int, filename: str, description: str, duration_ms: float) -> None:
    record = {
        "version": version,
        "filename": filename,
        "description": description,
        "applied_at": int(time.time() * 1_000_000),
        "duration_ms": float(duration_ms),
    }
    try:
        tbl = db.open_table("_migrations")
    except Exception:
        tbl = db.create_table("_migrations", schema=_MIGRATIONS_SCHEMA, exist_ok=True)
    tbl.add([record])


def _run_migration(db, version: int, filename: str, path: Path, dry_run: bool) -> None:
    spec = importlib.util.spec_from_file_location(f"migration_{version}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    description = getattr(mod, "DESCRIPTION", filename)
    if dry_run:
        print(f"  [dry-run] V{version:03d} — {description}")
        return

    print(f"  Applying V{version:03d} — {description} ... ", end="", flush=True)
    t0 = time.monotonic()
    mod.up(db)
    elapsed = (time.monotonic() - t0) * 1000
    _record_migration(db, version, filename, description, elapsed)
    print(f"done ({elapsed:.0f} ms)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="LanceDB migration runner")
    parser.add_argument("--status", action="store_true", help="Show migration status and exit")
    parser.add_argument("--dry-run", action="store_true", help="List pending migrations without applying")
    args = parser.parse_args()

    db = _load_lancedb()
    files = _get_migration_files()
    applied = _get_applied_versions(db)

    if args.status:
        print(f"\nLanceDB: {db.uri}")
        print(f"{'VER':<6} {'STATUS':<10} {'FILENAME'}")
        print("-" * 72)
        for version, filename, _ in files:
            status = "applied" if version in applied else "PENDING"
            print(f"V{version:<5} {status:<10} {filename}")
        pending = sum(1 for v, _, _ in files if v not in applied)
        print(f"\n{len(applied)} applied, {pending} pending\n")
        return

    pending = [(v, fn, p) for v, fn, p in files if v not in applied]
    if not pending:
        print("All migrations already applied.")
        return

    print(f"\nApplying {len(pending)} migration(s) to {db.uri}\n")
    for version, filename, path in pending:
        _run_migration(db, version, filename, path, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"\nDone. {len(pending)} migration(s) applied.")


if __name__ == "__main__":
    main()
