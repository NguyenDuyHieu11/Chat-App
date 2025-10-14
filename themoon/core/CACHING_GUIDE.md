# Core Caching Module - Quick Reference Guide

## Overview

The `core.caching` module provides a **Strategy Pattern-based** caching system for Django applications using Redis as the backend.

---

## 🏗️ Architecture Components

### 1. **CacheKeyBuilder**
Generates consistent, namespaced cache keys.

```python
from core.caching import CacheKeyBuilder

# Build a key
key = CacheKeyBuilder.build('chat', 'conversation', 123, 'messages')
# Result: 'chat:v1:conversation:123:messages'

# Pattern for bulk operations
pattern = CacheKeyBuilder.pattern('chat', 'conversation', '*', 'messages')
# Result: 'chat:v1:conversation:*:messages'
```

---

### 2. **BaseCacheStrategy**
Abstract base class for implementing domain-specific cache strategies.

**Key Methods:**
- `get(*key_parts, default=None)` - Retrieve from cache
- `set(*key_parts, value, ttl=None)` - Store in cache
- `delete(*key_parts)` - Remove from cache
- `get_many(*key_parts_list)` - Bulk retrieval
- `set_many(mapping, ttl=None)` - Bulk storage
- `get_or_set(*key_parts, default_func, ttl=None)` - Cache-aside pattern
- `invalidate(*key_parts)` - Force cache refresh

**Required Attributes:**
- `namespace` (str) - Domain namespace (e.g., 'chat', 'feed')
- `default_ttl` (int) - Default time-to-live in seconds

---

### 3. **WriteThroughCacheStrategy**
For strong consistency - writes go to both DB and cache.

**Best for:**
- Chat messages
- Real-time data
- Data requiring immediate consistency

**Key Method:**
```python
write(*key_parts, value, persist_func)
```

---

### 4. **CacheAsideStrategy**
For lazy loading - cache populated on demand.

**Best for:**
- User feeds
- Post details
- Read-heavy data with stale tolerance

**Key Method:**
```python
fetch(*key_parts, fetch_func, ttl=None)
```

---

### 5. **CacheManager**
Singleton for administrative operations.

**Methods:**
- `health_check()` - Check cache connectivity
- `get_stats()` - Get Redis statistics
- `clear_namespace(namespace)` - Clear all keys in a namespace

---

## 📦 Implementation Pattern

### Step 1: Create Domain-Specific Strategy

Create `chat/cache.py`:

```python
from core.caching import WriteThroughCacheStrategy

class ConversationMessagesCache(WriteThroughCacheStrategy):
    namespace = "chat"
    default_ttl = 1800  # 30 minutes
    
    def _fetch_from_source(self, conversation_id: int):
        from chat.models import Message
        return Message.objects.filter(
            Conversation_id=conversation_id
        ).order_by('-created_datetime')[:50]
    
    def get_messages(self, conversation_id: int):
        return self.get('conversation', conversation_id, 'messages', default=[])
    
    def cache_messages(self, conversation_id: int, messages: list):
        return self.set('conversation', conversation_id, 'messages', value=messages)
```

### Step 2: Use in Views/Consumers

```python
from chat.cache import ConversationMessagesCache

# Initialize (can be at module level)
msg_cache = ConversationMessagesCache()

# Get cached messages
messages = msg_cache.get_messages(conversation_id=123)

# Invalidate when needed
msg_cache.invalidate('conversation', 123, 'messages')
```

---

## 🎯 Common Use Cases

### Use Case 1: Chat Messages (Write-Through)

```python
class MessageCache(WriteThroughCacheStrategy):
    namespace = "chat"
    default_ttl = 1800
    
    def add_message(self, conv_id, message_data):
        messages = self.get('conversation', conv_id, 'messages', default=[])
        messages.insert(0, message_data)
        messages = messages[:50]  # Keep recent 50
        
        def persist(data):
            # Save to DB
            Message.objects.create(**message_data)
        
        return self.write('conversation', conv_id, 'messages', 
                         value=messages, persist_func=persist)
```

### Use Case 2: User Feed (Cache-Aside)

```python
class UserFeedCache(CacheAsideStrategy):
    namespace = "feed"
    default_ttl = 3600
    
    def get_feed(self, user_id, page=1):
        def fetch_from_db():
            return Post.objects.filter(
                created_by_user__followers__following_user_id=user_id
            ).order_by('-created_datetime')[:20]
        
        return self.fetch('user', user_id, 'feed', f'page_{page}', 
                         fetch_func=fetch_from_db)
```

### Use Case 3: Online Status (Short TTL)

```python
class OnlineStatusCache(BaseCacheStrategy):
    namespace = "chat"
    default_ttl = 60  # 1 minute
    
    def mark_online(self, user_id):
        self.set('user', user_id, 'online', value=True)
    
    def is_online(self, user_id):
        return self.get('user', user_id, 'online', default=False)
```

---

## ⚙️ Configuration

Ensure these are in `settings.py`:

```python
# Redis for channel layers (WebSockets)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("localhost", 6379)],
        },
    },
}

# Redis for caching
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    }
}
```

---

## 🚀 Next Steps

1. **Implement Chat Caching** (`chat/cache.py`):
   - ConversationMessagesCache
   - OnlineUsersCache
   - TypingIndicatorCache

2. **Implement Feed Caching** (`feed/cache.py`):
   - UserFeedCache
   - PostDetailCache
   - ReactionCountCache

3. **Integrate with Consumers** (`chat/consumers.py`):
   - Load cached messages on connect
   - Update cache on new message
   - Broadcast to channel layer

4. **Integrate with Views** (`feed/views.py`):
   - Use cache-aside for feed endpoints
   - Invalidate on write operations

---

## 🔍 Debugging

```python
from core.caching import CacheManager

# Check cache health
manager = CacheManager()
print(manager.health_check())  # True/False

# Get Redis stats
print(manager.get_stats())

# Clear namespace (dev/testing only!)
# manager.clear_namespace('chat')
```

---

## 📊 Cache Strategy Decision Matrix

| Data Type | Strategy | TTL | Reason |
|-----------|----------|-----|--------|
| Chat Messages | Write-Through | 30 min | Strong consistency |
| User Feed | Cache-Aside | 1 hour | Stale tolerance OK |
| Post Details | Cache-Aside | 2 hours | Read-heavy |
| Online Status | Base (TTL) | 1 min | Ephemeral |
| Typing Indicator | Base (TTL) | 10 sec | Very ephemeral |
| Reaction Counts | Cache-Aside | 30 min | Stale tolerance OK |

---

## 🛡️ Best Practices

1. **Always handle cache failures gracefully** - Return default values or fetch from DB
2. **Use appropriate TTLs** - Balance freshness vs. load
3. **Invalidate proactively** - When data changes, clear cache
4. **Monitor cache hit rates** - Adjust strategies based on metrics
5. **Use namespaces** - Prevent key collisions between domains
6. **Serialize complex objects** - Use `serialize=True` for dicts/lists
7. **Avoid cache stampedes** - Consider implementing lock mechanisms for high-traffic keys

---

## 📝 Example Files

See `core/cache_examples.py` for comprehensive usage examples covering:
- Write-through patterns
- Cache-aside patterns
- Bulk operations
- WebSocket consumer integration
- Django view integration
- Administrative operations

