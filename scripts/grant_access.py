#!/usr/bin/env python3
"""
Manage user access directly in the LanceDB database.

Usage:
    # Grant access (set status=active)
    python scripts/grant_access.py grant kamparboy@gmail.com

    # Revoke access (set status=blocked)
    python scripts/grant_access.py revoke someone@gmail.com

    # Promote to admin
    python scripts/grant_access.py promote kamparboy@gmail.com

    # List all users
    python scripts/grant_access.py list

Run from the repo root. Requires python-api deps (lancedb, pydantic-settings).
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../python-api"))

from app.db.lancedb_client import (
    list_users,
    get_user_by_email,
    update_user,
    create_or_update_user,
)


def _status_icon(status: str) -> str:
    return {"active": "✓", "pending": "⏳", "blocked": "✗"}.get(status, "?")


async def cmd_list():
    users = await list_users()
    if not users:
        print("No users registered yet.")
        return
    print(f"\n{'Email':<35} {'Name':<22} {'Role':<8} {'Status':<10} {'ID'}")
    print("─" * 100)
    for u in sorted(users, key=lambda x: x.get("email", "")):
        icon = _status_icon(u.get("status", "pending"))
        print(
            f"{u.get('email',''):<35} {u.get('name',''):<22} "
            f"{u.get('role','user'):<8} {icon} {u.get('status','pending'):<8}  {u.get('id','')}"
        )
    print()


async def cmd_grant(email: str):
    user = await get_user_by_email(email)
    if not user:
        print(f"  User '{email}' not found in database.")
        print("  They must attempt to log in first (which creates a pending record),")
        print("  or use 'pre-register' if they haven't signed in yet.")
        return
    updated = await update_user(user["id"], {"status": "active"})
    if updated:
        print(f"  ✓  {email}  →  status=active  (role={updated.get('role','user')})")
    else:
        print(f"  ✗  Failed to update {email}")


async def cmd_revoke(email: str):
    user = await get_user_by_email(email)
    if not user:
        print(f"  User '{email}' not found.")
        return
    updated = await update_user(user["id"], {"status": "blocked"})
    if updated:
        print(f"  ✓  {email}  →  status=blocked")
    else:
        print(f"  ✗  Failed to update {email}")


async def cmd_promote(email: str):
    user = await get_user_by_email(email)
    if not user:
        print(f"  User '{email}' not found.")
        return
    updated = await update_user(user["id"], {"role": "admin", "status": "active"})
    if updated:
        print(f"  ✓  {email}  →  role=admin, status=active")
    else:
        print(f"  ✗  Failed to update {email}")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "list":
        await cmd_list()
    elif cmd == "grant" and len(sys.argv) == 3:
        await cmd_grant(sys.argv[2])
    elif cmd == "revoke" and len(sys.argv) == 3:
        await cmd_revoke(sys.argv[2])
    elif cmd == "promote" and len(sys.argv) == 3:
        await cmd_promote(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
