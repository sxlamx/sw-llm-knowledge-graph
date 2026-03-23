#!/usr/bin/env python3
"""
Seed a user directly into the database.

Usage:
    python scripts/seed_user.py <email> [--role admin|user] [--status active|pending|blocked] [--name "Display Name"]

Examples:
    # Pre-register an admin before they log in
    python scripts/seed_user.py kamparboy@gmail.com --role admin --status active

    # Seed a regular active user
    python scripts/seed_user.py someone@example.com --status active

Run from the repo root. Requires python-api deps installed.
"""

import asyncio
import argparse
import sys
import os
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../python-api"))

from app.db.lancedb_client import get_lancedb, get_user_by_email, update_user
from datetime import datetime


async def seed(email: str, role: str, status: str, name: str) -> None:
    db = await get_lancedb()
    now = int(datetime.utcnow().timestamp() * 1_000_000)

    try:
        tbl = db.open_table("users")
    except Exception:
        print("✗  users table does not exist — start the API at least once first.")
        sys.exit(1)

    existing = tbl.search().where(f'email = "{email}"', prefilter=True).limit(1).to_list()

    if existing:
        row = existing[0]
        tbl.delete(f'email = "{email}"')
        tbl.add([{**row, "role": role, "status": status, **({"name": name} if name else {})}])
        print(f"✓  Updated  {email}  →  role={role}  status={status}")
    else:
        tbl.add([{
            "id":         str(uuid.uuid4()),
            "google_sub": "",        # filled in automatically on first Google login
            "email":      email,
            "name":       name,
            "avatar_url": "",
            "role":       role,
            "status":     status,
            "created_at": now,
            "last_login": 0,
        }])
        print(f"✓  Seeded   {email}  →  role={role}  status={status}")
        if not name:
            print("    (name will be filled in on first login)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a user into the KG database.")
    parser.add_argument("email", help="Google email address")
    parser.add_argument("--role",   default="admin",  choices=["admin", "user"])
    parser.add_argument("--status", default="active", choices=["active", "pending", "blocked"])
    parser.add_argument("--name",   default="",       help="Display name (optional)")
    args = parser.parse_args()

    asyncio.run(seed(args.email, args.role, args.status, args.name))


if __name__ == "__main__":
    main()
