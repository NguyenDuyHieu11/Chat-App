"""
Redis health check utilities for monitoring Redis connections used by Django Channels and caching.

This module provides health checks for:
1. Django Cache (Redis DB 1)
2. Channel Layer (Redis DB 0)
"""

import logging
from typing import Dict, Any, Optional
from django.core.cache import cache
from django_redis import get_redis_connection
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


class RedisHealthChecker:
    """
    Comprehensive health checker for Redis connections.
    
    Usage:
        checker = RedisHealthChecker()
        status = checker.check_all()
        if status['healthy']:
            print("All Redis systems operational")
        else:
            print(f"Redis issues detected: {status['errors']}")
    """
    
    @staticmethod
    def check_cache_backend() -> Dict[str, Any]:
        """
        Check Django cache backend (Redis DB 1).
        
        Returns:
            Dict with status, response_time, error
        """
        result = {
            'service': 'Django Cache (Redis DB 1)',
            'healthy': False,
            'response_time_ms': None,
            'error': None,
            'details': {}
        }
        
        try:
            import time
            start = time.time()
            
            # Test basic operations
            test_key = 'health_check:cache'
            test_value = 'ok'
            
            cache.set(test_key, test_value, timeout=10)
            retrieved = cache.get(test_key)
            cache.delete(test_key)
            
            end = time.time()
            result['response_time_ms'] = round((end - start) * 1000, 2)
            
            if retrieved == test_value:
                result['healthy'] = True
                
                # Get additional stats if available
                try:
                    conn = get_redis_connection("default")
                    info = conn.info()
                    result['details'] = {
                        'redis_version': info.get('redis_version'),
                        'used_memory': info.get('used_memory_human'),
                        'connected_clients': info.get('connected_clients'),
                        'total_keys': conn.dbsize(),
                    }
                except Exception as e:
                    logger.warning(f"Could not retrieve cache details: {e}")
            else:
                result['error'] = 'Cache value mismatch'
                
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Cache backend health check failed: {e}", exc_info=True)
            
        return result
    
    @staticmethod
    def check_channel_layer() -> Dict[str, Any]:
        """
        Check Django Channels layer (Redis DB 0).
        
        Returns:
            Dict with status, response_time, error
        """
        result = {
            'service': 'Channel Layer (Redis DB 0)',
            'healthy': False,
            'response_time_ms': None,
            'error': None,
            'details': {}
        }
        
        try:
            import time
            import asyncio
            
            channel_layer = get_channel_layer()
            
            if channel_layer is None:
                result['error'] = 'Channel layer not configured'
                return result
            
            # Test channel layer operations
            async def test_channel_layer():
                test_channel = 'health_check_channel'
                test_message = {'type': 'health.check', 'data': 'test'}
                
                try:
                    start = time.time()
                    
                    # Send message
                    await channel_layer.send(test_channel, test_message)
                    
                    # Receive message
                    received = await channel_layer.receive(test_channel)
                    
                    end = time.time()
                    
                    if received and received.get('type') == 'health.check':
                        return True, round((end - start) * 1000, 2), None
                    else:
                        return False, None, 'Message mismatch'
                        
                except Exception as e:
                    return False, None, str(e)
            
            # Run async test
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            healthy, response_time, error = loop.run_until_complete(test_channel_layer())
            loop.close()
            
            result['healthy'] = healthy
            result['response_time_ms'] = response_time
            result['error'] = error
            
            # Get channel layer details
            if hasattr(channel_layer, 'connection_kwargs'):
                result['details'] = {
                    'backend': channel_layer.__class__.__name__,
                    'hosts': channel_layer.connection_kwargs.get('address', 'unknown')
                }
                
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Channel layer health check failed: {e}", exc_info=True)
            
        return result
    
    @staticmethod
    def check_all() -> Dict[str, Any]:
        """
        Run all health checks.
        
        Returns:
            Dict with overall status and individual check results
        """
        cache_status = RedisHealthChecker.check_cache_backend()
        channel_status = RedisHealthChecker.check_channel_layer()
        
        all_healthy = cache_status['healthy'] and channel_status['healthy']
        
        errors = []
        if not cache_status['healthy']:
            errors.append(f"Cache: {cache_status['error']}")
        if not channel_status['healthy']:
            errors.append(f"Channel Layer: {channel_status['error']}")
        
        return {
            'healthy': all_healthy,
            'timestamp': __import__('datetime').datetime.now().isoformat(),
            'checks': {
                'cache': cache_status,
                'channel_layer': channel_status
            },
            'errors': errors if errors else None
        }
    
    @staticmethod
    def get_summary() -> str:
        """
        Get a human-readable summary of Redis health.
        
        Returns:
            Formatted string with health status
        """
        status = RedisHealthChecker.check_all()
        
        lines = []
        lines.append("=" * 60)
        lines.append("Redis Health Check Summary")
        lines.append("=" * 60)
        lines.append(f"Overall Status: {'✅ HEALTHY' if status['healthy'] else '❌ UNHEALTHY'}")
        lines.append(f"Timestamp: {status['timestamp']}")
        lines.append("")
        
        # Cache status
        cache = status['checks']['cache']
        lines.append(f"Django Cache (Redis DB 1): {'✅' if cache['healthy'] else '❌'}")
        if cache['healthy']:
            lines.append(f"  Response Time: {cache['response_time_ms']}ms")
            if cache['details']:
                lines.append(f"  Redis Version: {cache['details'].get('redis_version')}")
                lines.append(f"  Used Memory: {cache['details'].get('used_memory')}")
                lines.append(f"  Connected Clients: {cache['details'].get('connected_clients')}")
                lines.append(f"  Total Keys: {cache['details'].get('total_keys')}")
        else:
            lines.append(f"  Error: {cache['error']}")
        lines.append("")
        
        # Channel layer status
        channel = status['checks']['channel_layer']
        lines.append(f"Channel Layer (Redis DB 0): {'✅' if channel['healthy'] else '❌'}")
        if channel['healthy']:
            lines.append(f"  Response Time: {channel['response_time_ms']}ms")
            if channel['details']:
                lines.append(f"  Backend: {channel['details'].get('backend')}")
                lines.append(f"  Hosts: {channel['details'].get('hosts')}")
        else:
            lines.append(f"  Error: {channel['error']}")
        
        if status['errors']:
            lines.append("")
            lines.append("Errors:")
            for error in status['errors']:
                lines.append(f"  - {error}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)


class ConsumerHealthMixin:
    """
    Mixin for WebSocket consumers to add health check capabilities.
    
    Usage:
        class ChatConsumer(ConsumerHealthMixin, AsyncWebsocketConsumer):
            async def connect(self):
                if not await self.check_redis_health():
                    await self.close(code=4003)
                    return
                # ... rest of connection logic
    """
    
    async def check_redis_health(self) -> bool:
        """
        Check if Redis systems are healthy before accepting connection.
        
        Returns:
            True if healthy, False otherwise
        """
        try:
            from channels.db import database_sync_to_async
            
            @database_sync_to_async
            def _check():
                status = RedisHealthChecker.check_all()
                return status['healthy']
            
            return await _check()
            
        except Exception as e:
            logger.error(f"Error checking Redis health: {e}")
            return False
    
    async def send_health_status(self):
        """
        Send current Redis health status to WebSocket client.
        """
        try:
            from channels.db import database_sync_to_async
            import json
            
            @database_sync_to_async
            def _get_status():
                return RedisHealthChecker.check_all()
            
            status = await _get_status()
            
            await self.send(text_data=json.dumps({
                'type': 'health_status',
                'redis_healthy': status['healthy'],
                'cache_healthy': status['checks']['cache']['healthy'],
                'channel_layer_healthy': status['checks']['channel_layer']['healthy']
            }))
            
        except Exception as e:
            logger.error(f"Error sending health status: {e}")


def create_health_check_view():
    """
    Create a Django view for HTTP health checks.
    
    Returns:
        View function that returns health check JSON
    """
    from django.http import JsonResponse
    
    def health_check_view(request):
        """HTTP endpoint for health checks."""
        status = RedisHealthChecker.check_all()
        
        http_status = 200 if status['healthy'] else 503
        
        return JsonResponse(status, status=http_status)
    
    return health_check_view


# Convenience function for quick checks
def is_redis_healthy() -> bool:
    """
    Quick check if Redis is healthy.
    
    Returns:
        True if all Redis systems are healthy
    """
    try:
        status = RedisHealthChecker.check_all()
        return status['healthy']
    except Exception as e:
        logger.error(f"Error checking Redis health: {e}")
        return False


def print_health_summary():
    """Print health summary to console."""
    print(RedisHealthChecker.get_summary())


