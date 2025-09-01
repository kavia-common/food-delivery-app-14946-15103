# Notification Service

FastAPI-based notification service that supports:
- HTTP POST /notifications to accept and broadcast notifications
- WebSocket /notifications/stream for clients to subscribe to real-time updates

Aligned with openapi/notification.yaml.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8106 --reload
```

## Endpoints

- GET / -> Service info.
- GET /health -> Health check.
- POST /notifications -> Accepts NotificationCreateRequest and broadcasts to connected WebSocket clients. Returns 202 with `{status, id}`.
- WebSocket /notifications/stream -> Clients connect for real-time notifications.
  - Optional query params: `userId`, `topic` to filter messages.
  - Example: `ws://localhost:8106/notifications/stream?userId=123` 

## Message Format

Broadcasted messages match the `Notification` schema with an additional optional top-level `topic` if provided in the POST request:
```json
{
  "id": "uuid",
  "userId": "optional",
  "orderId": "optional",
  "type": "order_update|promotion|system|review_event",
  "title": "string",
  "body": "optional string",
  "data": { "any": "json" },
  "read": false,
  "createdAt": "ISO timestamp",
  "topic": "optional topic"
}
```

## Notes

- This implementation includes an in-memory broker suitable for development/testing. For production-grade reliability, replace with a persistent message queue or broker.
- Only `in_app` channel is implemented here (via WebSocket). Email/SMS/Push can be integrated in future iterations via external providers.
