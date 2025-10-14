# Core Caching Architecture

## 🏛️ High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Django Application Layer                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐              ┌──────────────┐                │
│  │  Chat App    │              │  Feed App    │                │
│  ├──────────────┤              ├──────────────┤                │
│  │ consumers.py │              │ views.py     │                │
│  │ models.py    │              │ models.py    │                │
│  │ cache.py     │◄────┐  ┌────►│ cache.py     │                │
│  └──────────────┘     │  │     └──────────────┘                │
│                       │  │                                       │
└───────────────────────┼──┼───────────────────────────────────────┘
                        │  │
                ┌───────┴──┴────────┐
                │                   │
       ┌────────▼────────┐ ┌────────▼────────┐
       │ Write-Through   │ │  Cache-Aside    │
       │    Strategy     │ │    Strategy     │
       └────────┬────────┘ └────────┬────────┘
                │                   │
                └──────────┬────────┘
                           │
               ┌───────────▼───────────┐
               │ BaseCacheStrategy     │
               ├───────────────────────┤
               │ • get()               │
               │ • set()               │
               │ • delete()            │
               │ • get_many()          │
               │ • set_many()          │
               │ • get_or_set()        │
               └───────────┬───────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
┌────────▼────────┐ ┌──────▼──────┐ ┌───────▼────────┐
│ CacheKeyBuilder │ │ Serializer  │ │ CacheManager   │
├─────────────────┤ ├─────────────┤ ├────────────────┤
│ • build()       │ │ • serialize │ │ • health_check │
│ • pattern()     │ │ • deserial. │ │ • get_stats    │
└─────────┬───────┘ └──────┬──────┘ └───────┬────────┘
          │                │                │
          └────────────────┴────────────────┘
                           │
                    ┌──────▼──────┐
                    │ Django Cache│
                    │  Framework  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │    Redis    │
                    │  (Backend)  │
                    └─────────────┘
```

---

## 🔄 Data Flow Patterns

### Pattern 1: Write-Through (Chat Messages)

```
User sends message
       │
       ▼
┌──────────────┐
│  Consumer    │ 1. Receive message
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ MessageCache │ 2. Call write()
│ (WriteThrough)│
└──────┬───────┘
       │
       ├──────────────────┐
       ▼                  ▼
┌─────────────┐    ┌──────────┐
│  Database   │    │  Redis   │ 3. Write to both
│   (SQLite)  │    │  Cache   │    (synchronously)
└─────────────┘    └──────────┘
       │                  │
       └────────┬─────────┘
                ▼
         ✅ Consistent data
```

### Pattern 2: Cache-Aside (User Feed)

```
User requests feed
       │
       ▼
┌──────────────┐
│  View/API    │ 1. Request feed
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  FeedCache   │ 2. Check cache
│ (CacheAside) │
└──────┬───────┘
       │
       ├─── Cache Hit? ───┐
       │                  │
       ▼ NO               ▼ YES
┌─────────────┐    ┌──────────┐
│  Database   │    │  Redis   │
│  Query      │    │  Return  │
└──────┬──────┘    └────┬─────┘
       │                │
       ▼                │
┌──────────────┐        │
│ Store in     │        │
│ Redis Cache  │        │
└──────┬───────┘        │
       │                │
       └────────┬───────┘
                ▼
         Return to user
```

---

## 🗂️ Namespace Organization

```
Redis Key Structure:
{namespace}:{version}:{entity}:{id}:{attribute}

Examples:

chat:v1:conversation:123:messages
chat:v1:conversation:123:participants
chat:v1:user:456:online
chat:v1:user:456:typing:789

feed:v1:user:123:feed:page_1
feed:v1:user:123:feed:page_2
feed:v1:post:789:detail
feed:v1:post:789:reactions_count
```

### Namespace Isolation Benefits:
- ✅ No key collisions between apps
- ✅ Easy to clear specific domains
- ✅ Version-controlled cache invalidation
- ✅ Debugging and monitoring

---

## 🎯 Strategy Pattern Implementation

```python
# Base Strategy (Abstract)
class BaseCacheStrategy(ABC):
    namespace: str
    default_ttl: int
    
    @abstractmethod
    def _fetch_from_source(self):
        pass

# Concrete Strategy 1: Write-Through
class WriteThroughCacheStrategy(BaseCacheStrategy):
    def write(self, *key_parts, value, persist_func):
        persist_func(value)  # DB first
        self.set(*key_parts, value=value)  # Then cache

# Concrete Strategy 2: Cache-Aside
class CacheAsideStrategy(BaseCacheStrategy):
    def fetch(self, *key_parts, fetch_func, ttl):
        return self.get_or_set(*key_parts, 
                               default_func=fetch_func, 
                               ttl=ttl)

# Domain-Specific Implementation
class ConversationMessagesCache(WriteThroughCacheStrategy):
    namespace = "chat"
    default_ttl = 1800
    
    def get_messages(self, conv_id):
        return self.get('conversation', conv_id, 'messages')
```

---

## 📊 Cache Decision Tree

```
┌─────────────────────────┐
│ Need to cache data?     │
└───────────┬─────────────┘
            │
            ▼
      ┌─────────┐
      │Real-time│  YES  ┌──────────────────┐
      │data?    ├───────►│ Write-Through    │
      └────┬────┘        │ (Chat messages)  │
           │             └──────────────────┘
           │ NO
           ▼
      ┌─────────┐
      │Ephemeral│  YES  ┌──────────────────┐
      │data?    ├───────►│ Base Strategy    │
      └────┬────┘        │ with short TTL   │
           │             │ (Online, Typing) │
           │ NO          └──────────────────┘
           ▼
      ┌─────────┐
      │Read     │  YES  ┌──────────────────┐
      │heavy?   ├───────►│ Cache-Aside      │
      └────┬────┘        │ (User feeds)     │
           │             └──────────────────┘
           │ NO
           ▼
      ┌─────────────────┐
      │ Don't cache     │
      │ (write-heavy)   │
      └─────────────────┘
```

---

## ⚙️ Configuration Layers

```
┌───────────────────────────────────────────┐
│         settings.py Configuration          │
├───────────────────────────────────────────┤
│                                           │
│ CHANNEL_LAYERS (Redis DB 0)              │
│   ├─ WebSocket pub/sub                   │
│   └─ Real-time messaging                 │
│                                           │
│ CACHES (Redis DB 1)                      │
│   ├─ Django cache framework              │
│   └─ Data caching                        │
│                                           │
└───────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────┐
│         core/caching.py Layer             │
├───────────────────────────────────────────┤
│                                           │
│ CacheKeyBuilder                           │
│   └─ Namespace: "{app}:v1:..."          │
│                                           │
│ BaseCacheStrategy                         │
│   ├─ default_ttl                         │
│   └─ serialize option                    │
│                                           │
│ Concrete Strategies                       │
│   ├─ WriteThroughCacheStrategy           │
│   └─ CacheAsideStrategy                  │
│                                           │
└───────────────────────────────────────────┘
         │
         ▼
┌───────────────────────────────────────────┐
│      App-Specific Cache Layer             │
├───────────────────────────────────────────┤
│                                           │
│ chat/cache.py                            │
│   ├─ ConversationMessagesCache           │
│   ├─ OnlineStatusCache                   │
│   └─ TypingIndicatorCache                │
│                                           │
│ feed/cache.py                            │
│   ├─ UserFeedCache                       │
│   ├─ PostDetailCache                     │
│   └─ ReactionCountCache                  │
│                                           │
└───────────────────────────────────────────┘
```

---

## 🔐 Key Design Principles

### 1. **Single Responsibility**
- `CacheKeyBuilder` → Key generation only
- `CacheSerializer` → Serialization only
- `BaseCacheStrategy` → Cache operations only

### 2. **Open/Closed Principle**
- Open for extension (new strategies)
- Closed for modification (base classes stable)

### 3. **Dependency Inversion**
- Depend on abstractions (`BaseCacheStrategy`)
- Not on concrete implementations

### 4. **Separation of Concerns**
- Core: How to cache (mechanisms)
- Apps: What to cache (domain logic)

### 5. **DRY (Don't Repeat Yourself)**
- Shared operations in base class
- Domain-specific logic in subclasses

---

## 🚦 Cache Invalidation Strategies

```
┌────────────────────────────────────────┐
│        When to Invalidate?             │
├────────────────────────────────────────┤
│                                        │
│ 1. On Write (Proactive)                │
│    ├─ User posts → invalidate feeds   │
│    ├─ User follows → invalidate feed  │
│    └─ New reaction → invalidate post  │
│                                        │
│ 2. On TTL Expiry (Passive)            │
│    ├─ Let cache expire naturally      │
│    └─ Lazy reload on next access      │
│                                        │
│ 3. On Event (Reactive)                │
│    ├─ User deletes post               │
│    ├─ User blocks someone             │
│    └─ Content moderation              │
│                                        │
└────────────────────────────────────────┘
```

---

## 📈 Performance Characteristics

| Strategy | Read Speed | Write Speed | Consistency | Use Case |
|----------|-----------|-------------|-------------|----------|
| Write-Through | ⚡⚡⚡ Fast | 🐌 Slower | 🔒 Strong | Chat messages |
| Cache-Aside | ⚡⚡⚡ Fast | ⚡⚡ Fast | 🔓 Eventual | User feeds |
| TTL-based | ⚡⚡⚡ Fast | ⚡⚡⚡ Fast | 🔓 Weak | Online status |

---

## 🎓 Advanced Patterns (Future)

### 1. **Cache Warming**
Pre-populate cache for popular content on startup.

### 2. **Stale-While-Revalidate**
Return stale data immediately, refresh in background.

### 3. **Cache Locking**
Prevent thundering herd with distributed locks.

### 4. **Multi-Level Caching**
Memory (L1) + Redis (L2) + DB (L3).

### 5. **Cache Compression**
Compress large objects before storing in Redis.

---

## 🔍 Monitoring Points

```python
# Key metrics to track:

1. Cache Hit Rate
   = cache_hits / (cache_hits + cache_misses)
   Target: > 80%

2. Average Response Time
   - With cache: < 10ms
   - Without cache: < 100ms

3. Cache Memory Usage
   Monitor: Redis used_memory
   Alert if: > 80% of maxmemory

4. Eviction Rate
   Monitor: evicted_keys
   Alert if: > 1000/min

5. Key Expiration Rate
   Monitor: expired_keys
   Ensure TTLs are working
```

---

## 📚 Further Reading

- **Django Caching**: https://docs.djangoproject.com/en/5.2/topics/cache/
- **Redis Best Practices**: https://redis.io/docs/manual/patterns/
- **Caching Strategies**: Martin Fowler's "Cache-Aside" pattern
- **Django Channels**: https://channels.readthedocs.io/

---

**Architecture Status**: ✅ Complete and ready for implementation

**Next Step**: Implement domain-specific strategies in `chat/cache.py`

