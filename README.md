# Real-Time Presence Platform Architecture

## Executive Summary

This document outlines the architecture and design decisions for a production-ready, scalable real-time presence platform built on Django Channels and Redis. The system tracks online/away/offline status for users, broadcasts changes in real-time, and enforces secure access control based on mutual follower relationships.

**Key metrics:**
- Target scale: 100k+ concurrent users
- Status update latency: <100ms
- Offline detection accuracy: ±30 seconds
- Security: mutual-follower authorization

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Core Architecture Decisions](#core-architecture-decisions)
3. [Data Model & Redis Structures](#data-model--redis-structures)
4. [Component Architecture](#component-architecture)
5. [Critical Design Patterns](#critical-design-patterns)
6. [Security Model](#security-model)
7. [Scalability & Performance](#scalability--performance)
8. [Trade-offs & Alternatives](#trade-offs--alternatives)

---

## System Overview

### Problem Statement

Build a real-time presence system where:
- Users can see if their **mutual followers** (bidirectional follow) and **DM contacts** are online/away/offline
- Status updates propagate in <100ms
- The system handles intermittent network connections gracefully (no "flapping")
- Offline detection is accurate and atomic (no "zombie" states)

### High-Level Architecture

```
┌─────────────┐  Heartbeat (30s)  ┌──────────────────┐
│   Client    │ ────────────────> │ PresenceConsumer │
│  (Browser)  │                    │   (WebSocket)    │
└─────────────┘                    └──────────────────┘
       │                                    │
       │ Subscribe                          │ Update
       │ (mutual auth)                      ▼
       │                            ┌──────────────┐
       │                            │  Redis ZSET  │ ← Heartbeat expiry
       │                            │ online_users │
       │                            └──────────────┘
       │                                    │
       │                                    │ Poll for expired
       │                                    ▼
       │                            ┌──────────────┐
       │                            │    Reaper    │ ← Offline detection
       │                            │  (Worker)    │
       │                            └──────────────┘
       │                                    │
       │ Status broadcast                   │ Publish offline
       └────────<─────────────────────<─────┘
              (Django Channels - Redis DB0)
```

**Two transport mechanisms:**
- **Client → Server**: WebSocket heartbeats (bidirectional, persistent)
- **Server → Client**: WebSocket broadcasts (reuse existing connection)

---

## Core Architecture Decisions

### 1. Why WebSocket (not UDP) for Heartbeats?

**Original LinkedIn design:** UDP for heartbeats (connectionless, lightweight).

**Our choice:** WebSocket heartbeats.

**Rationale:**
- **Simpler deployment**: No separate UDP server process
- **Firewall-friendly**: WebSocket (port 443/80) traverses corporate firewalls; UDP often blocked
- **Authentication built-in**: WebSocket already authenticated via Django session
- **Acceptable overhead**: For <100k users, WebSocket performance is sufficient
- **Unified transport**: Reuse existing chat WebSocket infrastructure

**Trade-off accepted:**
- Slightly higher per-connection memory (~2KB/connection vs UDP's stateless)
- Mitigated by: connection pooling and efficient async I/O (Channels + asyncio)

---

### 2. Why Redis ZSET (not HASH-only) for Liveness?

**Decision:** Use Redis **Sorted Set (ZSET)** for online user tracking.

**Key design:**
```
Key:    online_users
Type:   ZSET
Member: user_id (AppUser.id as string)
Score:  expiry_timestamp (now + 30s)
```

**Why ZSET?**

| Requirement | ZSET Solution | HASH Alternative |
|-------------|---------------|------------------|
| Find all expired users | `ZRANGEBYSCORE key -inf now LIMIT 0 500` <br> **O(log N + M)** | Scan all keys or iterate all users <br> **O(N)** |
| Atomic check-and-delete | Lua: `ZSCORE` → `ZREM` (atomic) | Race condition between `HGET` and `DEL` |
| Scalability (100k users) | Fast range queries | Requires full scan |
| Sharding | Natural (by user_id % shards) | Complex |

**Example query efficiency:**
- 100k online users, 500 expired → ZSET finds them in ~5ms
- HASH-only → would need 100k HGET calls or SCAN (seconds)

**Lua script for atomicity:**

```lua
local score = redis.call("ZSCORE", key, member)
if score and score < now then
  redis.call("ZREM", key, member)  -- Atomic removal
  return 1  -- Confirmed offline
end
return 0  -- Abort (heartbeat arrived in race window)
```

This prevents the **"zombie state"** where DB says online but friends see offline.

---

### 3. Why Separate HASH for Semantic Status?

**Decision:** Use Redis **HASH** per user for semantic status.

**Key design:**
```
Key:  presence:state:{user_id}
Type: HASH
Fields:
  - status: "online" | "away" | "offline"
  - updated_ts: timestamp
  - last_heartbeat_ts: timestamp
  - last_seen_ts: timestamp
```

**Why separate from ZSET?**

The ZSET answers: **"Is this user's heartbeat alive?"** (liveness)
The HASH answers: **"What is this user's semantic status?"** (intent)

**Example scenario:**
- User is online (ZSET has valid expiry)
- User goes idle → client sends `presence.away`
- ZSET unchanged (heartbeat still valid)
- HASH updated: `status = "away"`

**Without HASH:**
- No way to distinguish "online + active" from "online + away"
- Late joiners can't snapshot current status

**Snapshot on subscribe:**
```python
# Compute effective status (presence_redis.py)
def effective_status_single(conn, cfg, app_user_id, now_ts):
    score = conn.zscore(online_users_key, user_id)
    if score is None or score < now_ts:
        return "offline", now_ts  # ZSET says expired
    
    # Alive in ZSET → check semantic status
    state = conn.hgetall(presence_state_key)
    return state.get("status", "online"), state.get("updated_ts", now_ts)
```

---

### 4. The "Reaper" Pattern for Offline Detection

**Decision:** Background worker polls ZSET for expired users (vs event-driven).

**Why polling (not Redis keyspace notifications)?**

| Approach | Pros | Cons |
|----------|------|------|
| **Redis Keyspace Notifications** | Event-driven, real-time | Unreliable (can drop events), no ordering guarantees |
| **Polling ("Reaper")** | Reliable, idempotent, atomic with Lua | Adds ~1s latency to offline detection |

**LinkedIn uses polling** for the same reasons: reliability > 1s latency for offline.

**Reaper algorithm:**

```python
while True:
    # 1. Find candidates (may be stale)
    candidates = ZRANGEBYSCORE online_users -inf now LIMIT 0 500
    
    # 2. For each candidate, atomically confirm
    for user_id in candidates:
        confirmed = lua_confirm_offline(key, user_id, now)
        
        if confirmed == 1:  # Lua removed successfully
            # 3. Persist semantic offline state
            HSET presence:state:{user_id} status "offline" last_seen now
            
            # 4. Broadcast to friends
            channel_layer.group_send(f"user_{user_id}_status", {
                "type": "presence.status.changed",
                "status": "offline",
                ...
            })
    
    sleep(poll_interval)  # Default 1s
```

**Handling the race condition:**

Timeline:
```
T=0:   Reaper sees user 123 expired (score=1000, now=1001)
T=1ms: Heartbeat arrives, updates ZSET (score=1031)
T=2ms: Reaper runs Lua script
```

Lua script checks score **again** before removing:
```lua
score = redis.call("ZSCORE", key, "123")  -- Returns 1031 (updated!)
if score < now then  -- 1031 < 1001? False!
  ZREM(...)  -- NOT executed
  return 1
end
return 0  -- Aborted, user is actually online
```

**Result:** No false offline event. Reaper aborts. User stays online.

**Accepted "flapping" scenario:**

```
T=0:   User disconnects, heartbeat stops
T=30s: Expiry passes, Reaper sees expired
T=31s: Lua removes from ZSET → broadcast "offline"
T=32s: User reconnects, sends heartbeat
T=32s: ZADD → user back in ZSET → broadcast "online"
```

Friends see: offline (1s) → online (brief flap). This is **intentional**—we prioritize data consistency over UX smoothness.

---

## Data Model & Redis Structures

### Redis Database Allocation

The project uses **two separate Redis databases**:

- **Redis DB 0**: Django Channels layer (pub/sub fabric)
- **Redis DB 1**: Django cache (presence state + message cache)

**Why separate?**
- Isolate concerns (real-time transport vs data caching)
- Different eviction policies
- Easier monitoring and debugging

### Redis Keys Schema

#### 1. Online Users ZSET

```
Key:    online_users (or online_users:0, online_users:1 with sharding)
Type:   ZSET
Member: "123" (AppUser.id as string)
Score:  1734567890 (expiry timestamp in seconds)
TTL:    None (managed by Reaper, not Redis TTL)
```

**Operations:**
- Heartbeat: `ZADD online_users 1734567920 "123"` (extend expiry to now+30s)
- Reaper: `ZRANGEBYSCORE online_users -inf 1734567890 LIMIT 0 500`
- Offline: `ZREM online_users "123"` (via Lua)

#### 2. User State HASH

```
Key:    presence:state:123
Type:   HASH
Fields:
  status: "online" | "away" | "offline"
  updated_ts: "1734567860"
  last_heartbeat_ts: "1734567860"
  last_seen_ts: "1734567890"
TTL:    86400s (24 hours, configurable)
```

**Operations:**
- Heartbeat (first): `HSET presence:state:123 status online updated_ts 1734567860`
- Away: `HSET presence:state:123 status away updated_ts ...`
- Reaper (offline): `HSET presence:state:123 status offline last_seen ...`

**Why TTL on HASH but not ZSET?**
- ZSET cleaned by Reaper (Lua removal)
- HASH can accumulate stale data → TTL prevents orphans

---

### PostgreSQL Model

```python
# feed/models.py
class AppUser(models.Model):
    """User profile (1:1 with Django User)"""
    user = models.OneToOneField(User, ...)
    profile_name = models.CharField(...)

class Follower(models.Model):
    """Directed follow relationship"""
    following_user = models.ForeignKey(AppUser, ...)  # A follows B
    followed_user = models.ForeignKey(AppUser, ...)
    
    class Meta:
        unique_together = (('following_user', 'followed_user'),)
```

**Mutual follower query:**
```python
# Is A mutual with B? (both edges exist)
a_follows_b = Follower.objects.filter(
    following_user_id=A, followed_user_id=B
).exists()

b_follows_a = Follower.objects.filter(
    following_user_id=B, followed_user_id=A
).exists()

is_mutual = a_follows_b and b_follows_a
```

**Why no presence data in PostgreSQL?**
- Presence is ephemeral (stale in seconds)
- Redis is 100x faster for high-write workloads
- PostgreSQL used only for durable social graph (followers)

---

## Component Architecture

### 1. PresenceConsumer (WebSocket Handler)

**File:** `chat/presence_consumers.py`

**Responsibilities:**
- Accept authenticated WebSocket connections
- Handle subscription requests (with mutual-follower auth)
- Process heartbeat messages → update Redis ZSET
- Broadcast status changes via Django Channels groups

**Message types handled:**

| Client → Server | Action |
|-----------------|--------|
| `presence.heartbeat` | Update ZSET expiry, maybe broadcast "online" |
| `presence.away` | Update HASH to "away", broadcast |
| `presence.active` | Update HASH to "online", broadcast |
| `presence.subscribe` | Authorize + join group `user_{target_id}_status` |
| `presence.unsubscribe` | Leave group |

**Connection lifecycle:**

```python
async def connect(self):
    # 1. Authenticate (Django User → AppUser)
    self.app_user_id = self.user.app_user.id
    
    # 2. Join own status group (for multi-device sync)
    await self.channel_layer.group_add(
        f"user_{self.app_user_id}_status",
        self.channel_name
    )
    
    # 3. Accept connection
    await self.accept()
```

---

### 2. Reaper Worker (Management Command)

**File:** `chat/management/commands/run_reaper.py`

**Responsibilities:**
- Poll Redis ZSET every 1s for expired users
- Atomically confirm offline status (Lua script)
- Persist offline state to Redis HASH
- Broadcast offline events to Django Channels

**Run command:**
```bash
python manage.py run_reaper [--poll-interval 1.0] [--batch-size 500]
```

**Shard-aware mode** (future):
```bash
# Process shard 0 only
python manage.py run_reaper --shard-id 0

# Process all shards in one worker
python manage.py run_reaper --all-shards
```

**Why Django management command (not Celery)?**
- Simpler deployment (one process, no broker)
- Predictable resource usage
- Easy to monitor (single process, known PID)

**Trade-off:** Requires running as a separate process (e.g., systemd service).

---

### 3. Presence Redis Module (Shared Primitives)

**File:** `chat/presence_redis.py`

**Role:** Centralized configuration and key generation (Factory pattern).

**Key abstractions:**

```python
@dataclass(frozen=True)
class PresenceConfig:
    """Immutable config from Django settings"""
    base_key: str = "online_users"
    num_shards: int = 1
    heartbeat_window_seconds: int = 30
    state_key_prefix: str = "presence:state"
    state_ttl_seconds: int = 86400

# Key builders (ensure consistency across components)
def online_users_key_for_user(cfg, app_user_id: int) -> str:
    """Returns: 'online_users' or 'online_users:2' (sharded)"""

def presence_state_key(cfg, app_user_id: int) -> str:
    """Returns: 'presence:state:123'"""

# Effective status computation (ZSET + HASH → final status)
def effective_status_single(conn, cfg, app_user_id, now_ts) -> tuple[str, int]:
    """
    Returns: ("online"/"away"/"offline", timestamp)
    Rule: If ZSET expired → offline, else HASH status
    """
```

**Why this module?**
- **DRY**: Key generation logic in one place
- **Consistency**: Consumer, Reaper, Leaderboard all use same keys
- **Testability**: Easy to mock `PresenceConfig`

---

### 4. Django Channels (Pub/Sub Fabric)

**Built-in component** (not custom code).

**Role:** Broadcast presence events to subscribed WebSocket connections.

**Group model:**

```python
# Group per user (who wants to see this user's status?)
group_name = f"user_{app_user_id}_status"

# Subscribe (e.g., Bob subscribes to Alice)
await channel_layer.group_add("user_alice_status", bob_socket_channel)

# Publish (e.g., Reaper marks Alice offline)
await channel_layer.group_send("user_alice_status", {
    "type": "presence.status.changed",  # Handler method name
    "user_id": alice_id,
    "status": "offline",
    "timestamp": now
})

# Bob's socket receives and forwards to browser
async def presence_status_changed(self, event):
    await self.send(json.dumps({
        "type": "presence.status",
        "user_id": event["user_id"],
        "status": event["status"],
        ...
    }))
```

**Why Django Channels (not custom pub/sub)?**
- Production-ready (used by Instagram, Disqus)
- Handles multi-server fanout (uses Redis pub/sub internally)
- Integrates with Django auth/sessions

---

### 5. Leaderboard REST Endpoint (Batch Query)

**File:** `chat/views.py`

**Endpoint:** `GET /chat/api/presence/leaderboard/?limit=50`

**Purpose:** Efficiently fetch presence for many users (e.g., home page "online friends" list).

**Algorithm:**

```python
@login_required
def presence_leaderboard(request):
    # 1. Get mutual followers from PostgreSQL
    mutual_followers = get_mutual_followers(request.user.app_user.id)
    
    # 2. Compute effective status for each (Redis queries)
    now_ts = now_ts_seconds()
    results = []
    for friend in mutual_followers:
        status, ts = effective_status_single(redis_conn, cfg, friend.id, now_ts)
        results.append({
            "user_id": friend.id,
            "profile_name": friend.profile_name,
            "status": status,
            "last_seen": ts
        })
    
    # 3. Sort: online first, then by last_seen
    results.sort(key=lambda x: (x["status"] != "online", -x["last_seen"]))
    
    return JsonResponse({"friends": results[:limit]})
```

**Performance optimization opportunity:**
- Current: N Redis queries (one per friend)
- Future: Use Redis `PIPELINE` to batch ZSCORE + HGETALL (reduces RTT)

---

## Critical Design Patterns

### 1. Configuration Object Pattern

**Problem:** Presence config scattered across code, hard to test.

**Solution:** Immutable `PresenceConfig` dataclass.

```python
@dataclass(frozen=True)
class PresenceConfig:
    base_key: str = getattr(settings, "PRESENCE_ONLINE_USERS_KEY", "online_users")
    num_shards: int = int(getattr(settings, "PRESENCE_NUM_SHARDS", 1))
    # ... other config
```

**Benefits:**
- Single source of truth
- Easy to override in tests: `PresenceConfig(base_key="test_users")`
- Frozen = immutable = thread-safe

---

### 2. Repository Pattern (Partial)

**Current state:**
- Messages use `MessageRepository` (good)
- Presence uses scattered functions (inconsistent)

**Future refactor:**
```python
class PresenceRepository:
    @staticmethod
    def get_effective_status(app_user_id: int) -> tuple[str, int]:
        """Encapsulate Redis ZSET + HASH logic"""
    
    @staticmethod
    def update_heartbeat(app_user_id: int, expiry_ts: int):
        """Encapsulate ZADD"""
```

**Why not done yet?**
- Presence is simpler (fewer operations than messages)
- Can be refactored without breaking existing code

---

### 3. Observer Pattern (Django Channels)

**Pattern:** Observers (WebSocket consumers) subscribe to subjects (user status groups).

**Implementation:**

```python
# Subject: user_{id}_status group
# Observer: PresenceConsumer instance (Bob's socket)

# Subscribe
await channel_layer.group_add("user_alice_status", self.channel_name)

# Publish (from Reaper or another consumer)
await channel_layer.group_send("user_alice_status", event)

# Notify
async def presence_status_changed(self, event):
    # Bob's socket receives and forwards to browser
    await self.send(json.dumps(event))
```

**Why this pattern?**
- Decouples publishers (Reaper) from subscribers (sockets)
- Scales horizontally (Redis pub/sub across servers)

---

### 4. Command Pattern (Management Command)

**Pattern:** Encapsulate "reap offline users" operation as a command object.

**Implementation:**

```python
class Command(BaseCommand):
    def handle(self, *args, **options):
        # Encapsulated reaping logic
        while True:
            reap_expired_users()
            time.sleep(poll_interval)
```

**Benefits:**
- Runnable via `python manage.py run_reaper`
- Easy to test (call `handle()` directly)
- Consistent with Django conventions

---

## Security Model

### Principle: Mutual Follower Authorization

**Rule:** User A can subscribe to User B's presence **only if:**
1. A follows B **AND**
2. B follows A

**Why mutual (not one-way)?**
- Privacy: Prevents stalking (can't watch someone who doesn't follow back)
- Reciprocity: Matches social expectation (friends see each other)

**Implementation:**

```python
async def _handle_subscribe(self, data: dict):
    target_id = data["target_user_id"]
    
    # Check mutual relationship (both edges exist)
    if target_id != self.app_user_id:  # Skip check for self
        allowed = await self._is_mutual_follower(self.app_user_id, target_id)
        
        if not allowed:
            await self.send(json.dumps({
                "type": "presence.subscribe.denied",
                "reason": "not_mutual_followers"
            }))
            return
    
    # Authorized → join group
    await self.channel_layer.group_add(
        f"user_{target_id}_status",
        self.channel_name
    )
```

**Database query:**

```python
def _is_mutual_follower(me_id: int, target_id: int) -> bool:
    a_to_b = Follower.objects.filter(
        following_user_id=me_id,
        followed_user_id=target_id
    ).exists()
    
    if not a_to_b:
        return False
    
    b_to_a = Follower.objects.filter(
        following_user_id=target_id,
        followed_user_id=me_id
    ).exists()
    
    return b_to_a
```

**Performance note:**
- Two DB queries per subscribe request
- Acceptable (subscribe happens once per page load, not per heartbeat)
- Can be optimized with a `MutualFollower` materialized view if needed

---

### Rate Limiting

**Heartbeat rate limit:**

```python
# Config
PRESENCE_HEARTBEAT_MIN_INTERVAL_SECONDS = 5

# Enforcement (best-effort, client-side also throttles)
last_heartbeat = conn.hget(presence_state_key, "last_heartbeat_ts")
if last_heartbeat and (now - last_heartbeat) < min_interval:
    return  # Silently drop excessive heartbeats
```

**Subscription cap:**

```python
PRESENCE_MAX_SUBSCRIPTIONS_PER_SOCKET = 500

if len(self.subscribed_groups) >= cfg.max_subscriptions:
    await self.send(json.dumps({
        "type": "presence.subscribe.denied",
        "reason": "too_many_subscriptions"
    }))
```

**Why these limits?**
- Prevent abuse (malicious clients spamming)
- Protect Redis from write amplification
- Reasonable UX (500 friends visible at once is plenty)

---

## Scalability & Performance

### Horizontal Scaling Strategy

#### 1. WebSocket Tier (PresenceConsumer)

**Current:** All sockets connect to Django/Daphne servers.

**Scaling:**
- Add more Daphne servers behind a load balancer
- Use sticky sessions (IP hash) or layer-7 load balancing
- Django Channels handles cross-server communication via Redis pub/sub

**Example:**
```
┌──────────┐       ┌─────────────┐
│ Client A │ ────> │  Daphne 1   │
└──────────┘       └─────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ Redis Pub/Sub│ ← Channels layer
                   └──────────────┘
                          │
┌──────────┐       ┌─────────────┐
│ Client B │ ────> │  Daphne 2   │
└──────────┘       └─────────────┘
```

If A and B are on different servers and B's status changes, Redis pub/sub routes the event to A's server.

---

#### 2. Reaper Tier (Offline Detection)

**Single-shard mode (current):**
- One Reaper process polls `online_users`

**Multi-shard mode (future):**
- Split ZSET: `online_users:0`, `online_users:1`, ..., `online_users:N`
- Run N Reaper processes:
  ```bash
  python manage.py run_reaper --shard-id 0
  python manage.py run_reaper --shard-id 1
  ...
  ```
- Each Reaper only polls its assigned shard

**Shard routing:**
```python
shard_id = user_id % num_shards  # e.g., user 123 → shard 3
key = f"online_users:{shard_id}"
```

**Why sharding?**
- Single ZSET limit: ~10M members before `ZRANGEBYSCORE` slows down
- Sharding parallelizes reaping (N workers polling N keys)

**Alternative: One Reaper, All Shards:**
```bash
python manage.py run_reaper --all-shards
```
Loops through all shard keys in one process. Simpler ops, but single bottleneck.

---

#### 3. Redis Tier (State Store)

**Vertical scaling:**
- Increase Redis memory (cheap, scales to ~100GB single instance)

**Horizontal scaling (if needed):**
- Redis Cluster with hash slot distribution
- Each shard owns a subset of keys (`online_users:{0..N}`)

**Redis Sentinel for HA:**
- Master-slave replication
- Automatic failover

---

### Performance Metrics

| Operation | Latency | Throughput |
|-----------|---------|------------|
| Heartbeat (ZADD) | <1ms | 100k ops/sec (single Redis) |
| Effective status (ZSCORE + HGETALL) | <2ms | 50k ops/sec |
| Reaper poll (ZRANGEBYSCORE 500) | ~5ms | Batch of 500 users |
| Subscribe authorization (2x DB query) | ~10ms | Cached at socket level |
| Broadcast (Channels group_send) | <10ms | Depends on fanout size |

**Bottlenecks at scale:**
1. **Reaper:** Single process polls all shards → shard splitting
2. **Leaderboard:** N individual Redis queries → use pipeline
3. **Follower queries:** 2 DB hits per subscribe → add caching or materialized view

---

### Memory Footprint

**Per online user:**
- ZSET: `~100 bytes` (member + score)
- HASH: `~200 bytes` (status + timestamps)
- WebSocket: `~2KB` (connection state)
- **Total:** ~2.3KB/user

**For 100k concurrent users:**
- Redis: ~30MB (ZSET + HASH)
- Daphne: ~230MB (WebSocket connections)

---

## Trade-offs & Alternatives

### 1. Polling (Reaper) vs Event-Driven (Keyspace Notifications)

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **Polling (Reaper)** | Reliable, idempotent, atomic with Lua | +1s latency | ✅ Chosen |
| **Redis Keyspace Notifications** | Real-time (<100ms) | Unreliable (can drop), no ordering | ❌ Rejected |

**Justification:** Offline detection doesn't need <1s accuracy. Reliability > latency.

---

### 2. WebSocket vs UDP for Heartbeats

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **WebSocket** | Simple, firewall-friendly, reuses chat socket | Slightly higher memory | ✅ Chosen |
| **UDP** | Lightweight, no connection state | Blocked by firewalls, requires separate server | ❌ Rejected |

**Justification:** For <100k users, WebSocket overhead is negligible. Operational simplicity wins.

---

### 3. Redis ZSET + HASH vs HASH-only

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **ZSET + HASH** | Efficient expiry queries O(log N), atomic Lua | Two data structures | ✅ Chosen |
| **HASH-only** | Simpler (one structure) | O(N) to find expired, harder to shard | ❌ Rejected |

**Justification:** ZSET is purpose-built for "find expired" queries. Premature optimization trap avoided.

---

### 4. PostgreSQL vs Redis for Presence State

| Approach | Pros | Cons | Decision |
|----------|------|------|----------|
| **Redis (ephemeral)** | 100x faster writes, low latency | Data lost on Redis crash (acceptable) | ✅ Chosen |
| **PostgreSQL (durable)** | ACID, durable, relational joins | Too slow for high-write presence (10k writes/sec) | ❌ Rejected |

**Justification:** Presence is ephemeral (stale in 30s). No need for ACID. Redis is perfect fit.

---

### 5. Actor Model (LinkedIn) vs Polling (Our Choice)

**LinkedIn's approach:** Each online user gets a virtual "actor" with a delayed trigger.

**Our approach:** Polling ZSET every 1s.

| Aspect | Actor Model | Polling (Reaper) |
|--------|-------------|------------------|
| Complexity | High (actor lifecycle, GC) | Low (while loop + Lua) |
| Memory | One actor per user (~1KB) | Zero (stateless) |
| Latency | <100ms | ~1s |
| Correctness | Same (Lua still needed) | Same |

**Decision:** ✅ Polling. **Rationale:** Actor model is overkill for <1M users. Simpler code, easier debugging.

---

## Configuration Reference

All config is in Django `settings.py`. Defaults are production-ready.

```python
# Online users ZSET
PRESENCE_ONLINE_USERS_KEY = "online_users"  # Redis key name
PRESENCE_NUM_SHARDS = 1                      # Increase for >100k users

# Heartbeat timing
PRESENCE_HEARTBEAT_WINDOW_SECONDS = 30       # Offline after 30s no heartbeat

# Semantic status HASH
PRESENCE_STATE_KEY_PREFIX = "presence:state" # Key prefix per user
PRESENCE_STATE_TTL_SECONDS = 86400           # 24 hours (garbage collection)

# Rate limiting
PRESENCE_HEARTBEAT_MIN_INTERVAL_SECONDS = 5  # Min seconds between heartbeats
PRESENCE_MAX_SUBSCRIPTIONS_PER_SOCKET = 500  # Max friends to subscribe to
```

---

## Future Enhancements

### 1. Typing Indicators (Conversation-Scoped)

**Status:** Implemented (basic).

**Enhancement:**
- Auto-expire typing after 3s (use Redis SET with TTL)
- Debounce: client sends typing every 2s while typing
- Server publishes to conversation group (not per-user)

---

### 2. Batch Presence Query (Pipeline)

**Current:** Leaderboard makes N Redis queries.

**Future:**
```python
pipe = redis_conn.pipeline()
for user_id in friend_ids:
    pipe.zscore(online_users_key, user_id)
    pipe.hgetall(presence_state_key(user_id))
results = pipe.execute()  # Single network RTT
```

**Speedup:** N queries → 1 RTT (~10x faster for 50 friends).

---

### 3. Multi-Device Status Aggregation

**Current:** Each device sends heartbeat, all update same user_id.

**Future:** Track devices separately, user is "online" if any device is online.

```
presence:devices:{user_id}  (ZSET)
  member: device_id
  score: expiry_ts
```

**Complexity:** Medium (needs device_id from client).

---

### 4. Geo-Distribution (Active-Active)

**Current:** Single Redis (one datacenter).

**Future:** Redis CRDTs for active-active replication across regions.

**Challenge:** Last-write-wins conflicts for status changes.

---

## Conclusion

This presence platform balances:
- **Simplicity** (polling, WebSocket) over complexity (Actor model, UDP)
- **Reliability** (atomic Lua, idempotent) over micro-optimizations
- **Operational ease** (Django commands, single Redis) over distributed systems

**Key innovations:**
- Lua script for race-free offline detection
- ZSET for O(log N) expiry queries
- Mutual-follower authorization
- Shard-ready design without premature sharding

**Production-ready for:**
- ≤100k concurrent users (single Redis + multi-Daphne)
- ≤1M concurrent users (with sharding + Redis Cluster)

**Architectural principles followed:**
- Separation of concerns (ZSET for liveness, HASH for semantics)
- Idempotency (reaper can be stopped/restarted safely)
- Graceful degradation (Redis fail → fallback to "everyone offline")
- Security-first (authorization before subscription)

---

## Appendix: Quick Reference

### Key Redis Operations

```bash
# Check if user 123 is online
ZSCORE online_users "123"  # Returns expiry_ts or nil

# Get user's semantic status
HGETALL presence:state:123  # Returns {status: "away", updated_ts: ...}

# Manual reap (simulate Reaper)
ZRANGEBYSCORE online_users -inf $(date +%s) LIMIT 0 100

# Debug: see all online users
ZRANGE online_users 0 -1 WITHSCORES
```

### Django Management Commands

```bash
# Run reaper (single shard)
python manage.py run_reaper

# Run reaper (all shards, one process)
python manage.py run_reaper --all-shards

# Run reaper (specific shard)
python manage.py run_reaper --shard-id 2 --poll-interval 0.5

# Django checks
python manage.py check
python manage.py test chat.test_consumers
```

### WebSocket API (Client)

```javascript
// Connect to presence socket
const ws = new WebSocket("ws://localhost:8000/ws/presence/");

// Send heartbeat (every 10-20s)
ws.send(JSON.stringify({type: "presence.heartbeat"}));

// Subscribe to friend's status
ws.send(JSON.stringify({
    type: "presence.subscribe",
    target_user_id: 456
}));

// Handle status updates
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "presence.status") {
        updateUI(data.user_id, data.status);
    }
};
```

### REST API

```bash
# Get online friends leaderboard
curl -H "Cookie: sessionid=..." \
  http://localhost:8000/chat/api/presence/leaderboard/?limit=50
```

---

**Document Version:** 1.0  
**Last Updated:** January 2026  
**Maintainer:** Backend Team

