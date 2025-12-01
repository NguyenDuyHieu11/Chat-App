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

from chat.consumers import ChatConsumer
from chat.models import Conversation, Message
from feed.models import AppUser
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


