"""WebSocket endpoint — real-time job progress, graph updates, and collaborative editing.

Two connection modes
--------------------
1. **User stream** ``WS /ws?token=<jwt>``
   Push job-progress events and graph_update broadcasts to the authenticated user.
   Compatible with the existing wsMiddleware in the frontend.

2. **Collection room** ``WS /ws/collab/{collection_id}?token=<jwt>``
   Multi-user room for real-time collaborative graph editing.

   Collaborative editing protocol
   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
   Clients send JSON messages of the shape:

       { "op": "node_update",  "node_id": "...", "patch": {...}, "ts": <unix_ms> }
       { "op": "edge_create",  "edge": {...},    "ts": <unix_ms> }
       { "op": "edge_delete",  "edge_id": "...", "ts": <unix_ms> }
       { "op": "presence",     "node_id": "...",  "action": "viewing|leave" }

   The server applies **last-write-wins** on a per-field basis for ``node_update``
   ops (comparing ``ts``), then broadcasts the authoritative state to all other
   room members.

   Presence is forwarded as-is so clients can show "who is viewing this node".
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Path as FPath
from app.auth.jwt import decode_access_token
from app.pipeline.job_manager import get_job_manager

router = APIRouter()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User → sockets map (for broadcast_to_user)
# ---------------------------------------------------------------------------

_connections: dict[str, set[WebSocket]] = defaultdict(set)


def broadcast_to_user(user_id: str, message: dict) -> None:
    """Push a message to all WebSocket connections for *user_id*."""
    sockets = _connections.get(user_id, set())
    payload = json.dumps(message)
    for ws in list(sockets):
        try:
            asyncio.ensure_future(ws.send_text(payload))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Collaborative room state
# ---------------------------------------------------------------------------

class _CollabRoom:
    """State for one collection's collaborative editing room."""

    def __init__(self, collection_id: str) -> None:
        self.collection_id = collection_id
        # ws → {"user_id": str, "name": str}
        self.members: dict[WebSocket, dict] = {}
        # node_id → {field: (value, ts_ms)} — LWW register per field
        self._node_lww: dict[str, dict[str, tuple]] = {}
        self._lock = asyncio.Lock()

    async def join(self, ws: WebSocket, user_id: str, user_name: str) -> None:
        async with self._lock:
            self.members[ws] = {"user_id": user_id, "name": user_name}

    async def leave(self, ws: WebSocket) -> None:
        async with self._lock:
            self.members.pop(ws, None)

    def member_count(self) -> int:
        return len(self.members)

    async def apply_node_update(
        self, node_id: str, patch: dict, ts_ms: int
    ) -> dict:
        """Apply LWW merge and return the winning patch (fields that actually changed)."""
        async with self._lock:
            register = self._node_lww.setdefault(node_id, {})
            winning_patch: dict = {}
            for field, value in patch.items():
                existing_ts = register.get(field, (None, -1))[1]
                if ts_ms >= existing_ts:
                    register[field] = (value, ts_ms)
                    winning_patch[field] = value
            return winning_patch

    async def broadcast(
        self,
        message: dict,
        exclude: Optional[WebSocket] = None,
    ) -> None:
        """Send *message* to all room members except *exclude*."""
        payload = json.dumps(message)
        async with self._lock:
            targets = [ws for ws in self.members if ws is not exclude]
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                pass


# Collection rooms — created on first join, reaped when empty
_rooms: dict[str, _CollabRoom] = {}
_rooms_lock = asyncio.Lock()


async def _get_or_create_room(collection_id: str) -> _CollabRoom:
    async with _rooms_lock:
        if collection_id not in _rooms:
            _rooms[collection_id] = _CollabRoom(collection_id)
        return _rooms[collection_id]


async def _reap_room(collection_id: str) -> None:
    async with _rooms_lock:
        room = _rooms.get(collection_id)
        if room and room.member_count() == 0:
            del _rooms[collection_id]


# ---------------------------------------------------------------------------
# Endpoint 1: user-level stream (job progress + graph updates)
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
) -> None:
    """User-level WebSocket — pushes job progress and graph update events."""
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub", "")
        if not user_id:
            await websocket.close(code=4001)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    _connections[user_id].add(websocket)
    logger.info(f"WS connected: user={user_id}")

    event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    try:
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
            except asyncio.CancelledError:
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning(f"WS error user={user_id}: {exc}")
    finally:
        _connections[user_id].discard(websocket)
        if not _connections[user_id]:
            del _connections[user_id]
        logger.info(f"WS disconnected: user={user_id}")


# ---------------------------------------------------------------------------
# Endpoint 2: collaborative collection room
# ---------------------------------------------------------------------------

@router.websocket("/ws/collab/{collection_id}")
async def collab_endpoint(
    websocket: WebSocket,
    collection_id: str = FPath(...),
    token: str = Query(...),
) -> None:
    """Per-collection collaborative editing room with LWW conflict resolution."""
    # Authenticate
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub", "")
        user_name: str = payload.get("name", payload.get("email", user_id))
        if not user_id:
            await websocket.close(code=4001)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    room = await _get_or_create_room(collection_id)

    await websocket.accept()
    await room.join(websocket, user_id, user_name)
    logger.info(f"Collab joined: user={user_id} collection={collection_id} members={room.member_count()}")

    # Announce join to other members
    await room.broadcast(
        {
            "type": "presence",
            "action": "join",
            "user_id": user_id,
            "name": user_name,
            "ts": _now_ms(),
        },
        exclude=websocket,
    )

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=45.0)
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_json({"type": "ping", "ts": _now_ms()})
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            op = msg.get("op")
            ts_ms = int(msg.get("ts", _now_ms()))

            if op == "node_update":
                node_id = msg.get("node_id")
                patch = msg.get("patch") or {}
                if node_id and patch:
                    winning_patch = await room.apply_node_update(node_id, patch, ts_ms)
                    if winning_patch:
                        broadcast_msg = {
                            "type": "collab",
                            "op": "node_update",
                            "node_id": node_id,
                            "patch": winning_patch,
                            "user_id": user_id,
                            "ts": ts_ms,
                        }
                        await room.broadcast(broadcast_msg, exclude=websocket)
                        # Confirm to sender
                        await websocket.send_json({**broadcast_msg, "ack": True})

            elif op == "edge_create":
                edge = msg.get("edge") or {}
                if edge:
                    broadcast_msg = {
                        "type": "collab",
                        "op": "edge_create",
                        "edge": edge,
                        "user_id": user_id,
                        "ts": ts_ms,
                    }
                    await room.broadcast(broadcast_msg, exclude=websocket)
                    await websocket.send_json({**broadcast_msg, "ack": True})

            elif op == "edge_delete":
                edge_id = msg.get("edge_id")
                if edge_id:
                    broadcast_msg = {
                        "type": "collab",
                        "op": "edge_delete",
                        "edge_id": edge_id,
                        "user_id": user_id,
                        "ts": ts_ms,
                    }
                    await room.broadcast(broadcast_msg, exclude=websocket)
                    await websocket.send_json({**broadcast_msg, "ack": True})

            elif op == "presence":
                # Forward presence events (viewing, cursor position, etc.)
                await room.broadcast(
                    {
                        "type": "presence",
                        "action": msg.get("action", "viewing"),
                        "node_id": msg.get("node_id"),
                        "user_id": user_id,
                        "name": user_name,
                        "ts": ts_ms,
                    },
                    exclude=websocket,
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning(f"Collab WS error user={user_id} collection={collection_id}: {exc}")
    finally:
        await room.leave(websocket)
        await room.broadcast(
            {
                "type": "presence",
                "action": "leave",
                "user_id": user_id,
                "name": user_name,
                "ts": _now_ms(),
            }
        )
        await _reap_room(collection_id)
        logger.info(f"Collab left: user={user_id} collection={collection_id}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)
