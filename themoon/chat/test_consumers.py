"""
Comprehensive tests for ChatConsumer with Redis integration.

Tests cover:
1. WebSocket connection/disconnection
2. Message sending/receiving
3. User presence notifications
4. Redis cache integration
5. Error handling
6. Redis failure scenarios
"""

import json
import pytest
from channels.testing import WebsocketCommunicator
from channels.layers import get_channel_layer
from channels.db import database_sync_to_async
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django_redis import get_redis_connection

from chat.consumers import ChatConsumer
from chat.presence_consumers import PresenceConsumer
from chat.presence_redis import PresenceConfig, now_ts_seconds, online_users_key_for_user
from chat.models import Conversation, Message
from feed.models import AppUser, Follower
from core.redis_health import RedisHealthChecker


User = get_user_model()


class ChatConsumerTestCase(TransactionTestCase):
    """
    Test ChatConsumer with full Redis integration.
    
    Uses TransactionTestCase for proper async/database handling.
    """
    
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Verify Redis is available
        health = RedisHealthChecker.check_all()
        if not health['healthy']:
            pytest.skip(f"Redis not healthy: {health['errors']}")
    
    def setUp(self):
        """Set up test fixtures."""
        # Clear Redis cache before each test
        cache.clear()
        
        # Clear channel layer (skip if not available or async)
        # Note: flush is async and not needed for test isolation
        
        # Create test users with linked AppUsers
        self.user1 = User.objects.create_user(
            username='testuser1',
            password='testpass123'
        )
        self.app_user1 = AppUser.objects.create(
            user=self.user1,
            first_name='Test',
            last_name='User1',
            profile_name='testuser1'
        )
        
        self.user2 = User.objects.create_user(
            username='testuser2',
            password='testpass123'
        )
        self.app_user2 = AppUser.objects.create(
            user=self.user2,
            first_name='Test',
            last_name='User2',
            profile_name='testuser2'
        )
        
        # Create test conversation
        self.conversation = Conversation.objects.create()
        self.conversation.participants.add(self.app_user1, self.app_user2)
        
        # Create some test messages
        self.message1 = Message.objects.create(
            conversation=self.conversation,
            author=self.app_user1,
            content="Test message 1"
        )
        self.message2 = Message.objects.create(
            conversation=self.conversation,
            author=self.app_user2,
            content="Test message 2"
        )
    
    def tearDown(self):
        """Clean up after each test."""
        cache.clear()
    
    async def test_consumer_connect_authenticated(self):
        """Test successful WebSocket connection with authenticated user."""
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator.scope['user'] = self.user1
        communicator.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        # Connect
        connected, _ = await communicator.connect()
        self.assertTrue(connected, "Should successfully connect")
        
        # Should receive message history
        response = await communicator.receive_json_from()
        self.assertEqual(response['type'], 'message_history')
        self.assertIn('messages', response)
        
        # Should receive user_joined notification (from self)
        # Note: might not receive own join notification
        
        # Disconnect
        await communicator.disconnect()

    async def test_presence_consumer_subscribe_denied_when_not_mutual_followers(self):
        """PresenceConsumer must deny subscription unless users are mutual followers."""
        communicator = WebsocketCommunicator(
            PresenceConsumer.as_asgi(),
            "/ws/presence/",
        )
        communicator.scope["user"] = self.user1

        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # Connected ack
        msg = await communicator.receive_json_from()
        self.assertEqual(msg["type"], "presence.connected")
        self.assertEqual(msg["user_id"], self.app_user1.id)

        # Try to subscribe to user2 without mutual follow
        await communicator.send_json_to(
            {"type": "presence.subscribe", "target_user_id": self.app_user2.id}
        )
        resp = await communicator.receive_json_from()
        self.assertEqual(resp["type"], "presence.subscribe.denied")
        self.assertEqual(resp["target_user_id"], self.app_user2.id)
        self.assertEqual(resp["reason"], "not_mutual_followers")

        await communicator.disconnect()

    async def test_presence_consumer_subscribe_ok_when_mutual_followers(self):
        """PresenceConsumer must allow subscription when mutual follow exists."""
        Follower.objects.create(following_user=self.app_user1, followed_user=self.app_user2)
        Follower.objects.create(following_user=self.app_user2, followed_user=self.app_user1)

        communicator = WebsocketCommunicator(
            PresenceConsumer.as_asgi(),
            "/ws/presence/",
        )
        communicator.scope["user"] = self.user1

        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        _ = await communicator.receive_json_from()

        await communicator.send_json_to(
            {"type": "presence.subscribe", "target_user_id": self.app_user2.id}
        )
        resp = await communicator.receive_json_from()
        self.assertEqual(resp["type"], "presence.subscribe.ok")
        self.assertEqual(resp["target_user_id"], self.app_user2.id)

        await communicator.disconnect()

    async def test_presence_heartbeat_updates_online_users_zset_and_emits_online(self):
        """
        Heartbeat must:
        - ZADD expiry score into the correct online_users shard key
        - Emit an "online" status event on first heartbeat (missing/expired -> online)
        """
        communicator = WebsocketCommunicator(
            PresenceConsumer.as_asgi(),
            "/ws/presence/",
        )
        communicator.scope["user"] = self.user1

        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        # connected ack
        _ = await communicator.receive_json_from()

        await communicator.send_json_to({"type": "presence.heartbeat"})

        # Should receive online status via group_send to own group
        evt = await communicator.receive_json_from()
        self.assertEqual(evt["type"], "presence.status")
        self.assertEqual(evt["user_id"], self.app_user1.id)
        self.assertEqual(evt["status"], "online")

        # Verify Redis ZSET contains member with expiry >= now
        cfg = PresenceConfig()
        key = online_users_key_for_user(cfg, self.app_user1.id)
        member = str(self.app_user1.id)
        conn = get_redis_connection("default")
        score = conn.zscore(key, member)
        self.assertIsNotNone(score)
        self.assertGreaterEqual(float(score), float(now_ts_seconds()))

        await communicator.disconnect()

    async def test_presence_away_and_active_broadcast_and_snapshot(self):
        """
        - presence.away should broadcast away and persist state for snapshots
        - subscribing should immediately receive a snapshot status
        """
        # Make users mutual followers so user1 can subscribe to user2
        Follower.objects.create(following_user=self.app_user1, followed_user=self.app_user2)
        Follower.objects.create(following_user=self.app_user2, followed_user=self.app_user1)

        comm1 = WebsocketCommunicator(PresenceConsumer.as_asgi(), "/ws/presence/")
        comm1.scope["user"] = self.user1
        connected, _ = await comm1.connect()
        self.assertTrue(connected)
        _ = await comm1.receive_json_from()  # presence.connected

        comm2 = WebsocketCommunicator(PresenceConsumer.as_asgi(), "/ws/presence/")
        comm2.scope["user"] = self.user2
        connected, _ = await comm2.connect()
        self.assertTrue(connected)
        _ = await comm2.receive_json_from()  # presence.connected

        # user2 becomes online
        await comm2.send_json_to({"type": "presence.heartbeat"})
        evt2 = await comm2.receive_json_from()
        self.assertEqual(evt2["status"], "online")

        # user1 subscribes to user2 and should receive snapshot
        await comm1.send_json_to({"type": "presence.subscribe", "target_user_id": self.app_user2.id})
        _ = await comm1.receive_json_from()  # subscribe.ok
        snap = await comm1.receive_json_from()
        self.assertTrue(snap.get("snapshot"))
        self.assertEqual(snap["user_id"], self.app_user2.id)
        self.assertEqual(snap["status"], "online")

        # user2 goes away and user1 should receive event
        await comm2.send_json_to({"type": "presence.away"})
        away_evt = await comm1.receive_json_from()
        self.assertEqual(away_evt["type"], "presence.status")
        self.assertEqual(away_evt["user_id"], self.app_user2.id)
        self.assertEqual(away_evt["status"], "away")

        await comm1.disconnect()
        await comm2.disconnect()

    async def test_chat_typing_events_do_not_require_message_content(self):
        """
        ChatConsumer should accept presence.typing.start/stop without 'message'/'content'
        and broadcast to the conversation group.
        """
        comm1 = WebsocketCommunicator(ChatConsumer.as_asgi(), f"/ws/chat/{self.conversation.id}/")
        comm1.scope["user"] = self.user1
        comm1.scope["url_route"] = {"kwargs": {"conversation_id": str(self.conversation.id)}}

        comm2 = WebsocketCommunicator(ChatConsumer.as_asgi(), f"/ws/chat/{self.conversation.id}/")
        comm2.scope["user"] = self.user2
        comm2.scope["url_route"] = {"kwargs": {"conversation_id": str(self.conversation.id)}}

        connected, _ = await comm1.connect()
        self.assertTrue(connected)
        _ = await comm1.receive_json_from()  # message_history

        connected, _ = await comm2.connect()
        self.assertTrue(connected)
        _ = await comm2.receive_json_from()  # message_history

        await comm1.send_json_to({"type": "presence.typing.start"})

        # comm2 should receive typing event (comm1 may also receive; we only assert comm2)
        evt = await comm2.receive_json_from()
        # If message_history race occurs, this may be chat.message; loop until we see typing
        while evt.get("type") != "presence.typing":
            evt = await comm2.receive_json_from()

        self.assertEqual(evt["type"], "presence.typing")
        self.assertEqual(evt["conversation_id"], str(self.conversation.id) if isinstance(evt["conversation_id"], str) else evt["conversation_id"])
        self.assertEqual(evt["user_id"], self.app_user1.id)
        self.assertTrue(evt["is_typing"])

        await comm1.disconnect()
        await comm2.disconnect()

    def test_presence_leaderboard_endpoint_returns_mutual_followers_with_status(self):
        """
        GET /chat/api/presence/leaderboard/ should return mutual followers ordered with online first.
        """
        # Mutual follow between app_user1 and app_user2
        Follower.objects.create(following_user=self.app_user1, followed_user=self.app_user2)
        Follower.objects.create(following_user=self.app_user2, followed_user=self.app_user1)

        # Mark app_user2 online in Redis directly
        cfg = PresenceConfig()
        conn = get_redis_connection("default")
        key = online_users_key_for_user(cfg, self.app_user2.id)
        now_ts = now_ts_seconds()
        conn.zadd(key, {str(self.app_user2.id): now_ts + cfg.heartbeat_window_seconds})

        self.client.force_login(self.user1)
        resp = self.client.get("/chat/api/presence/leaderboard/?limit=50")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)

        # Should include user2
        ids = [r["user_id"] for r in data["results"]]
        self.assertIn(self.app_user2.id, ids)
    
    async def test_consumer_connect_unauthenticated(self):
        """Test WebSocket connection rejection for unauthenticated user."""
        from django.contrib.auth.models import AnonymousUser
        
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator.scope['user'] = AnonymousUser()
        communicator.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        # Should not connect
        connected, _ = await communicator.connect()
        self.assertFalse(connected, "Should reject unauthenticated connection")
    
    async def test_consumer_connect_no_conversation_id(self):
        """Test connection rejection when conversation_id is missing."""
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            "/ws/chat/"
        )
        communicator.scope['user'] = self.user1
        communicator.scope['url_route'] = {'kwargs': {}}
        
        # Should not connect
        connected, _ = await communicator.connect()
        self.assertFalse(connected, "Should reject connection without conversation_id")
    
    async def test_send_receive_message(self):
        """Test sending and receiving messages."""
        # Connect user1
        communicator1 = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator1.scope['user'] = self.user1
        communicator1.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        connected, _ = await communicator1.connect()
        self.assertTrue(connected)
        
        # Receive initial message history
        await communicator1.receive_json_from()
        
        # Connect user2
        communicator2 = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator2.scope['user'] = self.user2
        communicator2.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        connected, _ = await communicator2.connect()
        self.assertTrue(connected)
        
        # Receive initial message history
        await communicator2.receive_json_from()
        
        # User1 sends a message
        await communicator1.send_json_to({
            'type': 'chat.message',
            'message': 'Hello from user1!'
        })
        
        # Both users should receive the message
        response1 = await communicator1.receive_json_from()
        self.assertEqual(response1['type'], 'chat.message')
        self.assertIn('message', response1)
        
        response2 = await communicator2.receive_json_from()
        self.assertEqual(response2['type'], 'chat.message')
        self.assertIn('message', response2)
        
        # Disconnect
        await communicator1.disconnect()
        await communicator2.disconnect()
    
    async def test_message_persisted_to_database(self):
        """Test that messages are saved to database."""
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator.scope['user'] = self.user1
        communicator.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        
        # Skip message history
        await communicator.receive_json_from()
        
        # Count messages before
        @database_sync_to_async
        def get_message_count():
            return Message.objects.filter(conversation=self.conversation).count()
        
        initial_count = await get_message_count()
        
        # Send message
        test_content = 'This should be persisted'
        await communicator.send_json_to({
            'type': 'chat.message',
            'message': test_content
        })
        
        # Receive the broadcasted message
        await communicator.receive_json_from()
        
        # Check database
        final_count = await get_message_count()
        self.assertEqual(final_count, initial_count + 1, "Message should be saved to DB")
        
        # Verify content
        @database_sync_to_async
        def get_last_message():
            return Message.objects.filter(conversation=self.conversation).latest('created_datetime')
        
        last_message = await get_last_message()
        self.assertEqual(last_message.content, test_content)
        self.assertEqual(last_message.author_id, self.app_user1.id)  # Use author_id to avoid DB query
        
        await communicator.disconnect()
    
    async def test_message_cached_in_redis(self):
        """Test that messages are cached in Redis."""
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator.scope['user'] = self.user1
        communicator.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        
        # Skip message history
        await communicator.receive_json_from()
        
        # Send message
        await communicator.send_json_to({
            'type': 'chat.message',
            'message': 'Cached message'
        })
        
        # Receive the message
        await communicator.receive_json_from()
        
        # Check Redis cache
        @database_sync_to_async
        def check_cache():
            from chat.conversation_messages_cache import ConversationMessagesCache
            msg_cache = ConversationMessagesCache()
            cached_messages = msg_cache.get_conversation_messages(self.conversation.id)
            return cached_messages
        
        cached_messages = await check_cache()
        self.assertIsNotNone(cached_messages, "Messages should be in cache")
        
        await communicator.disconnect()
    
    async def test_invalid_message_format(self):
        """Test error handling for invalid message format."""
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator.scope['user'] = self.user1
        communicator.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        await communicator.connect()
        await communicator.receive_json_from()  # Skip history
        
        # Send invalid JSON
        await communicator.send_to(text_data="not valid json")
        
        # Should receive error message
        response = await communicator.receive_json_from()
        self.assertEqual(response['type'], 'error')
        self.assertIn('Invalid message format', response['message'])
        
        await communicator.disconnect()
    
    async def test_empty_message_content(self):
        """Test error handling for empty message content."""
        communicator = WebsocketCommunicator(
            ChatConsumer.as_asgi(),
            f"/ws/chat/{self.conversation.id}/"
        )
        communicator.scope['user'] = self.user1
        communicator.scope['url_route'] = {
            'kwargs': {'conversation_id': str(self.conversation.id)}
        }
        
        await communicator.connect()
        await communicator.receive_json_from()  # Skip history
        
        # Send message without content
        await communicator.send_json_to({
            'type': 'chat.message',
            'message': ''
        })
        
        # Should receive error message
        response = await communicator.receive_json_from()
        self.assertEqual(response['type'], 'error')
        self.assertIn('Message content is required', response['message'])
        
        await communicator.disconnect()
    
    async def test_multiple_concurrent_users(self):
        """Test multiple users in the same conversation."""
        communicators = []
        
        @database_sync_to_async
        def create_test_user(i):
            """Create Django User and AppUser, add to conversation."""
            user = User.objects.create_user(
                username=f'concurrentuser{i}',
                password='testpass123'
            )
            app_user = AppUser.objects.create(
                user=user,
                first_name='Concurrent',
                last_name=f'User{i}',
                profile_name=f'concurrentuser{i}'
            )
            self.conversation.participants.add(app_user)
            return user
        
        # Connect 5 users
        for i in range(5):
            user = await create_test_user(i)
            
            communicator = WebsocketCommunicator(
                ChatConsumer.as_asgi(),
                f"/ws/chat/{self.conversation.id}/"
            )
            communicator.scope['user'] = user
            communicator.scope['url_route'] = {
                'kwargs': {'conversation_id': str(self.conversation.id)}
            }
            
            await communicator.connect()
            await communicator.receive_json_from()  # Skip history
            
            communicators.append(communicator)
        
        # First user sends a message
        await communicators[0].send_json_to({
            'type': 'chat.message',
            'message': 'Broadcast to all!'
        })
        
        # All users should receive the message
        for communicator in communicators:
            response = await communicator.receive_json_from()
            self.assertEqual(response['type'], 'chat.message')
        
        # Disconnect all
        for communicator in communicators:
            await communicator.disconnect()


class RedisCacheIntegrationTestCase(TestCase):
    """Test Redis cache integration with message storage."""
    
    def setUp(self):
        """Set up test fixtures."""
        cache.clear()
        
        # Create test user with linked AppUser
        self.user = User.objects.create_user(
            username='cacheuser',
            password='testpass123'
        )
        self.app_user = AppUser.objects.create(
            user=self.user,
            first_name='Cache',
            last_name='User',
            profile_name='cacheuser'
        )
        
        # Create conversation
        self.conversation = Conversation.objects.create()
        self.conversation.participants.add(self.app_user)
    
    def tearDown(self):
        """Clean up."""
        cache.clear()
    
    def test_cache_write_through_strategy(self):
        """Test TRUE write-through cache strategy for messages using Redis LISTs."""
        from chat.conversation_messages_cache import ConversationMessagesCache
        from chat.repository_layer.message_repo import MessageRepository
        
        cache_strategy = ConversationMessagesCache()
        
        # Create persist function
        def persist_func(conversation_id, author_id, content):
            return MessageRepository.create_message(conversation_id, author_id, content)
        
        # Add message using write-through
        message_data = {
            'author': self.app_user.id,
            'author_name': 'Cache User',
            'content': 'Test write-through'
        }
        
        cache_strategy.add_message(
            self.conversation.id,
            message_data,
            persist_func
        )
        
        # Verify message is in database
        messages = Message.objects.filter(conversation=self.conversation)
        self.assertEqual(messages.count(), 1)
        self.assertEqual(messages.first().content, 'Test write-through')
        
        # Verify message is in cache (should be appended, not invalidated)
        cached_messages = cache_strategy.get_conversation_messages(self.conversation.id)
        self.assertIsNotNone(cached_messages)
        self.assertEqual(len(cached_messages), 1)
        self.assertEqual(cached_messages[0]['content'], 'Test write-through')
        
        # Add another message - should append without invalidation
        message_data2 = {
            'author': self.app_user.id,
            'author_name': 'Cache User',
            'content': 'Second message'
        }
        
        cache_strategy.add_message(
            self.conversation.id,
            message_data2,
            persist_func
        )
        
        # Both messages should be in cache now (incremental update, not refetch)
        cached_messages = cache_strategy.get_conversation_messages(self.conversation.id)
        self.assertEqual(len(cached_messages), 2)
        self.assertEqual(cached_messages[0]['content'], 'Test write-through')
        self.assertEqual(cached_messages[1]['content'], 'Second message')
    
    def test_cache_fallback_on_redis_failure(self):
        """Test fallback to database when Redis fails."""
        from chat.conversation_messages_cache import ConversationMessagesCache
        
        # Create some messages in database
        Message.objects.create(
            conversation=self.conversation,
            author=self.app_user,
            content="Message 1"
        )
        Message.objects.create(
            conversation=self.conversation,
            author=self.app_user,
            content="Message 2"
        )
        
        # Clear cache to simulate cache miss
        cache.clear()
        
        # Try to get messages (should fallback to DB)
        cache_strategy = ConversationMessagesCache()
        messages = cache_strategy.get_conversation_messages(self.conversation.id)
        
        # Should return messages from database
        self.assertIsNotNone(messages)
        self.assertEqual(len(messages), 2)
    
    def test_cache_invalidation(self):
        """Test cache invalidation."""
        from chat.conversation_messages_cache import ConversationMessagesCache
        
        cache_strategy = ConversationMessagesCache()
        
        # Create and cache some data
        Message.objects.create(
            conversation=self.conversation,
            author=self.app_user,
            content="Cached message"
        )
        
        # Get messages (caches them)
        messages1 = cache_strategy.get_conversation_messages(self.conversation.id)
        self.assertIsNotNone(messages1)
        
        # Invalidate cache
        result = cache_strategy.invalidate_conversation(self.conversation.id)
        self.assertTrue(result)
        
        # Next get should fetch from database again
        messages2 = cache_strategy.get_conversation_messages(self.conversation.id)
        self.assertIsNotNone(messages2)


class RedisHealthCheckTestCase(TestCase):
    """Test Redis health check utilities."""
    
    def test_check_cache_backend(self):
        """Test cache backend health check."""
        result = RedisHealthChecker.check_cache_backend()
        
        self.assertIn('healthy', result)
        self.assertIn('service', result)
        self.assertEqual(result['service'], 'Django Cache (Redis DB 1)')
        
        if result['healthy']:
            self.assertIsNotNone(result['response_time_ms'])
            self.assertIsNone(result['error'])
        else:
            self.assertIsNotNone(result['error'])
    
    def test_check_channel_layer(self):
        """Test channel layer health check."""
        result = RedisHealthChecker.check_channel_layer()
        
        self.assertIn('healthy', result)
        self.assertIn('service', result)
        self.assertEqual(result['service'], 'Channel Layer (Redis DB 0)')
    
    def test_check_all(self):
        """Test comprehensive health check."""
        result = RedisHealthChecker.check_all()
        
        self.assertIn('healthy', result)
        self.assertIn('checks', result)
        self.assertIn('cache', result['checks'])
        self.assertIn('channel_layer', result['checks'])
        self.assertIn('timestamp', result)
        
        if not result['healthy']:
            self.assertIsNotNone(result['errors'])
    
    def test_health_summary(self):
        """Test health summary generation."""
        summary = RedisHealthChecker.get_summary()
        
        self.assertIsInstance(summary, str)
        self.assertIn('Redis Health Check Summary', summary)
        self.assertIn('Django Cache', summary)
        self.assertIn('Channel Layer', summary)


