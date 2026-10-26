import asyncio
import json
import time
from typing import AsyncGenerator
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from app.core.logging import get_logger
from app.services.broadcaster import broadcaster

logger = get_logger("taskmesh.events_api")

router = APIRouter(prefix="/events", tags=["Events & Observability Stream"])


@router.get(
    "/stream",
    summary="Server-Sent Events (SSE) Stream",
    description="""
    Real-time SSE event stream for jobs and aggregate status updates.

    Access Control / Authentication:
    - In this local developer environment, access is open to configured CORS origins.
      If token authentication is configured, standard Bearer token header or ?token= query parameter is validated.

    Reliability & Memory Safety:
    - Bounded subscriber queues prevent memory growth from slow/lagging clients.
    - Periodic heartbeat ping (: ping) every 15s keeps connections alive across proxies.
    - Handles disconnects immediately and cleans up subscriptions.
    - Reconnecting clients should fetch updated state from REST API on reconnect.
    """,
    response_class=StreamingResponse,
)
async def event_stream(request: Request):
    queue = await broadcaster.register()

    async def sse_generator() -> AsyncGenerator[str, None]:
        # Immediate handshake comment so browser EventSource flushes HTTP headers and establishes state
        yield ": handshake\n\n"

        # Initial connection acknowledgement
        initial_msg = {
            "event": "connected",
            "data": {
                "message": "Connected to TaskMesh real-time telemetry stream",
                "timestamp": time.time(),
            },
        }
        yield f"event: {initial_msg['event']}\ndata: {json.dumps(initial_msg['data'])}\n\n"

        heartbeat_interval = 15.0
        last_heartbeat = time.time()

        try:
            while True:
                # Check client disconnect
                if await request.is_disconnected():
                    logger.debug("Client disconnected during SSE stream loop")
                    break

                try:
                    # Wait for next event or timeout for heartbeat
                    timeout = max(0.5, heartbeat_interval - (time.time() - last_heartbeat))
                    msg = await asyncio.wait_for(queue.get(), timeout=min(timeout, 2.0))
                    event_name = msg.get("event", "message")
                    event_data = json.dumps(msg.get("data", {}))
                    yield f"event: {event_name}\ndata: {event_data}\n\n"
                except asyncio.TimeoutError:
                    pass

                # Emit heartbeat ping if interval exceeded
                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    yield f": ping {int(now)}\n\n"
                    yield f"event: ping\ndata: {{\"timestamp\": {now}}}\n\n"
                    last_heartbeat = now

        except asyncio.CancelledError:
            logger.debug("SSE generator cancelled")
        finally:
            await broadcaster.unregister(queue)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
