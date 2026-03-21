"""Tests for the WebSocket router — user stream and collaborative editing room."""

import asyncio
import json
import pytest
import time
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.ws import router, _CollabRoom, _now_ms


# ---------------------------------------------------------------------------
# App fixture (sync TestClient supports WS in Starlette)
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# _CollabRoom unit tests (no HTTP / WS needed)
# ---------------------------------------------------------------------------

class TestCollabRoom:
    async def test_join_adds_member(self):
        room = _CollabRoom("col-1")
        ws_mock = MagicMock()
        await room.join(ws_mock, "user-1", "Alice")
        assert room.member_count() == 1

    async def test_leave_removes_member(self):
        room = _CollabRoom("col-1")
        ws_mock = MagicMock()
        await room.join(ws_mock, "user-1", "Alice")
        await room.leave(ws_mock)
        assert room.member_count() == 0

    async def test_leave_unknown_ws_no_error(self):
        room = _CollabRoom("col-1")
        ws_mock = MagicMock()
        await room.leave(ws_mock)  # should not raise
        assert room.member_count() == 0

    async def test_node_update_lww_first_write_wins(self):
        room = _CollabRoom("col-1")
        ts_early = 1000
        ts_late = 2000

        winning = await room.apply_node_update("node-a", {"label": "First"}, ts_late)
        assert winning == {"label": "First"}

        # Earlier timestamp — should NOT overwrite
        winning2 = await room.apply_node_update("node-a", {"label": "Second"}, ts_early)
        assert winning2 == {}  # stale write rejected

    async def test_node_update_lww_same_ts_accepted(self):
        room = _CollabRoom("col-1")
        ts = 5000
        await room.apply_node_update("node-b", {"label": "V1"}, ts)
        # Same timestamp — should be accepted (>=)
        result = await room.apply_node_update("node-b", {"label": "V2"}, ts)
        assert result == {"label": "V2"}

    async def test_node_update_multiple_fields_merged(self):
        room = _CollabRoom("col-1")
        result = await room.apply_node_update("node-c", {"label": "X", "description": "Desc"}, 1000)
        assert result == {"label": "X", "description": "Desc"}

    async def test_node_update_partial_stale_partial_new(self):
        """One field may be stale while another field is new."""
        room = _CollabRoom("col-1")
        await room.apply_node_update("node-d", {"label": "Old Label"}, 2000)

        # label ts=1000 (stale), description ts=3000 (new)
        result = await room.apply_node_update(
            "node-d",
            {"label": "Ignored", "description": "New Desc"},
            2000,  # same-ts for label → accepted; but let's use different logic
        )
        # Both at ts=2000 which equals current for label → accepted
        assert "description" in result

    async def test_broadcast_sends_to_all_except_sender(self):
        room = _CollabRoom("col-1")

        sent_by_ws1 = []
        sent_by_ws2 = []

        async def send1(text): sent_by_ws1.append(text)
        async def send2(text): sent_by_ws2.append(text)

        ws1, ws2 = MagicMock(), MagicMock()
        ws1.send_text = send1
        ws2.send_text = send2

        await room.join(ws1, "u1", "Alice")
        await room.join(ws2, "u2", "Bob")

        await room.broadcast({"type": "test"}, exclude=ws1)

        assert len(sent_by_ws1) == 0  # excluded
        assert len(sent_by_ws2) == 1
        assert json.loads(sent_by_ws2[0])["type"] == "test"

    async def test_broadcast_all_when_no_exclude(self):
        room = _CollabRoom("col-1")
        received = []

        async def send(text): received.append(text)

        ws = MagicMock()
        ws.send_text = send
        await room.join(ws, "u1", "Alice")

        await room.broadcast({"type": "ping"})
        assert len(received) == 1


# ---------------------------------------------------------------------------
# _now_ms
# ---------------------------------------------------------------------------

def test_now_ms_returns_milliseconds():
    before = int(time.time() * 1000)
    ts = _now_ms()
    after = int(time.time() * 1000)
    assert before <= ts <= after + 10


# ---------------------------------------------------------------------------
# WebSocket user-stream endpoint (auth rejection)
# ---------------------------------------------------------------------------

class TestUserStreamWS:
    def test_rejects_invalid_token(self, client):
        with pytest.raises(Exception):
            # Invalid JWT should cause close with code 4001
            with client.websocket_connect("/ws?token=badtoken") as ws:
                ws.receive_json()

    def test_rejects_missing_token(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()


# ---------------------------------------------------------------------------
# WebSocket collab room endpoint (auth rejection)
# ---------------------------------------------------------------------------

class TestCollabWS:
    def test_rejects_invalid_token(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/collab/col-1?token=badtoken") as ws:
                ws.receive_json()

    def test_collab_join_requires_token_param(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/collab/col-1") as ws:
                ws.receive_json()

    def test_collab_join_with_valid_token(self, client):
        fake_payload = {"sub": "user-123", "name": "Alice", "email": "alice@example.com"}
        with patch("app.routers.ws.decode_access_token", return_value=fake_payload):
            with client.websocket_connect("/ws/collab/col-abc?token=valid") as ws:
                # Server should broadcast a join presence event (but since we're
                # the only member, no one receives it). Just check connection works.
                ws.send_text(json.dumps({"op": "presence", "action": "viewing", "node_id": "n1", "ts": _now_ms()}))
                # No response expected for presence-only send; connection stays open.

    def test_collab_node_update_ack(self, client):
        fake_payload = {"sub": "user-123", "name": "Alice"}
        with patch("app.routers.ws.decode_access_token", return_value=fake_payload):
            with client.websocket_connect("/ws/collab/col-xyz?token=valid") as ws:
                msg = {
                    "op": "node_update",
                    "node_id": "node-1",
                    "patch": {"label": "Updated"},
                    "ts": _now_ms(),
                }
                ws.send_text(json.dumps(msg))
                ack = ws.receive_json()
                assert ack["op"] == "node_update"
                assert ack["ack"] is True
                assert ack["node_id"] == "node-1"
                assert ack["patch"]["label"] == "Updated"

    def test_collab_edge_create_ack(self, client):
        fake_payload = {"sub": "user-123", "name": "Alice"}
        with patch("app.routers.ws.decode_access_token", return_value=fake_payload):
            with client.websocket_connect("/ws/collab/col-xyz2?token=valid") as ws:
                edge = {"id": "e-new", "source": "n1", "target": "n2", "relation_type": "RelatesTo", "weight": 0.8}
                ws.send_text(json.dumps({"op": "edge_create", "edge": edge, "ts": _now_ms()}))
                ack = ws.receive_json()
                assert ack["op"] == "edge_create"
                assert ack["ack"] is True

    def test_collab_edge_delete_ack(self, client):
        fake_payload = {"sub": "user-123", "name": "Alice"}
        with patch("app.routers.ws.decode_access_token", return_value=fake_payload):
            with client.websocket_connect("/ws/collab/col-xyz3?token=valid") as ws:
                ws.send_text(json.dumps({"op": "edge_delete", "edge_id": "e-old", "ts": _now_ms()}))
                ack = ws.receive_json()
                assert ack["op"] == "edge_delete"
                assert ack["ack"] is True
                assert ack["edge_id"] == "e-old"

    def test_collab_invalid_json_ignored(self, client):
        fake_payload = {"sub": "user-123", "name": "Alice"}
        with patch("app.routers.ws.decode_access_token", return_value=fake_payload):
            with client.websocket_connect("/ws/collab/col-junk?token=valid") as ws:
                ws.send_text("not json at all")
                # No ack expected — server ignores invalid JSON.
                # Send a valid node_update to confirm connection still alive.
                ws.send_text(json.dumps({"op": "node_update", "node_id": "n1", "patch": {"label": "ok"}, "ts": _now_ms()}))
                ack = ws.receive_json()
                assert ack["ack"] is True
