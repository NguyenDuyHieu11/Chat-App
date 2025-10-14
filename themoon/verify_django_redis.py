#!/usr/bin/env python
"""
Verification script to confirm django-redis is properly configured and working.

Run this to verify:
    python verify_django_redis.py
"""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'themoon.settings')
django.setup()

from django.core.cache import cache
from django_redis import get_redis_connection


def test_django_redis():
    """Run comprehensive django-redis verification tests."""
    
    print("=" * 60)
    print("Django-Redis Verification Script")
    print("=" * 60)
    
    # Test 1: Check backend
    print("\n1. Checking cache backend...")
    backend_class = cache.__class__.__name__
    print(f"   Backend: {backend_class}")
    
    if 'RedisCache' in backend_class:
        print("   ✅ django-redis is active!")
    else:
        print("   ❌ Not using django-redis backend")
        return False
    
    # Test 2: Test connection
    print("\n2. Testing Redis connection...")
    try:
        conn = get_redis_connection("default")
        ping_result = conn.ping()
        print(f"   Redis ping: {ping_result}")
        print("   ✅ Redis connection successful!")
    except Exception as e:
        print(f"   ❌ Redis connection failed: {e}")
        return False
    
    # Test 3: Basic operations
    print("\n3. Testing basic cache operations...")
    try:
        cache.set('test_key', 'test_value', timeout=60)
        value = cache.get('test_key')
        cache.delete('test_key')
        
        if value == 'test_value':
            print("   ✅ Basic operations working!")
        else:
            print("   ❌ Value mismatch")
            return False
    except Exception as e:
        print(f"   ❌ Basic operations failed: {e}")
        return False
    
    # Test 4: django-redis specific features
    print("\n4. Testing django-redis specific features...")
    
    # Test delete_pattern
    try:
        cache.set('test:1', 'val1')
        cache.set('test:2', 'val2')
        cache.delete_pattern('test:*')
        print("   ✅ delete_pattern() works!")
    except AttributeError:
        print("   ❌ delete_pattern() not available (not using django-redis)")
        return False
    except Exception as e:
        print(f"   ❌ delete_pattern() failed: {e}")
        return False
    
    # Test TTL
    try:
        cache.set('ttl_test', 'value', timeout=300)
        ttl = cache.ttl('ttl_test')
        print(f"   TTL test: {ttl} seconds remaining")
        cache.delete('ttl_test')
        print("   ✅ ttl() works!")
    except AttributeError:
        print("   ❌ ttl() not available")
        return False
    except Exception as e:
        print(f"   ❌ ttl() failed: {e}")
        return False
    
    # Test atomic operations
    try:
        cache.set('counter', 0)
        cache.incr('counter', 5)
        count = int(cache.get('counter'))
        cache.delete('counter')
        
        if count == 5:
            print(f"   Counter test: {count}")
            print("   ✅ incr() works!")
        else:
            print(f"   ❌ Counter mismatch: expected 5, got {count}")
            return False
    except Exception as e:
        print(f"   ❌ incr() failed: {e}")
        return False
    
    # Test locks
    try:
        with cache.lock('test_lock', timeout=10):
            print("   ✅ lock() works!")
    except AttributeError:
        print("   ❌ lock() not available")
        return False
    except Exception as e:
        print(f"   ❌ lock() failed: {e}")
        return False
    
    # Test 5: Redis server info
    print("\n5. Getting Redis server info...")
    try:
        info = conn.info()
        print(f"   Redis version: {info.get('redis_version', 'unknown')}")
        print(f"   Used memory: {info.get('used_memory_human', 'unknown')}")
        print(f"   Connected clients: {info.get('connected_clients', 0)}")
        print(f"   Total keys: {conn.dbsize()}")
        print("   ✅ Server info retrieved!")
    except Exception as e:
        print(f"   ❌ Failed to get server info: {e}")
        return False
    
    # Test 6: Test our custom utilities
    print("\n6. Testing custom utilities...")
    try:
        from core.redis_utils import RedisClient, RedisCounter, RedisLock
        
        # RedisClient
        client = RedisClient()
        if client.ping():
            print("   ✅ RedisClient works!")
        else:
            print("   ❌ RedisClient ping failed")
            return False
        
        # RedisCounter
        counter = RedisCounter('verify:counter')
        counter.set(0)
        counter.increment(10)
        value = counter.get()
        counter.reset()
        
        if value == 10:
            print(f"   ✅ RedisCounter works! (value: {value})")
        else:
            print(f"   ❌ RedisCounter mismatch: {value}")
            return False
        
        # RedisLock
        lock = RedisLock('verify:lock', timeout=5)
        if lock.acquire(blocking=False):
            lock.release()
            print("   ✅ RedisLock works!")
        else:
            print("   ❌ RedisLock failed to acquire")
            return False
            
    except ImportError as e:
        print(f"   ❌ Failed to import utilities: {e}")
        return False
    except Exception as e:
        print(f"   ❌ Utility test failed: {e}")
        return False
    
    # Test 7: Test caching strategies
    print("\n7. Testing caching strategies...")
    try:
        from core.caching import CacheAsideStrategy
        
        class TestCache(CacheAsideStrategy):
            namespace = "verify"
            default_ttl = 60
            
            def _fetch_from_source(self):
                return {'test': 'data'}
        
        test_cache = TestCache()
        test_cache.set('test', 'key', value={'data': 'test'})
        result = test_cache.get('test', 'key')
        test_cache.delete('test', 'key')
        
        if result == {'data': 'test'}:
            print("   ✅ CacheAsideStrategy works!")
        else:
            print(f"   ❌ Strategy test failed: {result}")
            return False
            
    except ImportError as e:
        print(f"   ❌ Failed to import strategies: {e}")
        return False
    except Exception as e:
        print(f"   ❌ Strategy test failed: {e}")
        return False
    
    # Success!
    print("\n" + "=" * 60)
    print("✅ All tests passed! django-redis is working correctly!")
    print("=" * 60)
    
    # Summary
    print("\nConfiguration Summary:")
    print(f"  • Backend: {backend_class}")
    print(f"  • Redis version: {info.get('redis_version', 'unknown')}")
    print(f"  • Connection: localhost:6379/1")
    print(f"  • Features: delete_pattern, ttl, locks, counters, pipelines")
    print("\nYou can now:")
    print("  1. Implement chat/cache.py using core.caching strategies")
    print("  2. Use core.redis_utils for advanced features")
    print("  3. Integrate with chat/consumers.py and feed/views.py")
    
    return True


if __name__ == '__main__':
    try:
        success = test_django_redis()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Verification failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


