"""
Core caching module providing base abstractions for domain-specific cache strategies.

This module implements a Strategy Pattern for flexible caching operations across
chat and feed domains, with a centralized CacheManager for Redis operations.
"""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional, Union, List, Dict
from datetime import timedelta

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder


logger = logging.getLogger(__name__)


class CacheKeyBuilder:
    """
    Centralized key generation with namespace support.
    
    Provides consistent key formatting across the application:
    - Prevents key collisions
    - Supports versioning
    - Easy to debug and monitor
    """
    
    SEPARATOR = ":"
    VERSION = "v1"
    
    @classmethod
    def build(cls, namespace: str, *parts: Union[str, int], version: Optional[str] = None) -> str:
        """
        Build a cache key with namespace and optional version.
        
        Args:
            namespace: Domain namespace (e.g., 'chat', 'feed', 'user')
            parts: Key components (e.g., 'conversation', conv_id, 'messages')
            version: Optional version override
            
        Returns:
            Formatted cache key (e.g., 'chat:v1:conversation:123:messages')
            
        Example:
            >>> CacheKeyBuilder.build('chat', 'conversation', 123, 'messages')
            'chat:v1:conversation:123:messages'
        """
        version = version or cls.VERSION
        key_parts = [namespace, version] + [str(part) for part in parts]
        return cls.SEPARATOR.join(key_parts)
    
    @classmethod
    def pattern(cls, namespace: str, *parts: Union[str, int]) -> str:
        """
        Build a key pattern for bulk operations (e.g., delete_pattern).
        
        Args:
            namespace: Domain namespace
            parts: Key components, use '*' for wildcards
            
        Returns:
            Pattern string for matching multiple keys
            
        Example:
            >>> CacheKeyBuilder.pattern('chat', 'conversation', '*', 'messages')
            'chat:v1:conversation:*:messages'
        """
        return cls.build(namespace, *parts)


class CacheSerializer:
    """
    Handles serialization/deserialization for cache storage.
    
    Supports:
    - JSON (default)
    - Django model instances
    - Complex nested structures
    """
    
    @staticmethod
    def serialize(data: Any) -> str:
        """Serialize data to JSON string using Django's encoder."""
        try:
            return json.dumps(data, cls=DjangoJSONEncoder)
        except (TypeError, ValueError) as e:
            logger.error(f"Serialization error: {e}")
            raise
    
    @staticmethod
    def deserialize(data: str) -> Any:
        """Deserialize JSON string back to Python object."""
        try:
            return json.loads(data)
        except (TypeError, ValueError) as e:
            logger.error(f"Deserialization error: {e}")
            raise


class BaseCacheStrategy(ABC):
    """
    Abstract base class for cache strategies.
    
    Implements the Strategy Pattern, allowing different caching behaviors
    (write-through, cache-aside, write-behind) for different domains.
    
    Subclasses must implement:
    - namespace: Domain-specific namespace
    - default_ttl: Default time-to-live for cached items
    - _fetch_from_source: How to retrieve data from the primary source (DB)
    """
    
    namespace: str = ""  # Must be overridden (e.g., 'chat', 'feed')
    default_ttl: int = 3600  # 1 hour default
    
    def __init__(self, serialize: bool = True):
        """
        Initialize cache strategy.
        
        Args:
            serialize: Whether to JSON-serialize data (True for complex objects)
        """
        self.serialize = serialize
        if not self.namespace:
            raise ValueError(f"{self.__class__.__name__} must define a 'namespace' attribute")
    
    def _build_key(self, *parts: Union[str, int]) -> str:
        """Build a namespaced cache key."""
        return CacheKeyBuilder.build(self.namespace, *parts)
    
    def _serialize(self, data: Any) -> Any:
        """Conditionally serialize data."""
        return CacheSerializer.serialize(data) if self.serialize else data
    
    def _deserialize(self, data: Any) -> Any:
        """Conditionally deserialize data."""
        if data is None:
            return None
        return CacheSerializer.deserialize(data) if self.serialize else data
    
    def get(self, *key_parts: Union[str, int], default: Any = None) -> Optional[Any]:
        """
        Retrieve value from cache.
        
        Args:
            key_parts: Components to build the cache key
            default: Value to return if key doesn't exist
            
        Returns:
            Cached value or default
        """
        key = self._build_key(*key_parts)
        try:
            cached = cache.get(key)
            if cached is None:
                logger.debug(f"Cache miss: {key}")
                return default
            logger.debug(f"Cache hit: {key}")
            return self._deserialize(cached)
        except Exception as e:
            logger.error(f"Cache get error for {key}: {e}")
            return default
    
    def set(
        self, 
        *key_parts: Union[str, int],
        value: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """
        Store value in cache.
        
        Args:
            key_parts: Components to build the cache key
            value: Data to cache
            ttl: Time-to-live in seconds (uses default_ttl if None)
            
        Returns:
            True if successful, False otherwise
        """
        key = self._build_key(*key_parts)
        ttl = ttl if ttl is not None else self.default_ttl
        
        try:
            serialized = self._serialize(value)
            cache.set(key, serialized, timeout=ttl)
            logger.debug(f"Cache set: {key} (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.error(f"Cache set error for {key}: {e}")
            return False
    
    def delete(self, *key_parts: Union[str, int]) -> bool:
        """
        Remove value from cache.
        
        Args:
            key_parts: Components to build the cache key
            
        Returns:
            True if successful, False otherwise
        """
        key = self._build_key(*key_parts)
        try:
            cache.delete(key)
            logger.debug(f"Cache delete: {key}")
            return True
        except Exception as e:
            logger.error(f"Cache delete error for {key}: {e}")
            return False
    
    def get_many(self, *key_parts_list: List[Union[str, int]]) -> Dict[str, Any]:
        """
        Retrieve multiple values from cache.
        
        Args:
            key_parts_list: List of key component tuples
            
        Returns:
            Dictionary mapping keys to values
            
        Example:
            >>> strategy.get_many(
            ...     ['conversation', 1, 'messages'],
            ...     ['conversation', 2, 'messages']
            ... )
        """
        keys = [self._build_key(*parts) for parts in key_parts_list]
        try:
            results = cache.get_many(keys)
            return {
                k: self._deserialize(v) 
                for k, v in results.items()
            }
        except Exception as e:
            logger.error(f"Cache get_many error: {e}")
            return {}
    
    def set_many(self, mapping: Dict[tuple, Any], ttl: Optional[int] = None) -> bool:
        """
        Store multiple values in cache.
        
        Args:
            mapping: Dict where keys are tuples of key_parts, values are data
            ttl: Time-to-live in seconds
            
        Returns:
            True if successful
            
        Example:
            >>> strategy.set_many({
            ...     ('conversation', 1, 'messages'): messages_1,
            ...     ('conversation', 2, 'messages'): messages_2,
            ... })
        """
        ttl = ttl if ttl is not None else self.default_ttl
        try:
            cache_mapping = {
                self._build_key(*key_parts): self._serialize(value)
                for key_parts, value in mapping.items()
            }
            cache.set_many(cache_mapping, timeout=ttl)
            logger.debug(f"Cache set_many: {len(cache_mapping)} items (TTL: {ttl}s)")
            return True
        except Exception as e:
            logger.error(f"Cache set_many error: {e}")
            return False
    
    def invalidate(self, *key_parts: Union[str, int]) -> bool:
        """
        Invalidate cache entry (alias for delete).
        
        More semantic for cache-aside pattern where you want to force refresh.
        """
        return self.delete(*key_parts)
    
    def get_or_set(
        self,
        *key_parts: Union[str, int],
        default_func: callable = None,
        ttl: Optional[int] = None
    ) -> Optional[Any]:
        """
        Retrieve from cache or compute and store if missing.
        
        Implements cache-aside pattern.
        
        Args:
            key_parts: Components to build the cache key
            default_func: Callable that returns the value if cache misses
            ttl: Time-to-live in seconds
            
        Returns:
            Cached or computed value
            
        Example:
            >>> strategy.get_or_set(
            ...     'user', user_id, 'feed',
            ...     default_func=lambda: fetch_user_feed(user_id)
            ... )
        """
        # Try cache first
        cached = self.get(*key_parts)
        if cached is not None:
            return cached
        
        # Cache miss - compute value
        if default_func is None:
            return None
        
        try:
            value = default_func()
            if value is not None:
                self.set(*key_parts, value=value, ttl=ttl)
            return value
        except Exception as e:
            logger.error(f"Error in get_or_set default_func: {e}")
            return None
    
    @abstractmethod
    def _fetch_from_source(self, *args, **kwargs) -> Any:
        """
        Fetch data from the primary source (database).
        
        Must be implemented by subclasses to define how to retrieve
        data when cache misses occur.
        """
        pass


class WriteThroughCacheStrategy(BaseCacheStrategy):
    """
    Write-through cache strategy.
    
    Writes go to both cache and DB synchronously.
    Best for: Chat messages, real-time data requiring strong consistency.
    
    Characteristics:
    - Cache is always consistent with DB
    - Higher write latency
    - Read performance is excellent
    """
    
    def write(self, *key_parts: Union[str, int], value: Any, persist_func: callable) -> bool:
        """
        Write data to both cache and database.
        
        Args:
            key_parts: Cache key components
            value: Data to write
            persist_func: Function that persists data to DB
            
        Returns:
            True if both operations succeed
        """
        try:
            # Write to DB first
            persist_func(value)
            
            # Then update cache
            self.set(*key_parts, value=value)
            
            logger.debug(f"Write-through complete: {self._build_key(*key_parts)}")
            return True
        except Exception as e:
            logger.error(f"Write-through error: {e}")
            # Invalidate cache to maintain consistency
            self.delete(*key_parts)
            return False


class CacheAsideStrategy(BaseCacheStrategy):
    """
    Cache-aside (lazy loading) strategy.
    
    Application code manages cache explicitly.
    Best for: User feeds, post details, read-heavy data with stale tolerance.
    
    Characteristics:
    - Cache populated on demand
    - Resilient to cache failures
    - Potential for stale data
    """
    
    def fetch(self, *key_parts: Union[str, int], fetch_func: callable, ttl: Optional[int] = None) -> Optional[Any]:
        """
        Fetch data using cache-aside pattern.
        
        Args:
            key_parts: Cache key components
            fetch_func: Function to fetch from DB if cache misses
            ttl: Time-to-live in seconds
            
        Returns:
            Data from cache or DB
        """
        return self.get_or_set(*key_parts, default_func=fetch_func, ttl=ttl)


class CacheManager:
    """
    Centralized cache management singleton.
    
    Provides high-level operations and utilities for cache strategies.
    Use this for operations that span multiple strategies or need
    administrative access to the cache backend.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @staticmethod
    def clear_namespace(namespace: str) -> bool:
        """
        Clear all keys in a namespace.
        
        Warning: Use with caution in production.
        
        Args:
            namespace: Namespace to clear (e.g., 'chat', 'feed')
            
        Returns:
            True if successful
        """
        try:
            # Note: Django's cache.delete_pattern requires django-redis backend
            pattern = CacheKeyBuilder.pattern(namespace, '*')
            cache.delete_pattern(pattern)
            logger.info(f"Cleared cache namespace: {namespace}")
            return True
        except AttributeError:
            logger.warning("delete_pattern not supported by current cache backend")
            return False
        except Exception as e:
            logger.error(f"Error clearing namespace {namespace}: {e}")
            return False
    
    @staticmethod
    def get_stats() -> Dict[str, Any]:
        """
        Get cache statistics (if supported by backend).
        
        Returns:
            Dictionary of cache stats
        """
        try:
            # Redis-specific stats
            from django_redis import get_redis_connection
            conn = get_redis_connection("default")
            info = conn.info()
            return {
                'used_memory': info.get('used_memory_human'),
                'connected_clients': info.get('connected_clients'),
                'total_keys': conn.dbsize(),
            }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {}
    
    @staticmethod
    def health_check() -> bool:
        """
        Check if cache backend is responsive.
        
        Returns:
            True if cache is healthy
        """
        try:
            test_key = 'health_check'
            cache.set(test_key, 'ok', timeout=10)
            result = cache.get(test_key)
            cache.delete(test_key)
            return result == 'ok'
        except Exception as e:
            logger.error(f"Cache health check failed: {e}")
            return False

