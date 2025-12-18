---
name: Real-Time Presence System
overview: Implement a production-ready real-time presence platform with online/typing/away/offline status, last seen timestamps, and efficient Redis-based state management supporting friends and DM contacts visibility.
todos:
  - id: todo-0
    content: Create UserPresence model in chat/models.py
    status: pending
  - id: todo-1
    content: Implement PresenceCache strategy in chat/presence_cache.py
    status: pending
  - id: todo-2
    content: Build PresenceRepository in chat/repository_layer/presence_repo.py
    status: pending
  - id: todo-3
    content: Implement PresenceService core logic in chat/presence_service.py
    status: pending
  - id: todo-4
    content: Add heartbeat handling to ChatConsumer
    status: pending
  - id: todo-5
    content: Add typing indicator handling to ChatConsumer
    status: pending
  - id: todo-6
    content: Implement presence status broadcasting in ChatConsumer
    status: pending
  - id: todo-7
    content: Generate and apply database migrations
    status: pending
  - id: todo-8
    content: Create REST API endpoints for presence (Phase 2)
    status: pending
---

# Real-Time Presence System Implementation

## Architecture Overview

Implement a scalable presence system using Redis Sorted Sets for efficient online user tracking, Redis Hashes for user status storage, and Django Channels for real-time broadcasting. The system will track online/typing/away/offline states with last seen timestamps.

## Core Components

### 1. Data Model Layer

**New Model: `chat/models.py`**

- Add `UserPresence` model to track presence state in PostgreSQL
- Fields: user (FK), status (choices), last_seen, last_heartbeat
- This is the source of truth for presence data

### 2. Redis Data Structures (DB 1 - Django Cache)

**Three key Redis structures:**

**a) Online Users Sorted Set**

```
Key: presence:online_users
Type: ZSET
Score: timestamp
Members: user_id
Purpose: O(1) online checks, efficient range queries for "who's online"
```

**b) User Status Hash**

```
Key: presence:user:{user_id}
Type: HASH
Fields: {status: "online", last_seen: timestamp, typing_in: conversation_id}
Purpose: Full status details per user
```

**c) Conversation Typing Set**

```
Key: presence:typing:{conversation_id}
Type: SET
Members: user_ids currently typing
Purpose: Track who's typing in each conversation
```

### 3. Presence Service (`chat/presence_service.py`)

Core business logic module implementing:

**Status Management:**

- `update_presence(user_id, status)` - Write-through to Redis + DB
- `get_user_status(user_id)` - Read from Redis (cache-aside to DB)
- `get_online_friends(user_id)` - Query online users from friend list
- `get_online_in_conversation(conversation_id)` - Who's online in this chat

**Heartbeat Processing:**

- `process_heartbeat(user_id)` - Update timestamp, refresh TTL
- `mark_offline(user_id)` - Cleanup when user disconnects
- Actor-like pattern: delayed trigger for timeout detection

**Typing Indicators:**

- `start_typing(user_id, conversation_id)` - 3-second ephemeral state
- `stop_typing(user_id, conversation_id)` - Explicit stop

### 4. UDP Heartbeat Server (`chat/heartbeat_server.py`)

**Dedicated UDP server for receiving heartbeats:**

**Why UDP?**

- Connectionless and lightweight (no TCP handshake overhead)
- Fire-and-forget semantics perfect for heartbeats
- Reduced battery consumption on mobile clients
- Handles packet loss gracefully (missing one heartbeat is acceptable)

**Implementation using asyncio UDP protocol:**

```python
class HeartbeatProtocol(asyncio.DatagramProtocol):
    def datagram_received(self, data, addr):
        # Parse: {"user_id": 123, "timestamp": 1234567890}
        # Call PresenceService.process_heartbeat()
```

**UDP Packet Format:**

```json
{
    "user_id": 123,
    "token": "auth_token_here",
    "timestamp": 1234567890
}
```

**Server binding:**

- Port: 5555 (configurable)
- Protocol: UDP/IPv4
- Max packet size: 512 bytes

**Security considerations:**

- Auth token validation required
- Rate limiting per user_id (max 1 heartbeat/10s)
- Drop malformed packets silently

### 5. WebSocket Integration (`chat/consumers.py`)

**Extend `ChatConsumer` for broadcasting only:**

**New message types to handle (client → server):**

```python
{
    "type": "presence.typing.start",
    "conversation_id": 123
}

{
    "type": "presence.typing.stop",
    "conversation_id": 123
}
```

**Connection lifecycle hooks:**

- `connect()` - Join presence groups, send current status
- `disconnect()` - Mark offline (backup to UDP timeout), cleanup
- `receive()` - Handle typing events only (no heartbeat here)

**Broadcasting handlers (server → client via WebSocket):**

```python
async def presence_status_changed(self, event):
    # Broadcast presence updates to client
    await self.send(json.dumps({
        "type": "presence.status",
        "user_id": event["user_id"],
        "status": event["status"],
        "last_seen": event["last_seen"]
    }))

async def presence_typing_update(self, event):
    # Broadcast typing indicators
    await self.send(json.dumps({
        "type": "presence.typing",
        "user_id": event["user_id"],
        "conversation_id": event["conversation_id"],
        "is_typing": event["is_typing"]
    }))
```

**Hybrid transport model:**

- UDP receives heartbeats → updates Redis → triggers WebSocket broadcasts
- Client maintains both: UDP socket for heartbeats + WebSocket for chat/presence updates

### 5. Cache Strategy (`chat/presence_cache.py`)

**New cache strategy class:**

- Extends `BaseCacheStrategy` from `core/caching.py`
- Domain: "presence"
- TTL: 300 seconds (5 minutes)
- Implements write-through for status changes
- Atomic operations using Redis pipelines

**Key methods:**

- `set_user_online(user_id, timestamp)` - ZADD + HSET + EXPIRE
- `set_user_offline(user_id)` - ZREM + HSET (offline state) + persist to DB
- `get_online_users(user_ids)` - ZSCORE batch lookup
- `cleanup_stale_presence()` - Remove users with expired TTLs

### 6. Repository Layer (`chat/repository_layer/presence_repo.py`)

Database access abstraction:

```python
class PresenceRepository:
    @staticmethod
    def update_user_presence(user_id, status, last_seen):
        # Upsert to UserPresence model
        
    @staticmethod
    def get_user_connections(user_id):
        # Get mutual followers + conversation participants
        
    @staticmethod
    def get_presence_subscribers(user_id):
        # Who should see this user's presence?
        # Returns list of user_ids
```

### 7. REST API Endpoints (`chat/views.py` + `chat/urls.py`)

**Phase 2 - HTTP heartbeat endpoint:**

```
POST /api/presence/heartbeat/
GET /api/presence/status/<user_id>/
GET /api/presence/online-friends/
```

## Broadcasting Strategy

**Channel Layer Groups:**

**1. User-specific presence group:**

```python
group_name = f"presence_user_{user_id}"
# All clients of this user (multiple devices)
```

**2. Friendship presence groups:**

```python
# When user goes online/offline, broadcast to:
friends = PresenceRepository.get_presence_subscribers(user_id)
for friend_id in friends:
    await channel_layer.group_send(
        f"presence_user_{friend_id}",
        {"type": "presence.status.changed", ...}
    )
```

**3. Conversation presence groups:**

```python
group_name = f"presence_conversation_{conversation_id}"
# Typing indicators scoped to conversation
```

## Timeout & Offline Detection

**Actor Model Implementation:**

Each online user gets a "virtual actor" tracked in memory:

1. Heartbeat received → Cancel existing timeout, schedule new one
2. Timeout fires (60s no heartbeat) → Mark user offline
3. Use `asyncio.create_task()` for delayed triggers

**Graceful shutdown:**

- Django signal handlers to cleanup pending timeouts
- Persist current state to DB before service restart

## Data Persistence Strategy

**Write-through pattern:**

1. Critical status changes → Redis + PostgreSQL immediately
2. Heartbeat updates → Redis only (reduce DB writes)
3. Periodic background job → Sync Redis state to PostgreSQL every 5 minutes

**Why?**

- Redis is fast but volatile
- PostgreSQL is durable but slower
- Hybrid approach: real-time + durability

## Friend/Connection Logic

**"Friends + DM contacts" visibility rule:**

```python
def get_presence_subscribers(user_id):
    # 1. Mutual followers (bidirectional following)
    mutual = get_mutual_followers(user_id)
    
    # 2. Anyone in a conversation with this user
    dm_contacts = get_conversation_participants(user_id)
    
    return list(set(mutual) | set(dm_contacts))
```

## Performance Optimizations

1. **Redis pipelining** - Batch operations (ZADD, HSET, EXPIRE) atomically
2. **Sorted Set range queries** - O(log N) for "get online friends"
3. **Lazy loading** - Only subscribe to presence for visible users
4. **Debouncing** - Typing indicators throttled to 1 update/3s
5. **Stale presence cleanup** - Background Celery task every 5 minutes

## Error Handling & Resilience

1. **Redis failure** - Fallback to PostgreSQL queries (degraded mode)
2. **Network jitter** - 60s timeout window prevents flapping
3. **Race conditions** - Use Redis transactions (MULTI/EXEC)
4. **Partial failures** - Log errors, continue operation (best effort)

## Migration Strategy

**Database migrations:**

1. Add `UserPresence` model
2. Create indexes on (user, last_seen) for efficient queries

**Deployment:**

1. Deploy presence backend (consumers, service, cache)
2. No breaking changes to existing chat functionality
3. Clients opt-in by sending heartbeat messages

## Testing Considerations

1. Unit tests for `PresenceService` logic
2. Integration tests for Redis operations
3. WebSocket consumer tests for message handling
4. Load testing: simulate 10k concurrent heartbeats

## Files to Create/Modify

**New files:**

- `chat/models.py` - Add `UserPresence` model
- `chat/presence_service.py` - Core presence logic
- `chat/presence_cache.py` - Redis cache strategy
- `chat/repository_layer/presence_repo.py` - DB access
- `chat/migrations/000X_add_user_presence.py` - Auto-generated

**Modified files:**

- `chat/consumers.py` - Add heartbeat/typing handlers
- `chat/views.py` - Add HTTP endpoints (Phase 2)
- `chat/urls.py` - Register new routes
- `core/caching.py` - No changes (reuse existing abstractions)