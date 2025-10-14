# Django-Redis Integration Guide

## ✅ Yes, We're Using Django-Redis!

The caching implementation **fully leverages django-redis** with both high-level abstractions and direct Redis access.

---

## 🔄 How It Works

### Layer 1: Django Cache Framework (High-Level)
```python
from django.core.cache import cache

# This uses django-redis under the hood
cache.set('key', 'value', timeout=300)
cache.get('key')
```

### Layer 2: Django-Redis Specific Features (Mid-Level)
```python
from django.core.cache import cache

# django-redis specific methods
cache.delete_pattern('chat:v1:*')  # Pattern deletion
cache.ttl('my_key')                 # Get TTL
cache.persist('my_key')             # Remove expiration
cache.expire('my_key', 300)         # Set expiration
```

### Layer 3: Raw Redis Access (Low-Level)
```python
from django_redis import get_redis_connection

conn = get_redis_connection("default")
conn.keys('chat:*')        # Direct Redis commands
conn.info()                # Server info
conn.pipeline()            # Pipeline operations
```

---

## 📦 Our Implementation Stack

```
┌─────────────────────────────────────────┐
│         Application Code                │
│     (chat/cache.py, feed/cache.py)      │
└─────────────────┬───────────────────────┘
                  │
      ┌───────────┴───────────┐
      │                       │
┌─────▼──────────┐  ┌────────▼─────────┐
│ core/caching.py│  │ core/redis_utils │
│                │  │      .py         │
│ • Strategies   │  │ • RedisClient    │
│ • KeyBuilder   │  │ • RedisLock      │
│ • Serializer   │  │ • RedisPipeline  │
└─────┬──────────┘  └────────┬─────────┘
      │                      │
      └──────────┬───────────┘
                 │
      ┌──────────▼──────────┐
      │ django.core.cache   │
      │   (uses backend)    │
      └──────────┬──────────┘
                 │
      ┌──────────▼──────────┐
      │   django-redis      │
      │ (RedisCache backend)│
      └──────────┬──────────┘
                 │
      ┌──────────▼──────────┐
      │   redis-py client   │
      └──────────┬──────────┘
                 │
      ┌──────────▼──────────┐
      │    Redis Server     │
      │ (localhost:6379/1)  │
      └─────────────────────┘
```

---

## 🎯 Django-Redis Features We Use

### 1. **Pattern Deletion** ✅
```python
from django.core.cache import cache

# Delete all keys matching pattern
cache.delete_pattern('chat:v1:conversation:*')
cache.delete_pattern('feed:v1:user:123:*')
```

**Used in:**
- `core/caching.py` → `CacheManager.clear_namespace()`
- `core/redis_utils.py` → `RedisClient.delete_pattern()`

---

### 2. **TTL Operations** ✅
```python
# Get time-to-live
ttl = cache.ttl('my_key')  # Returns seconds remaining

# Set expiration on existing key
cache.expire('my_key', 300)  # 5 minutes

# Remove expiration (make permanent)
cache.persist('my_key')
```

**Used in:**
- `core/redis_utils.py` → `RedisClient.ttl()`, `expire()`, `persist()`

---

### 3. **Atomic Operations** ✅
```python
# Increment/decrement atomically
cache.incr('view_count')         # Increment by 1
cache.incr('view_count', 10)     # Increment by 10
cache.decr('stock_count')        # Decrement by 1
```

**Used in:**
- `core/redis_utils.py` → `RedisCounter` class

---

### 4. **Distributed Locks** ✅
```python
from django.core.cache import cache

# Acquire lock (prevents race conditions)
with cache.lock('update_feed:123', timeout=10):
    # Only one process can execute this at a time
    expensive_operation()
```

**Used in:**
- `core/redis_utils.py` → `RedisLock` class
- Prevents thundering herd problems

---

### 5. **Direct Redis Connection** ✅
```python
from django_redis import get_redis_connection

conn = get_redis_connection("default")

# Raw Redis commands
conn.keys('chat:*')
conn.scan(match='feed:*', count=100)
conn.info()
conn.pipeline()
```

**Used in:**
- `core/caching.py` → `CacheManager.get_stats()`
- `core/redis_utils.py` → `RedisClient` class

---

### 6. **Pipeline Operations** ✅
```python
from django_redis import get_redis_connection

conn = get_redis_connection("default")

# Batch multiple operations
pipe = conn.pipeline()
pipe.set('key1', 'value1')
pipe.set('key2', 'value2')
pipe.incr('counter')
results = pipe.execute()
```

**Used in:**
- `core/redis_utils.py` → `RedisPipeline` class

---

## 🚀 Advanced Features Available

### 1. RedisClient (Direct Redis Access)
```python
from core.redis_utils import RedisClient

client = RedisClient()

# Ping Redis
client.ping()  # True/False

# Get keys (blocking - dev only)
keys = client.keys('chat:*')

# Scan keys (non-blocking - production safe)
for key in client.scan('chat:*', count=100):
    print(key)

# Memory stats
stats = client.get_memory_stats()
print(stats['used_memory'])
```

### 2. RedisLock (Distributed Locking)
```python
from core.redis_utils import RedisLock

# Context manager
with RedisLock('update_feed:123', timeout=10):
    # Critical section - only one process at a time
    update_user_feed(123)

# Manual acquire/release
lock = RedisLock('compute_expensive_data')
if lock.acquire(blocking=True, blocking_timeout=5):
    try:
        result = expensive_computation()
    finally:
        lock.release()

# Decorator
from core.redis_utils import with_lock

@with_lock('update_reactions', timeout=5)
def update_post_reactions(post_id):
    # Automatically locked
    pass
```

### 3. RedisPipeline (Batch Operations)
```python
from core.redis_utils import RedisPipeline

pipeline = RedisPipeline()

# Add operations
pipeline.add_operation(lambda p: p.set('key1', 'val1'))
pipeline.add_operation(lambda p: p.set('key2', 'val2'))
pipeline.add_operation(lambda p: p.incr('counter'))

# Execute all at once
results = pipeline.execute()
```

### 4. RedisCounter (Atomic Counters)
```python
from core.redis_utils import RedisCounter

# View counter
views = RedisCounter('post:123:views')
views.increment()          # +1
views.increment(10)        # +10
views.decrement()          # -1
count = views.get()        # Get current value

# Reaction counter
reactions = RedisCounter('post:123:reactions')
reactions.increment()
reactions.get()           # Returns count
```

### 5. Thundering Herd Prevention
```python
from core.redis_utils import get_or_lock

# Only one process computes, others wait
def compute_expensive_feed(user_id):
    # Expensive DB query
    return Post.objects.filter(...)[:20]

feed = get_or_lock(
    key=f'feed:user:{user_id}',
    compute_func=lambda: compute_expensive_feed(user_id),
    timeout=3600,      # Cache for 1 hour
    lock_timeout=10    # Lock for 10 seconds
)
```

---

## 📋 Configuration Deep Dive

### settings.py Configuration
```python
CACHES = {
    "default": {
        # django-redis backend
        "BACKEND": "django_redis.cache.RedisCache",
        
        # Redis connection URL
        "LOCATION": "redis://127.0.0.1:6379/1",
        
        # Options
        "OPTIONS": {
            # Client class (default is best for most cases)
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            
            # Connection pool settings (optional)
            "CONNECTION_POOL_KWARGS": {
                "max_connections": 50,
                "retry_on_timeout": True,
            },
            
            # Parser (optional, for performance)
            # "PARSER_CLASS": "redis.connection.HiredisParser",
            
            # Serializer (optional, we use JSON in code)
            # "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
            
            # Key prefix (optional)
            # "KEY_PREFIX": "themoon",
        },
        
        # Key function (optional, for custom key generation)
        # "KEY_FUNCTION": "path.to.custom_key_func",
        
        # Default timeout in seconds (None = no expiry)
        "TIMEOUT": None,
    }
}
```

---

## 🔍 Debugging & Monitoring

### Check Django-Redis is Active
```python
from django.core.cache import cache
from django_redis import get_redis_connection

# Method 1: Check backend
print(cache.__class__)  # <class 'django_redis.cache.RedisCache'>

# Method 2: Try django-redis specific method
try:
    cache.delete_pattern('test:*')
    print("✅ django-redis is active")
except AttributeError:
    print("❌ Not using django-redis")

# Method 3: Get Redis connection
try:
    conn = get_redis_connection("default")
    print(f"✅ Connected to Redis: {conn.ping()}")
except Exception as e:
    print(f"❌ Redis error: {e}")
```

### Monitor Redis
```python
from core.redis_utils import RedisClient

client = RedisClient()

# Server info
info = client.get_info()
print(f"Redis version: {info['redis_version']}")
print(f"Connected clients: {info['connected_clients']}")

# Memory stats
memory = client.get_memory_stats()
print(f"Used memory: {memory['used_memory']}")
print(f"Fragmentation: {memory['mem_fragmentation_ratio']}")

# Key count
print(f"Total keys: {client.connection.dbsize()}")
```

---

## 🎓 When to Use Each Layer

### Use `core/caching.py` (Strategy Pattern) When:
✅ Building domain-specific cache logic  
✅ Need serialization/deserialization  
✅ Want consistent key naming  
✅ Need different cache patterns (write-through, cache-aside)

**Example:**
```python
from core.caching import CacheAsideStrategy

class UserFeedCache(CacheAsideStrategy):
    namespace = "feed"
    default_ttl = 3600
```

### Use `core/redis_utils.py` (Django-Redis) When:
✅ Need distributed locking  
✅ Need atomic counters  
✅ Need pipeline operations  
✅ Need direct Redis commands  
✅ Need to prevent thundering herd

**Example:**
```python
from core.redis_utils import RedisLock, RedisCounter

with RedisLock('update_post:123'):
    counter = RedisCounter('post:123:views')
    counter.increment()
```

### Use Raw Django Cache When:
✅ Simple key-value operations  
✅ Quick prototyping  
✅ No special requirements

**Example:**
```python
from django.core.cache import cache

cache.set('simple_key', 'simple_value', 300)
```

---

## 🚀 Real-World Examples

### Example 1: Chat Message Caching with Lock
```python
from core.caching import WriteThroughCacheStrategy
from core.redis_utils import RedisLock

class MessageCache(WriteThroughCacheStrategy):
    namespace = "chat"
    default_ttl = 1800
    
    def add_message(self, conv_id, message_data):
        # Use lock to prevent race conditions
        lock_key = f'conversation:{conv_id}:add_message'
        
        with RedisLock(lock_key, timeout=5):
            messages = self.get('conversation', conv_id, 'messages', default=[])
            messages.insert(0, message_data)
            messages = messages[:50]
            
            def persist(data):
                Message.objects.create(**message_data)
            
            self.write('conversation', conv_id, 'messages',
                      value=messages, persist_func=persist)
```

### Example 2: Reaction Count with Atomic Counter
```python
from core.redis_utils import RedisCounter

def like_post(post_id, user_id):
    # Atomic increment
    counter = RedisCounter(f'post:{post_id}:reactions')
    new_count = counter.increment()
    
    # Async save to DB
    Reaction.objects.get_or_create(post_id=post_id, user_id=user_id)
    
    return new_count

def unlike_post(post_id, user_id):
    # Atomic decrement
    counter = RedisCounter(f'post:{post_id}:reactions')
    new_count = counter.decrement()
    
    # Async delete from DB
    Reaction.objects.filter(post_id=post_id, user_id=user_id).delete()
    
    return new_count
```

### Example 3: Feed Generation with Thundering Herd Prevention
```python
from core.redis_utils import get_or_lock

def get_user_feed(user_id, page=1):
    key = f'feed:user:{user_id}:page:{page}'
    
    def compute_feed():
        # Expensive query
        followed = Follower.objects.filter(
            following_user_id=user_id
        ).values_list('followed_user_id', flat=True)
        
        return Post.objects.filter(
            created_by_user_id__in=followed
        ).order_by('-created_datetime')[:20]
    
    # Only one process computes, others wait
    return get_or_lock(
        key=key,
        compute_func=compute_feed,
        timeout=3600,
        lock_timeout=10
    )
```

---

## ⚠️ Important Notes

1. **Django-Redis IS Active** ✅  
   All cache operations use `django-redis` backend

2. **Two Redis Databases**:
   - DB 0: Channel Layers (WebSockets)
   - DB 1: Django Cache (our caching)

3. **Serialization**:
   - We use JSON serialization in code
   - Django-redis can use pickle (faster) or JSON (readable)

4. **Performance**:
   - Use pipelines for bulk operations
   - Use locks sparingly (they're blocking)
   - Use scan() instead of keys() in production

5. **Monitoring**:
   - Watch memory usage
   - Monitor cache hit rates
   - Alert on connection failures

---

## 📊 Feature Comparison

| Feature | Django Cache | django-redis | Our Abstraction |
|---------|-------------|--------------|-----------------|
| get/set | ✅ | ✅ | ✅ |
| delete_pattern | ❌ | ✅ | ✅ |
| TTL operations | ❌ | ✅ | ✅ |
| Atomic incr/decr | ✅ | ✅ | ✅ |
| Locks | ❌ | ✅ | ✅ |
| Pipelines | ❌ | ✅ | ✅ |
| Key namespacing | ❌ | ❌ | ✅ |
| Serialization | Basic | Flexible | JSON |
| Strategies | ❌ | ❌ | ✅ |
| Type hints | ❌ | Partial | ✅ |

---

**Summary**: Yes, we're fully using `django-redis`! You have access to all its features through both high-level abstractions (`core/caching.py`) and low-level utilities (`core/redis_utils.py`). 🚀


