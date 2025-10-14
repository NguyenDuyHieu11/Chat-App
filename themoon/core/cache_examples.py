"""
Examples demonstrating how to use the core caching module.

This file shows concrete usage patterns for both chat and feed domains.
Delete this file once you've implemented your domain-specific cache strategies.
"""

from core.caching import (
    BaseCacheStrategy,
    WriteThroughCacheStrategy,
    CacheAsideStrategy,
    CacheManager,
)


# ============================================================================
# Example 1: Chat Message Cache (Write-Through)
# ============================================================================

class ConversationMessagesCacheExample(WriteThroughCacheStrategy):
    """
    Cache for recent messages in a conversation.
    Uses write-through for strong consistency.
    """
    
    namespace = "chat"
    default_ttl = 1800  # 30 minutes
    
    def _fetch_from_source(self, conversation_id: int, limit: int = 50):
        """Fetch messages from database."""
        # This would query your Message model
        # from chat.models import Message
        # return Message.objects.filter(
        #     Conversation_id=conversation_id
        # ).order_by('-created_datetime')[:limit]
        pass
    
    def get_conversation_messages(self, conversation_id: int, limit: int = 50):
        """Get cached messages or fetch from DB."""
        return self.get('conversation', conversation_id, 'messages', default=[])
    
    def add_message(self, conversation_id: int, message_data: dict, persist_func: callable):
        """
        Add a new message using write-through strategy.
        
        Args:
            conversation_id: ID of the conversation
            message_data: Message data to persist
            persist_func: Function that saves message to DB
        """
        # Get current cached messages
        messages = self.get_conversation_messages(conversation_id)
        
        # Add new message
        messages.insert(0, message_data)  # Newest first
        
        # Keep only recent N messages
        messages = messages[:50]
        
        # Write through to DB and cache
        return self.write(
            'conversation', conversation_id, 'messages',
            value=messages,
            persist_func=persist_func
        )
    
    def invalidate_conversation(self, conversation_id: int):
        """Invalidate all cache for a conversation."""
        return self.delete('conversation', conversation_id, 'messages')


# ============================================================================
# Example 2: User Feed Cache (Cache-Aside)
# ============================================================================

class UserFeedCacheExample(CacheAsideStrategy):
    """
    Cache for user's personalized feed.
    Uses cache-aside for read-heavy workloads with stale tolerance.
    """
    
    namespace = "feed"
    default_ttl = 3600  # 1 hour
    
    def _fetch_from_source(self, user_id: int, page: int = 1):
        """Fetch feed from database."""
        # This would query your Post model with complex logic:
        # - Posts from followed users
        # - Sorted by relevance/recency
        # - Filtered by user preferences
        # from feed.models import Post, Follower
        # followed_users = Follower.objects.filter(
        #     following_user_id=user_id
        # ).values_list('followed_user_id', flat=True)
        # return Post.objects.filter(
        #     created_by_user_id__in=followed_users
        # ).order_by('-created_datetime')[:20]
        pass
    
    def get_user_feed(self, user_id: int, page: int = 1):
        """Get cached feed or fetch from DB."""
        def fetch_feed():
            return self._fetch_from_source(user_id, page)
        
        return self.fetch('user', user_id, 'feed', f'page_{page}', fetch_func=fetch_feed)
    
    def invalidate_user_feed(self, user_id: int):
        """Invalidate user's feed cache (e.g., after they follow someone)."""
        # Invalidate all pages
        for page in range(1, 6):  # Assume max 5 cached pages
            self.delete('user', user_id, 'feed', f'page_{page}')


# ============================================================================
# Example 3: Post Detail Cache (Cache-Aside)
# ============================================================================

class PostDetailCacheExample(CacheAsideStrategy):
    """
    Cache for individual post details with reactions and comments count.
    """
    
    namespace = "feed"
    default_ttl = 7200  # 2 hours
    
    def _fetch_from_source(self, post_id: int):
        """Fetch post with aggregated data from DB."""
        # from feed.models import Post, Reaction, Comment
        # post = Post.objects.get(id=post_id)
        # return {
        #     'id': post.id,
        #     'caption': post.caption,
        #     'created_by': post.created_by_user.profile_name,
        #     'created_datetime': post.created_datetime,
        #     'reactions_count': Reaction.objects.filter(post=post).count(),
        #     'comments_count': Comment.objects.filter(post=post).count(),
        # }
        pass
    
    def get_post_detail(self, post_id: int):
        """Get cached post detail or fetch from DB."""
        def fetch_post():
            return self._fetch_from_source(post_id)
        
        return self.fetch('post', post_id, 'detail', fetch_func=fetch_post)
    
    def invalidate_post(self, post_id: int):
        """Invalidate post cache (e.g., after new reaction/comment)."""
        return self.delete('post', post_id, 'detail')


# ============================================================================
# Example 4: Online Users Cache (Short TTL)
# ============================================================================

class OnlineUsersCacheExample(BaseCacheStrategy):
    """
    Cache for tracking online users.
    Very short TTL, updated frequently.
    """
    
    namespace = "chat"
    default_ttl = 60  # 1 minute
    
    def _fetch_from_source(self):
        """Not applicable - this is ephemeral data."""
        pass
    
    def mark_user_online(self, user_id: int):
        """Mark user as online."""
        self.set('user', user_id, 'online', value=True, ttl=self.default_ttl)
    
    def is_user_online(self, user_id: int) -> bool:
        """Check if user is online."""
        return self.get('user', user_id, 'online', default=False)
    
    def mark_user_offline(self, user_id: int):
        """Mark user as offline."""
        self.delete('user', user_id, 'online')


# ============================================================================
# Example 5: Typing Indicator Cache (Very Short TTL)
# ============================================================================

class TypingIndicatorCacheExample(BaseCacheStrategy):
    """
    Cache for real-time typing indicators.
    Very short TTL, no DB persistence.
    """
    
    namespace = "chat"
    default_ttl = 10  # 10 seconds
    
    def _fetch_from_source(self):
        """Not applicable - ephemeral data."""
        pass
    
    def set_typing(self, conversation_id: int, user_id: int):
        """Mark user as typing in conversation."""
        self.set('conversation', conversation_id, 'typing', user_id, value=True, ttl=self.default_ttl)
    
    def get_typing_users(self, conversation_id: int) -> list:
        """Get list of users currently typing."""
        # In practice, you might store a set of user IDs
        return self.get('conversation', conversation_id, 'typing_users', default=[])
    
    def clear_typing(self, conversation_id: int, user_id: int):
        """Clear typing indicator for user."""
        self.delete('conversation', conversation_id, 'typing', user_id)


# ============================================================================
# Example 6: Bulk Operations
# ============================================================================

def bulk_cache_example():
    """Example of bulk caching operations."""
    
    cache_strategy = UserFeedCacheExample()
    
    # Cache multiple users' feeds at once
    feeds_to_cache = {
        ('user', 1, 'feed', 'page_1'): [{'post_id': 1}, {'post_id': 2}],
        ('user', 2, 'feed', 'page_1'): [{'post_id': 3}, {'post_id': 4}],
        ('user', 3, 'feed', 'page_1'): [{'post_id': 5}, {'post_id': 6}],
    }
    
    cache_strategy.set_many(feeds_to_cache, ttl=3600)


# ============================================================================
# Example 7: Cache Manager Administrative Operations
# ============================================================================

def cache_admin_examples():
    """Examples of administrative cache operations."""
    
    manager = CacheManager()
    
    # Health check
    is_healthy = manager.health_check()
    print(f"Cache health: {'OK' if is_healthy else 'FAILED'}")
    
    # Get statistics
    stats = manager.get_stats()
    print(f"Cache stats: {stats}")
    
    # Clear entire namespace (use with caution!)
    # manager.clear_namespace('chat')  # Clears all chat cache
    # manager.clear_namespace('feed')  # Clears all feed cache


# ============================================================================
# Example 8: Usage in Django Views
# ============================================================================

async def example_view_usage():
    """
    Example of how to use cache strategies in Django views/consumers.
    """
    
    # Initialize strategies (can be done once at module level)
    post_cache = PostDetailCacheExample()
    feed_cache = UserFeedCacheExample()
    
    # Get post detail (automatically caches on first access)
    post = post_cache.get_post_detail(post_id=123)
    
    # Get user feed (automatically caches on first access)
    user_feed = feed_cache.get_user_feed(user_id=456, page=1)
    
    # Invalidate cache when data changes
    post_cache.invalidate_post(post_id=123)
    feed_cache.invalidate_user_feed(user_id=456)


# ============================================================================
# Example 9: Usage in WebSocket Consumer
# ============================================================================

async def example_consumer_usage():
    """
    Example of how to use cache in Django Channels consumer.
    """
    
    msg_cache = ConversationMessagesCacheExample()
    online_cache = OnlineUsersCacheExample()
    
    # On user connect
    user_id = 123
    conversation_id = 456
    
    online_cache.mark_user_online(user_id)
    
    # Get cached messages
    messages = msg_cache.get_conversation_messages(conversation_id)
    
    # On new message received
    new_message = {'id': 789, 'content': 'Hello!', 'author_id': user_id}
    
    def persist_message(msg_data):
        # Save to database
        # from chat.models import Message
        # Message.objects.create(**msg_data)
        pass
    
    msg_cache.add_message(conversation_id, new_message, persist_message)
    
    # On user disconnect
    online_cache.mark_user_offline(user_id)

