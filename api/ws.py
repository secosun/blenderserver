from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("blenderserver.ws")

router = APIRouter(tags=["websocket"])

# Active WebSocket connections keyed by task_id
_connections: dict[str, set] = {}


async def broadcast(task_id: str, event: dict):
    """Push an event to all WebSocket clients watching a task."""
    if task_id not in _connections:
        return
    dead: set = set()
    for ws in _connections[task_id]:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    _connections[task_id] -= dead


@router.websocket("/ws/{task_id}")
async def task_websocket(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for real-time task progress updates.

    Usage: ``ws://host:port/api/ws/{task_id}``

    Events pushed to the client:
    - ``{"type": "status", "status": "...", "progress": 0.0, "message": "..."}``
    - ``{"type": "completed", "result_url": "..."}``
    - ``{"type": "failed", "error": "..."}``
    """
    await websocket.accept()
    _connections.setdefault(task_id, set()).add(websocket)
    logger.info("WS client connected for task %s", task_id)

    try:
        # Send current state immediately
        task = await websocket.app.state.task_manager.get_task(task_id)
        if task:
            await websocket.send_json({"type": "status", **task})

        # Keep connection alive until client disconnects
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("WS error for task %s: %s", task_id, e)
    finally:
        _connections.get(task_id, set()).discard(websocket)
        logger.info("WS client disconnected for task %s", task_id)
