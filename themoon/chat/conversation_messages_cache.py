import json
import logging
from typing import Union, Optional, List, Dict
from datetime import datetime

from django_redis import get_redis_connection
from core.caching import WriteThroughCacheStrategy, CacheKeyBuilder
from chat.repository_layer.message_repo import MessageRepository


logger = logging.getLogger(__name__)


class ConversationMessagesCache(WriteThroughCacheStrategy):
    """
    True write-through cache for conversation messages using Redis LISTs.
    
    This implementation uses Redis LIST data structure for optimal performance:
    - Messages stored as individual list items (not monolithic JSON blob)
    - RPUSH for O(1) append operations
    - LTRIM to maintain sliding window of last N messages
    - Zero cache invalidations on writes
    
    Key format: chat:conversation_id
    Value type: Redis LIST of JSON-serialized message objects
    
    Performance characteristics:
    - Read: O(1) - single LRANGE operation
    - Write: O(1) - RPUSH + LTRIM
    - Memory: O(n) where n = limit (50 messages by default)
    """
    
    domain = "chat"
    default_ttl = 1800  # 30 minutes
    default_limit = 50  # Keep last 50 messages in cache

    def __init__(self):
        """Initialize with Redis connection."""
        super().__init__()
        try:
            self.redis_client = get_redis_connection("default")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self.redis_client = None

    def _build_key(self, conversation_id: int) -> str:
        """Build cache key for conversation: chat:123"""
        return CacheKeyBuilder.build(self.domain, str(conversation_id))
    
    def _fetch_from_source(self, conversation_id: int, limit: int = 50):
        """Fetch messages from database when cache is missed"""
        messages = MessageRepository.get_conversation_messages(conversation_id, limit)
        return MessageRepository.serialize_message(messages)
    
    def _serialize_message(self, message_data: dict) -> str:
        """Serialize a single message to JSON string."""
        try:
            # Add timestamp if not present
            if 'created_datetime' not in message_data:
                message_data['created_datetime'] = datetime.utcnow().isoformat()
            return json.dumps(message_data)
        except (TypeError, ValueError) as e:
            logger.error(f"Error serializing message: {e}")
            raise
    
    def _deserialize_message(self, message_str: str) -> Optional[dict]:
        """Deserialize a single message from JSON string."""
        try:
            return json.loads(message_str)
        except (TypeError, ValueError) as e:
            logger.error(f"Error deserializing message: {e}")
            return None
    
    def get_conversation_messages(self, conversation_id: int, limit: int = 50) -> List[Dict]:
        """
        Get messages from Redis LIST or fallback to database.
        
        Uses LRANGE to fetch all messages from the list in a single operation.
        If cache miss, populates cache from database.
        
        Args:
            conversation_id: ID of the conversation
            limit: Maximum number of messages to return
            
        Returns:
            List of message dictionaries (oldest first)
        """
        if not self.redis_client:
            logger.warning("Redis not available, falling back to database")
            return self._fetch_from_source(conversation_id, limit)
        
        key = self._build_key(conversation_id)
        
        try:
            # Try to get messages from Redis LIST
            # LRANGE 0 -1 gets all items in the list
            cached_items = self.redis_client.lrange(key, 0, -1)
            
            if cached_items:
                # Cache hit - deserialize all messages
                messages = []
                for item in cached_items:
                    # Redis returns bytes, decode to string
                    message_str = item.decode('utf-8') if isinstance(item, bytes) else item
                    message = self._deserialize_message(message_str)
                    if message:
                        messages.append(message)
                
                logger.debug(f"Cache hit: {key} ({len(messages)} messages)")
                return messages
            
            # Cache miss - fetch from database
            logger.debug(f"Cache miss: {key}")
            messages = self._fetch_from_source(conversation_id, limit)
            
            if messages:
                # Populate cache with fetched messages
                self._populate_cache(conversation_id, messages)
            
            return messages
            
        except Exception as e:
            logger.error(f"Redis error in get_conversation_messages: {e}")
            # Fallback to database on any Redis error
            return self._fetch_from_source(conversation_id, limit)
    
    def _populate_cache(self, conversation_id: int, messages: List[Dict]) -> bool:
        """
        Populate cache with a list of messages.
        
        Used during cache miss to store database results.
        Uses pipeline for atomic operations.
        
        Args:
            conversation_id: ID of the conversation
            messages: List of message dictionaries to cache
            
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client or not messages:
            return False
        
        key = self._build_key(conversation_id)
        
        try:
            # Use pipeline for atomic operations
            pipe = self.redis_client.pipeline()
            
            # Delete existing key to start fresh
            pipe.delete(key)
            
            # Add all messages to the list
            for message in messages:
                serialized = self._serialize_message(message)
                pipe.rpush(key, serialized)
            
            # Set expiration
            pipe.expire(key, self.default_ttl)
            
            # Execute pipeline
            pipe.execute()
            
            logger.debug(f"Populated cache: {key} with {len(messages)} messages")
            return True
            
        except Exception as e:
            logger.error(f"Error populating cache: {e}")
            return False
    
    def add_message(self, conversation_id: int, message_data: dict, persist_func: callable) -> bool:
        """
        Add a new message using TRUE write-through strategy.
        
        This is the core improvement over write-invalidate:
        1. Write to database (source of truth)
        2. Append to Redis LIST (O(1) operation, no invalidation)
        3. Trim list to keep only last N messages
        4. Refresh TTL
        
        If Redis fails, operation still succeeds (DB write is primary).
        
        Args:
            conversation_id: ID of the conversation
            message_data: Message data dict with 'author', 'author_name', 'content'
            persist_func: Function that saves message to DB
        
        Returns:
            True if database write succeeded, False otherwise
        """
        try:
            # 1. ALWAYS persist to database first (source of truth)
            persist_func(conversation_id, message_data['author'], message_data['content'])
            
            # 2. Try to append to cache (best effort)
            if self.redis_client:
                self._append_to_cache(conversation_id, message_data)
            else:
                logger.warning("Redis not available, message saved to DB only")
            
            return True
            
        except Exception as e:
            logger.error(f"Error adding message (DB write failed): {e}")
            # Database write failed - do NOT update cache
            return False
    
    def _append_to_cache(self, conversation_id: int, message_data: dict) -> bool:
        """
        Append a single message to Redis LIST.
        
        Uses RPUSH (append to end), LTRIM (keep last N), and EXPIRE in a pipeline.
        This is O(1) amortized and requires zero invalidations.
        
        Args:
            conversation_id: ID of the conversation
            message_data: Message dictionary to append
            
        Returns:
            True if successful, False otherwise
        """
        key = self._build_key(conversation_id)
        
        try:
            # Serialize the message
            serialized = self._serialize_message(message_data)
            
            # Use pipeline for atomic operations
            pipe = self.redis_client.pipeline()
            
            # Append message to the end of the list
            pipe.rpush(key, serialized)
            
            # Trim to keep only last N messages
            # LTRIM keeps indices from -limit to -1 (last N items)
            pipe.ltrim(key, -self.default_limit, -1)
            
            # Refresh TTL on every write
            pipe.expire(key, self.default_ttl)
            
            # Execute all commands atomically
            pipe.execute()
            
            logger.debug(f"Appended message to cache: {key}")
            return True
            
        except Exception as e:
            logger.error(f"Error appending to cache: {e}")
            # Non-fatal: message is in DB, cache can be rebuilt on next read
            return False
    
    def invalidate_conversation(self, conversation_id: int) -> bool:
        """
        Invalidate cache for a conversation.
        
        Note: With true write-through, this is rarely needed.
        Provided for administrative purposes or emergency cache clearing.
        
        Args:
            conversation_id: ID of the conversation
            
        Returns:
            True if successful, False otherwise
        """
        if not self.redis_client:
            return False
        
        key = self._build_key(conversation_id)
        
        try:
            self.redis_client.delete(key)
            logger.info(f"Invalidated cache: {key}")
            return True
        except Exception as e:
            logger.error(f"Error invalidating cache: {e}")
            return False