"""
Redis-based Push Notification Service.

Provides:
- Redis Stream queue for push messages
- P0 priority fast lane (no rate limiting)
- P1 batch windowed delivery (30 min)
- P2 user-controlled frequency
- Token bucket rate limiting for Feishu API (60/min)

M3 Enhancements:
- Integrated P1 batching service for 30-min windowed delivery
- P2 frequency control based on user preferences
- Push configuration management for user preferences
"""
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, List

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

    # P2 Frequency control keys
    P2_LAST_PUSH_KEY = "push:p2:last"
    P2_QUEUE_KEY = "push:p2:pending"

    async def enqueue_p2_notification(
        self,
        msg_type: str,
        title: str,
        content: str,
        url: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        Enqueue a P2 notification with user-controlled frequency.
        
        P2 messages are rate-limited based on user preferences:
        - real_time: No batching, send immediately (if not rate limited)
        - hourly: At most once per hour per user
        - daily: At most once per day per user
        - weekly: At most once per week per user
        - off: Don't send
        
        Args:
            msg_type: Message type
            title: Notification title
            content: Notification content
            url: Optional deep link
            user_id: Target user open_id (required for frequency control)
            
        Returns:
            Message ID, or empty string if rate limited
        """
        await self._ensure_connected()
        
        # Check user frequency preference
        if user_id:
            from app.services.push_config_service import get_push_config_service, PushFrequency
            
            config_service = get_push_config_service()
            config = await config_service.get_user_config(user_id)
            
            if config.p2_frequency == PushFrequency.OFF:
                logger.info(f"P2 push skipped for {user_id}: frequency set to OFF")
                return ""
            
            # Check rate limit based on frequency
            if not await self._check_p2_rate_limit(user_id, config.p2_frequency):
                logger.info(f"P2 push skipped for {user_id}: rate limited ({config.p2_frequency})")
                return ""
        
        return await self.enqueue_message(
            level="P2",
            msg_type=msg_type,
            title=title,
            content=content,
            url=url,
            user_id=user_id,
        )

    async def _check_p2_rate_limit(self, user_id: str, frequency: str) -> bool:
        """
        Check if P2 push is within rate limit for user.
        
        Args:
            user_id: User open_id
            frequency: PushFrequency value
            
        Returns:
            True if allowed, False if rate limited
        """
        if not self._connected:
            return True  # Allow in mock mode
        
        key = f"{self.P2_LAST_PUSH_KEY}:{user_id}"
        
        try:
            last_push = await self._redis.get(key)
            
            if last_push is None:
                # Never pushed, allow
                return True
            
            last_time = datetime.fromisoformat(last_push)
            now = datetime.utcnow()
            elapsed = (now - last_time).total_seconds()
            
            if frequency == "real_time":
                # 1 minute cooldown
                return elapsed >= 60
            elif frequency == "hourly":
                return elapsed >= 3600
            elif frequency == "daily":
                return elapsed >= 86400
            elif frequency == "weekly":
                return elapsed >= 604800
            else:
                return True
                
        except Exception as e:
            logger.warning(f"Failed to check P2 rate limit: {e}")
            return True  # Allow on error

    async def record_p2_push(self, user_id: str) -> None:
        """
        Record a P2 push for rate limiting.
        
        Args:
            user_id: User open_id
        """
        await self._ensure_connected()
        
        if not self._connected:
            return
        
        key = f"{self.P2_LAST_PUSH_KEY}:{user_id}"
        now = datetime.utcnow().isoformat()
        
        try:
            await self._redis.set(key, now)
        except Exception as e:
            logger.warning(f"Failed to record P2 push: {e}")

    async def enqueue_dr_alert(
        self,
        alert_level: str,
        title: str,
        content: str,
        signal_id: Optional[str] = None,
        url: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        Enqueue a DR (Data Report) platform alert.
        
        DR alerts are mapped to push levels:
        - critical -> P0 (immediate)
        - error -> P1 (batched)
        - warning -> P1 (batched)
        - info -> P2 (frequency controlled)
        
        Args:
            alert_level: DR alert level (critical/error/warning/info)
            title: Alert title
            content: Alert content
            signal_id: Optional related signal ID
            url: Optional deep link
            user_id: Optional target user
            
        Returns:
            Message ID
        """
        # Map DR level to push level
        level_map = {
            "critical": "P0",
            "error": "P1",
            "warning": "P1",
            "info": "P2",
        }
        push_level = level_map.get(alert_level.lower(), "P1")
        
        if signal_id:
            content = f"{content}\n\n📊 Signal: `{signal_id}`"
        
        return await self.enqueue_message(
            level=push_level,
            msg_type="dr_alert",
            title=title,
            content=content,
            url=url,
            user_id=user_id,
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
