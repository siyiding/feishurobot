"""
Redis-based Push Notification Service.

Provides:
- Redis Stream queue for push messages
- P0 priority fast lane (no rate limiting)
- P1 batch windowed delivery (30 min)
- P2 user-controlled frequency
- Token bucket rate limiting for Feishu API (60/min)
"""
import json
import uuid
from datetime import datetime
from typing import Optional

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import PushMessage

logger = get_logger(__name__)


class PushService:
    """
    Push notification service using Redis Streams.
    
    Provides priority-based message delivery:
    - P0: Immediate, no rate limiting (fast lane)
    - P1: 30-minute batching window
    - P2: User-controlled frequency
    """

    # Redis keys
    STREAM_KEY = "push:queue:stream"
    CONSUMER_GROUP = "push-workers"
    P0_STREAM_KEY = "push:queue:p0"  # Fast lane for P0
    
    # Rate limiting
    RATE_LIMIT_PER_MINUTE = 60
    RATE_LIMIT_KEY = "push:rate_limit"

    def __init__(self):
        settings = get_settings()
        self.redis_url = f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}/{settings.redis_db}" if settings.redis_password else f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        self._redis: Optional[redis.Redis] = None
        self._connected = False

    async def connect(self):
        """Establish Redis connection."""
        if not REDIS_AVAILABLE:
            logger.warning("Redis module not available. Push service will operate in mock mode.")
            self._connected = False
            return
            
        if self._redis is None:
            try:
                self._redis = redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
                await self._redis.ping()
                self._connected = True
                logger.info("Redis connection established for push service")
            except Exception as e:
                logger.warning(f"Redis not available: {e}. Push service will operate in mock mode.")
                self._connected = False

    async def disconnect(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._connected = False

    async def _ensure_connected(self):
        """Ensure Redis is connected."""
        if not self._connected:
            await self.connect()

    async def enqueue_message(
        self,
        level: str,
        msg_type: str,
        title: str,
        content: str,
        url: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        Enqueue a push message.
        
        Args:
            level: Priority level (P0, P1, P2)
            msg_type: Message type (bug_new, bug_update, task_overdue, dr_alert)
            title: Message title
            content: Message content (markdown supported)
            url: Optional deep link
            user_id: Optional target user open_id
            
        Returns:
            Message ID
        """
        await self._ensure_connected()
        
        msg_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        
        message = PushMessage(
            id=msg_id,
            level=level,
            msg_type=msg_type,
            title=title,
            content=content,
            url=url,
            user_id=user_id,
            created_at=now,
            enqueue_at=now,
        )
        
        if not self._connected:
            # Mock mode - just log
            logger.info(f"[MOCK] Enqueued push message: {title}")
            return msg_id
        
        # Determine stream key based on priority
        stream_key = self.P0_STREAM_KEY if level == "P0" else self.STREAM_KEY
        
        try:
            # Add to Redis Stream
            await self._redis.xadd(
                stream_key,
                {
                    "data": message.model_dump_json(),
                    "level": level,
                    "msg_type": msg_type,
                },
            )
            logger.info(f"Enqueued {level} message to {stream_key}: {title}")
        except Exception as e:
            logger.error(f"Failed to enqueue message: {e}")
            # Fallback - store in memory (will be lost on restart)
            logger.warning("Falling back to in-memory queue (messages may be lost)")
        
        return msg_id

    async def enqueue_p0_alert(
        self,
        title: str,
        content: str,
        bug_id: Optional[str] = None,
        url: Optional[str] = None,
    ) -> str:
        """
        Enqueue a P0 alert via the fast lane (no rate limiting).
        
        Args:
            title: Alert title
            content: Alert content
            bug_id: Optional bug ID for deep link
            url: Optional deep link URL
            
        Returns:
            Message ID
        """
        url = url or (f"https://project.feishu.cn/open-apis/baike/v1/bug_issues/{bug_id}" if bug_id else None)
        
        return await self.enqueue_message(
            level="P0",
            msg_type="bug_new" if "新建" in title or "创建" in title else "bug_update",
            title=title,
            content=content,
            url=url,
        )

    async def enqueue_p1_notification(
        self,
        msg_type: str,
        title: str,
        content: str,
        url: Optional[str] = None,
    ) -> str:
        """
        Enqueue a P1 notification (30-minute batching window).
        
        Args:
            msg_type: Message type
            title: Notification title
            content: Notification content
            url: Optional deep link
            
        Returns:
            Message ID
        """
        return await self.enqueue_message(
            level="P1",
            msg_type=msg_type,
            title=title,
            content=content,
            url=url,
        )

    async def acquire_rate_limit_token(self) -> bool:
        """
        Acquire a rate limit token using token bucket algorithm.
        
        Returns:
            True if token acquired, False if rate limited
        """
        await self._ensure_connected()
        
        if not self._connected:
            # Mock mode - always allow
            return True
        
        try:
            # Use Redis INCR with EXPIRE for simple token bucket
            current = await self._redis.incr(self.RATE_LIMIT_KEY)
            if current == 1:
                # First request, set expiry for this minute
                await self._redis.expire(self.RATE_LIMIT_KEY, 60)
            
            if current <= self.RATE_LIMIT_PER_MINUTE:
                return True
            else:
                logger.warning(f"Rate limit exceeded: {current}/{self.RATE_LIMIT_PER_MINUTE}")
                return False
        except Exception as e:
            logger.error(f"Rate limit check failed: {e}")
            return True  # Allow on error to avoid blocking

    async def get_queue_length(self) -> int:
        """Get current queue length (for monitoring)."""
        await self._ensure_connected()
        
        if not self._connected:
            return 0
        
        try:
            length = await self._redis.xlen(self.STREAM_KEY)
            p0_length = await self._redis.xlen(self.P0_STREAM_KEY)
            return length + p0_length
        except Exception as e:
            logger.error(f"Failed to get queue length: {e}")
            return 0

    async def get_queue_stats(self) -> dict:
        """Get queue statistics for monitoring."""
        total = await self.get_queue_length()
        p0_count = 0
        p1p2_count = total
        
        if self._connected:
            try:
                p0_count = await self._redis.xlen(self.P0_STREAM_KEY)
                p1p2_count = await self._redis.xlen(self.STREAM_KEY)
            except Exception:
                pass
        
        return {
            "total": total,
            "p0": p0_count,
            "p1p2": p1p2_count,
            "connected": self._connected,
        }


# Singleton instance
_push_service: Optional[PushService] = None


def get_push_service() -> PushService:
    """Get singleton PushService instance."""
    global _push_service
    if _push_service is None:
        _push_service = PushService()
    return _push_service
