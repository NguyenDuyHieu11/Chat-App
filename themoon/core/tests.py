"""
Tests for core caching module.
"""

from django.test import TestCase
from django.core.cache import cache

from core.caching import (
    CacheKeyBuilder,
    CacheSerializer,
    WriteThroughCacheStrategy,
    CacheAsideStrategy,
    CacheManager,
)


class CacheKeyBuilderTest(TestCase):
    """Test cache key generation."""
    
    def test_build_key(self):
        """Test basic key building."""
        key = CacheKeyBuilder.build('chat', 'conversation', 123, 'messages')
        self.assertEqual(key, 'chat:v1:conversation:123:messages')
    
    def test_build_key_with_custom_version(self):
        """Test key building with custom version."""
        key = CacheKeyBuilder.build('feed', 'user', 456, version='v2')
        self.assertEqual(key, 'feed:v2:user:456')
    
    def test_pattern(self):
        """Test pattern generation for wildcards."""
        pattern = CacheKeyBuilder.pattern('chat', 'conversation', '*', 'messages')
        self.assertEqual(pattern, 'chat:v1:conversation:*:messages')


class CacheSerializerTest(TestCase):
    """Test serialization and deserialization."""
    
    def test_serialize_dict(self):
        """Test serializing a dictionary."""
        data = {'id': 1, 'name': 'Test'}
        serialized = CacheSerializer.serialize(data)
        self.assertIsInstance(serialized, str)
        
    def test_deserialize_dict(self):
        """Test deserializing back to dict."""
        data = {'id': 1, 'name': 'Test'}
        serialized = CacheSerializer.serialize(data)
        deserialized = CacheSerializer.deserialize(serialized)
        self.assertEqual(deserialized, data)
    
    def test_serialize_list(self):
        """Test serializing a list."""
        data = [1, 2, 3, 4, 5]
        serialized = CacheSerializer.serialize(data)
        deserialized = CacheSerializer.deserialize(serialized)
        self.assertEqual(deserialized, data)


class MockCacheStrategy(CacheAsideStrategy):
    """Mock strategy for testing."""
    
    namespace = "test"
    default_ttl = 60
    
    def _fetch_from_source(self, *args, **kwargs):
        return {'mock': 'data'}


class BaseCacheStrategyTest(TestCase):
    """Test base cache strategy operations."""
    
    def setUp(self):
        """Set up test cache strategy."""
        self.strategy = MockCacheStrategy()
        cache.clear()
    
    def tearDown(self):
        """Clean up cache after each test."""
        cache.clear()
    
    def test_set_and_get(self):
        """Test basic set and get operations."""
        self.strategy.set('user', 123, 'profile', value={'name': 'John'})
        result = self.strategy.get('user', 123, 'profile')
        self.assertEqual(result, {'name': 'John'})
    
    def test_get_with_default(self):
        """Test get with default value for missing key."""
        result = self.strategy.get('user', 999, 'profile', default={'name': 'Default'})
        self.assertEqual(result, {'name': 'Default'})
    
    def test_delete(self):
        """Test delete operation."""
        self.strategy.set('user', 123, 'profile', value={'name': 'John'})
        self.strategy.delete('user', 123, 'profile')
        result = self.strategy.get('user', 123, 'profile')
        self.assertIsNone(result)
    
    def test_set_many(self):
        """Test bulk set operation."""
        mapping = {
            ('user', 1, 'profile'): {'name': 'User1'},
            ('user', 2, 'profile'): {'name': 'User2'},
            ('user', 3, 'profile'): {'name': 'User3'},
        }
        self.strategy.set_many(mapping)
        
        result1 = self.strategy.get('user', 1, 'profile')
        result2 = self.strategy.get('user', 2, 'profile')
        result3 = self.strategy.get('user', 3, 'profile')
        
        self.assertEqual(result1, {'name': 'User1'})
        self.assertEqual(result2, {'name': 'User2'})
        self.assertEqual(result3, {'name': 'User3'})
    
    def test_get_or_set(self):
        """Test get_or_set (cache-aside pattern)."""
        call_count = [0]
        
        def fetch_func():
            call_count[0] += 1
            return {'fetched': True}
        
        # First call should fetch from source
        result1 = self.strategy.get_or_set('user', 123, 'data', default_func=fetch_func)
        self.assertEqual(result1, {'fetched': True})
        self.assertEqual(call_count[0], 1)
        
        # Second call should use cache
        result2 = self.strategy.get_or_set('user', 123, 'data', default_func=fetch_func)
        self.assertEqual(result2, {'fetched': True})
        self.assertEqual(call_count[0], 1)  # Should not increment


class WriteThroughCacheStrategyTest(TestCase):
    """Test write-through cache strategy."""
    
    def setUp(self):
        """Set up test strategy."""
        
        class TestWriteThroughStrategy(WriteThroughCacheStrategy):
            namespace = "test"
            default_ttl = 60
            
            def _fetch_from_source(self, *args, **kwargs):
                pass
        
        self.strategy = TestWriteThroughStrategy()
        cache.clear()
    
    def tearDown(self):
        """Clean up cache after each test."""
        cache.clear()
    
    def test_write_through(self):
        """Test write-through operation."""
        persist_called = [False]
        
        def persist_func(value):
            persist_called[0] = True
        
        data = {'message': 'Hello World'}
        result = self.strategy.write('conversation', 123, 'messages', 
                                     value=data, persist_func=persist_func)
        
        self.assertTrue(result)
        self.assertTrue(persist_called[0])
        
        # Verify data is in cache
        cached = self.strategy.get('conversation', 123, 'messages')
        self.assertEqual(cached, data)


class CacheManagerTest(TestCase):
    """Test cache manager operations."""
    
    def setUp(self):
        """Set up cache manager."""
        self.manager = CacheManager()
        cache.clear()
    
    def tearDown(self):
        """Clean up cache after each test."""
        cache.clear()
    
    def test_singleton(self):
        """Test that CacheManager is a singleton."""
        manager1 = CacheManager()
        manager2 = CacheManager()
        self.assertIs(manager1, manager2)
    
    def test_health_check(self):
        """Test cache health check."""
        result = self.manager.health_check()
        self.assertTrue(result)
    
    def test_get_stats(self):
        """Test getting cache statistics."""
        stats = self.manager.get_stats()
        self.assertIsInstance(stats, dict)
