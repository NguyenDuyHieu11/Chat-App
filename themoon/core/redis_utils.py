"""
Django-Redis specific utilities and advanced features.

This module exposes django-redis specific functionality beyond the base
Django cache framework, including:
- Raw Redis commands
- Pipeline operations
- Atomic operations
- Lock mechanisms
- Pub/Sub (if needed beyond Channels)
"""

import logging
from typing import Any, List, Dict, Optional, Callable
from contextlib import contextmanager

from django.core.cache import cache
from django_redis import get_redis_connection


logger = logging.getLogger(__name__)


class RedisClient:
    """
    Wrapper for direct Redis operations using django-redis.
    
    Provides access to raw Redis commands and advanced features
    not available through Django's cache framework.
    """
    
    def __init__(self, alias: str = "default"):
        """
        Initialize Redis client.
        
        Args:
            alias: Cache alias from settings.CACHES (default: "default")
        """
        self.alias = alias
        self._connection = None
    
    @property
    def connection(self):
        """Get Redis connection (lazy loading)."""
        if self._connection is None:
            self._connection = get_redis_connection(self.alias)
        return self._connection
    
    def ping(self) -> bool:
        """Test Redis connection."""
        try:
            return self.connection.ping()
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False
    
    def keys(self, pattern: str = "*") -> List[bytes]:
        """
        Get all keys matching pattern.
        
        Warning: Use with caution in production (blocking operation).
        
        Args:
            pattern: Redis pattern (e.g., "chat:v1:*")
            
        Returns:
            List of matching keys
        """
        try:
            return self.connection.keys(pattern)
        except Exception as e:
            logger.error(f"Error getting keys for pattern {pattern}: {e}")
            return []
    
    def scan(self, pattern: str = "*", count: int = 100):
        """
        Iterate over keys using SCAN (non-blocking).
        
        Better than keys() for production use.
        
        Args:
            pattern: Redis pattern
            count: Hint for number of keys to return per iteration
            
        Yields:
            Key bytes
        """
        cursor = 0
        while True:
            cursor, keys = self.connection.scan(cursor=cursor, match=pattern, count=count)
            for key in keys:
                yield key
            if cursor == 0:
                break
    
    def delete_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern using django-redis.
        
        Args:
            pattern: Redis pattern (e.g., "chat:v1:conversation:*")
            
        Returns:
            Number of keys deleted
        """
        try:
            # django-redis provides optimized delete_pattern
            deleted = cache.delete_pattern(pattern)
            logger.info(f"Deleted {deleted} keys matching pattern: {pattern}")
            return deleted
        except Exception as e:
            logger.error(f"Error deleting pattern {pattern}: {e}")
            return 0
    
    def ttl(self, key: str) -> int:
        """
        Get time-to-live for a key.
        
        Args:
            key: Cache key
            
        Returns:
            TTL in seconds (-1 if no expiry, -2 if key doesn't exist)
        """
        try:
            # django-redis exposes ttl method
            return cache.ttl(key)
        except Exception as e:
            logger.error(f"Error getting TTL for {key}: {e}")
            return -2
    
    def persist(self, key: str) -> bool:
        """
        Remove expiration from a key (make it permanent).
        
        Args:
            key: Cache key
            
        Returns:
            True if successful
        """
        try:
            # django-redis exposes persist method
            return cache.persist(key)
        except Exception as e:
            logger.error(f"Error persisting key {key}: {e}")
            return False
    
    def expire(self, key: str, timeout: int) -> bool:
        """
        Set expiration on an existing key.
        
        Args:
            key: Cache key
            timeout: Timeout in seconds
            
        Returns:
            True if successful
        """
        try:
            # django-redis exposes expire method
            return cache.expire(key, timeout)
        except Exception as e:
            logger.error(f"Error setting expiration on {key}: {e}")
            return False
    
    def incr(self, key: str, delta: int = 1) -> int:
        """
        Increment a key's value atomically.
        
        Args:
            key: Cache key
            delta: Amount to increment
            
        Returns:
            New value after increment
        """
        try:
            return cache.incr(key, delta=delta)
        except Exception as e:
            logger.error(f"Error incrementing {key}: {e}")
            return 0
    
    def decr(self, key: str, delta: int = 1) -> int:
        """
        Decrement a key's value atomically.
        
        Args:
            key: Cache key
            delta: Amount to decrement
            
        Returns:
            New value after decrement
        """
        try:
            return cache.decr(key, delta=delta)
        except Exception as e:
            logger.error(f"Error decrementing {key}: {e}")
            return 0
    
    @contextmanager
    def pipeline(self):
        """
        Context manager for Redis pipeline operations.
        
        Pipelines batch multiple Redis commands for better performance.
        
        Example:
            >>> client = RedisClient()
            >>> with client.pipeline() as pipe:
            ...     pipe.set('key1', 'value1')
            ...     pipe.set('key2', 'value2')
            ...     pipe.execute()
        """
        pipe = self.connection.pipeline()
        try:
            yield pipe
        finally:
            pass
    
    def get_info(self) -> Dict[str, Any]:
        """
        Get Redis server information.
        
        Returns:
            Dictionary of Redis server stats
        """
        try:
            return self.connection.info()
        except Exception as e:
            logger.error(f"Error getting Redis info: {e}")
            return {}
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get memory-specific statistics.
        
        Returns:
            Dictionary of memory stats
        """
        info = self.get_info()
        return {
            'used_memory': info.get('used_memory_human'),
            'used_memory_peak': info.get('used_memory_peak_human'),
            'used_memory_rss': info.get('used_memory_rss_human'),
            'mem_fragmentation_ratio': info.get('mem_fragmentation_ratio'),
        }
    
    def flush_db(self) -> bool:
        """
        Flush current database (DANGER: deletes all keys).
        
        Use only in development/testing!
        
        Returns:
            True if successful
        """
        try:
            self.connection.flushdb()
            logger.warning("Flushed Redis database!")
            return True
        except Exception as e:
            logger.error(f"Error flushing database: {e}")
            return False


class RedisLock:
    """
    Distributed lock implementation using django-redis.
    
    Prevents race conditions and thundering herd problems.
    """
    
    def __init__(self, key: str, timeout: int = 10, alias: str = "default"):
        """
        Initialize distributed lock.
        
        Args:
            key: Lock key
            timeout: Lock timeout in seconds
            alias: Cache alias
        """
        self.key = f"lock:{key}"
        self.timeout = timeout
        self.alias = alias
        self._lock = None
    
    def acquire(self, blocking: bool = True, blocking_timeout: Optional[float] = None) -> bool:
        """
        Acquire the lock.
        
        Args:
            blocking: Whether to block until lock is available
            blocking_timeout: Max time to wait (None = wait forever)
            
        Returns:
            True if lock acquired
        """
        try:
            # django-redis provides lock mechanism
            self._lock = cache.lock(
                self.key,
                timeout=self.timeout,
                blocking_timeout=blocking_timeout if blocking else 0
            )
            return self._lock.acquire(blocking=blocking)
        except Exception as e:
            logger.error(f"Error acquiring lock {self.key}: {e}")
            return False
    
    def release(self) -> bool:
        """
        Release the lock.
        
        Returns:
            True if successful
        """
        try:
            if self._lock:
                self._lock.release()
                return True
            return False
        except Exception as e:
            logger.error(f"Error releasing lock {self.key}: {e}")
            return False
    
    def __enter__(self):
        """Context manager entry."""
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.release()


class RedisPipeline:
    """
    High-level pipeline wrapper for batch operations.
    
    Example:
        >>> pipeline = RedisPipeline()
        >>> pipeline.add_operation(lambda p: p.set('key1', 'val1'))
        >>> pipeline.add_operation(lambda p: p.set('key2', 'val2'))
        >>> pipeline.execute()
    """
    
    def __init__(self, alias: str = "default"):
        """
        Initialize pipeline.
        
        Args:
            alias: Cache alias
        """
        self.client = RedisClient(alias)
        self.operations: List[Callable] = []
    
    def add_operation(self, operation: Callable) -> 'RedisPipeline':
        """
        Add operation to pipeline.
        
        Args:
            operation: Callable that accepts pipeline as argument
            
        Returns:
            Self for chaining
        """
        self.operations.append(operation)
        return self
    
    def execute(self) -> List[Any]:
        """
        Execute all operations in pipeline.
        
        Returns:
            List of results
        """
        try:
            with self.client.pipeline() as pipe:
                for operation in self.operations:
                    operation(pipe)
                results = pipe.execute()
                logger.debug(f"Executed {len(self.operations)} operations in pipeline")
                return results
        except Exception as e:
            logger.error(f"Pipeline execution error: {e}")
            return []
        finally:
            self.operations.clear()


class RedisCounter:
    """
    Atomic counter using Redis INCR/DECR.
    
    Useful for:
    - Reaction counts
    - View counts
    - Rate limiting
    """
    
    def __init__(self, key: str, alias: str = "default"):
        """
        Initialize counter.
        
        Args:
            key: Counter key
            alias: Cache alias
        """
        self.key = key
        self.client = RedisClient(alias)
    
    def increment(self, amount: int = 1) -> int:
        """Increment counter and return new value."""
        return self.client.incr(self.key, delta=amount)
    
    def decrement(self, amount: int = 1) -> int:
        """Decrement counter and return new value."""
        return self.client.decr(self.key, delta=amount)
    
    def get(self) -> int:
        """Get current counter value."""
        try:
            value = cache.get(self.key)
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0
    
    def reset(self) -> bool:
        """Reset counter to 0."""
        return cache.set(self.key, 0)
    
    def set(self, value: int) -> bool:
        """Set counter to specific value."""
        return cache.set(self.key, value)


# Convenience functions
def with_lock(key: str, timeout: int = 10):
    """
    Decorator for functions that need distributed locking.
    
    Example:
        >>> @with_lock('update_feed', timeout=5)
        ... def update_user_feed(user_id):
        ...     # This will only run if lock is acquired
        ...     pass
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            lock = RedisLock(key, timeout=timeout)
            if lock.acquire(blocking=True, blocking_timeout=timeout):
                try:
                    return func(*args, **kwargs)
                finally:
                    lock.release()
            else:
                logger.warning(f"Could not acquire lock for {key}")
                return None
        return wrapper
    return decorator


def get_or_lock(key: str, compute_func: Callable, timeout: int = 300, lock_timeout: int = 10) -> Any:
    """
    Get cached value or compute with distributed lock (prevents thundering herd).
    
    Args:
        key: Cache key
        compute_func: Function to compute value if cache misses
        timeout: Cache timeout in seconds
        lock_timeout: Lock timeout in seconds
        
    Returns:
        Cached or computed value
    """
    # Try cache first
    cached = cache.get(key)
    if cached is not None:
        return cached
    
    # Cache miss - acquire lock to compute
    lock = RedisLock(f"compute:{key}", timeout=lock_timeout)
    if lock.acquire(blocking=True, blocking_timeout=lock_timeout):
        try:
            # Double-check cache (another process might have computed it)
            cached = cache.get(key)
            if cached is not None:
                return cached
            
            # Compute and cache
            value = compute_func()
            cache.set(key, value, timeout=timeout)
            return value
        finally:
            lock.release()
    else:
        # Could not acquire lock - wait and retry once
        import time
        time.sleep(0.1)
        return cache.get(key)


