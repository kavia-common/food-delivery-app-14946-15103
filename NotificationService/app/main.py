import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, status, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

# -----------------------------------------------------------------------------
# FastAPI app with OpenAPI metadata and tags
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Notification Service API",
    description="Sends notifications via in-app, email, SMS, and supports WebSocket streams.",
    version="1.0.0",
    openapi_tags=[
        {"name": "Notifications", "description": "Realtime notifications publishing and streaming"},
    ],
)

# CORS setup (liberal defaults for demo; adjust in production via env)
allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins.split(",")] if allowed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()


# -----------------------------------------------------------------------------
# Pydantic Models aligned with openapi/notification.yaml
# -----------------------------------------------------------------------------

class NotificationType(str):
    ORDER_UPDATE = "order_update"
    PROMOTION = "promotion"
    SYSTEM = "system"
    REVIEW_EVENT = "review_event"


class Channel(str):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"
    PUSH = "push"


class Notification(BaseModel):
    """Represents a notification event delivered to clients."""
    id: str = Field(..., description="Unique notification identifier")
    userId: Optional[str] = Field(None, description="User ID the notification targets")
    orderId: Optional[str] = Field(None, description="Order ID associated with the notification")
    type: str = Field(..., description="Type of notification (order_update, promotion, system, review_event)")
    title: str = Field(..., description="Notification title")
    body: Optional[str] = Field(None, description="Notification body")
    data: Optional[Dict[str, Any]] = Field(None, description="Additional metadata payload")
    read: bool = Field(default=False, description="Read status")
    createdAt: datetime = Field(..., description="Creation timestamp in ISO8601 format")


class NotificationCreateRequest(BaseModel):
    """Request payload for creating/publishing a new notification."""
    userId: Optional[str] = Field(None, description="Target user identifier (optional)")
    topic: Optional[str] = Field(None, description="Target topic to broadcast to (optional)")
    orderId: Optional[str] = Field(None, description="Associated order identifier (optional)")
    type: str = Field(..., description="Type of notification (order_update, promotion, system, review_event)")
    title: str = Field(..., description="Notification title")
    body: Optional[str] = Field(None, description="Notification body")
    data: Optional[Dict[str, Any]] = Field(None, description="Arbitrary key-value payload")
    channels: Optional[List[str]] = Field(
        None,
        description="Delivery channels (in_app, email, sms, push). Only in_app is implemented in this service."
    )

    @validator("type")
    def validate_type(cls, v: str) -> str:
        allowed = {NotificationType.ORDER_UPDATE, NotificationType.PROMOTION, NotificationType.SYSTEM, NotificationType.REVIEW_EVENT}
        if v not in allowed:
            raise ValueError(f"type must be one of {sorted(list(allowed))}")
        return v

    @validator("channels", each_item=True)
    def validate_channel(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {Channel.IN_APP, Channel.EMAIL, Channel.SMS, Channel.PUSH}
        if v not in allowed:
            raise ValueError(f"channels items must be one of {sorted(list(allowed))}")
        return v


# -----------------------------------------------------------------------------
# In-memory Broker
# -----------------------------------------------------------------------------

class ConnectionManager:
    """
    Manages active WebSocket connections and their subscriptions.

    - Clients connect to /notifications/stream and can optionally pass query params:
        ?userId=<id>&topic=<topic>
      We store their subscription interests for simple filtering.
    """

    def __init__(self) -> None:
        # Store all active websockets
        self.active_connections: Set[WebSocket] = set()
        # Simple subscription metadata (maps websocket to filters)
        self.subscriptions: Dict[WebSocket, Dict[str, Optional[str]]] = {}
        # Asyncio lock to avoid race conditions on connection add/remove/broadcast
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: Optional[str], topic: Optional[str]) -> None:
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
            self.subscriptions[websocket] = {"userId": user_id, "topic": topic}

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self.active_connections.discard(websocket)
            self.subscriptions.pop(websocket, None)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """
        Broadcast a message to all connected clients honoring basic subscription filters:
        - If a client subscribed with userId, it will receive messages where message.userId matches or is None.
        - If a client subscribed with topic, it will receive messages where message.data.topic matches client topic,
          or if the message has explicit top-level 'topic' field set.
        """
        data_text = json.dumps(message, default=str)
        async with self._lock:
            to_remove: List[WebSocket] = []
            for ws in list(self.active_connections):
                filters = self.subscriptions.get(ws, {})
                user_filter = filters.get("userId")
                topic_filter = filters.get("topic")

                # Determine matching
                matches_user = True
                if user_filter:
                    matches_user = (message.get("userId") == user_filter)

                # topic could be provided either as top-level or in data
                msg_topic = message.get("topic")
                if not msg_topic:
                    msg_topic = (message.get("data") or {}).get("topic")
                matches_topic = True
                if topic_filter:
                    matches_topic = (msg_topic == topic_filter)

                if matches_user and matches_topic:
                    try:
                        await ws.send_text(data_text)
                    except Exception:
                        # Mark broken connections for removal
                        to_remove.append(ws)

            for ws in to_remove:
                self.active_connections.discard(ws)
                self.subscriptions.pop(ws, None)


manager = ConnectionManager()


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

# PUBLIC_INTERFACE
@router.post(
    "/notifications",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Notifications"],
    summary="Send a notification to a user or topic",
    response_description="Accepted",
)
async def post_notification(payload: NotificationCreateRequest):
    """
    Accept a notification creation request and broadcast to connected clients over WebSocket.

    Parameters:
    - payload: NotificationCreateRequest
        The notification content, including optional routing to userId or topic. Only in-app
        WebSocket delivery is implemented in this service; other channels can be integrated
        via downstream providers in a future iteration.

    Returns:
    - 202 Accepted with a JSON body { "status": "accepted", "id": "<notification_id>" }
    """
    notification = Notification(
        id=str(uuid.uuid4()),
        userId=payload.userId,
        orderId=payload.orderId,
        type=payload.type,
        title=payload.title,
        body=payload.body,
        data=payload.data,
        createdAt=datetime.now(timezone.utc),
    )
    # Prepare message to broadcast. Add topic if provided to ease filtering.
    message: Dict[str, Any] = notification.dict()
    if payload.topic:
        message["topic"] = payload.topic

    await manager.broadcast(message)

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "id": notification.id},
    )


# PUBLIC_INTERFACE
@router.get(
    "/notifications/stream",
    tags=["Notifications"],
    summary="WebSocket endpoint",
    description="Upgrades to WebSocket for real-time notifications. "
                "Connect using ws://host:port/notifications/stream?userId=<id>&topic=<topic>",
    responses={
        101: {"description": "Switching Protocols"},
        400: {"description": "Bad Request"},
    },
)
async def websocket_help():
    """
    Helper endpoint documented for OpenAPI to indicate WebSocket usage.
    See the /ws implementation mounted separately for the actual WebSocket connection.
    """
    return JSONResponse(
        status_code=400,
        content={"detail": "This endpoint is a WebSocket upgrade path. Use a WebSocket client to connect."},
    )


# PUBLIC_INTERFACE
@app.websocket("/notifications/stream")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for receiving real-time notifications.

    Query parameters:
    - userId: optional string to filter messages for a specific user
    - topic: optional string to filter messages for a specific topic

    Behavior:
    - On connect, the server registers the subscription with the provided filters.
    - The server broadcasts notifications to all connections whose filters match.
    - The server periodically pings clients by awaiting messages to detect disconnects.

    Returns:
    - Real-time stream of Notification JSON messages as text frames.
    """
    user_id = websocket.query_params.get("userId")
    topic = websocket.query_params.get("topic")

    try:
        await manager.connect(websocket, user_id=user_id, topic=topic)
        # Keep connection alive; wait for incoming messages to detect disconnects.
        while True:
            # We don't expect client messages; this await simply detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        # On unexpected errors, ensure cleanup
        await manager.disconnect(websocket)


# Mount router
app.include_router(router)


# -----------------------------------------------------------------------------
# Root and Health
# -----------------------------------------------------------------------------

# PUBLIC_INTERFACE
@app.get("/", tags=["Notifications"], summary="Service info")
def root():
    """Returns basic service information."""
    return {
        "name": "Notification Service API",
        "version": "1.0.0",
        "status": "ok",
    }


# PUBLIC_INTERFACE
@app.get("/health", tags=["Notifications"], summary="Liveness/Readiness check")
def health():
    """Simple health check endpoint."""
    return {"status": "healthy"}


# -----------------------------------------------------------------------------
# Dev server entrypoint
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    # Development server for local testing:
    #   uvicorn app.main:app --host 0.0.0.0 --port 8106 --reload
    import uvicorn

    port = int(os.getenv("PORT", "8106"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
