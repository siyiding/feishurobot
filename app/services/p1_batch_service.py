"""
P1 Batch Notification Service.

Implements 30-minute batching window for P1 notifications:
- Messages are accumulated in a Redis sorted set keyed by time window
- After the window closes, batched messages are delivered together
- Supports configurable batch window duration
"""
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from collections import defaultdict

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


class P1BatchService:
    """
    P1 notification batching service.
    
    Accumulates P1 messages during a configurable time window (default 30 min),
    then delivers them as a single batch summary.
    """
    
    # Redis keys
    P1_BATCH_KEY = "push:batch:p1"
    P1_BATCH_META_KEY = "push:batch:p1:meta"
    BATCH_WINDOW_SECONDS = 1800  # 30 minutes default
    
    def __init__(self):
        settings = get_settings()
        self.redis_url = f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}/{settings.redis_db}" if settings.redis_password else f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        self._redis: Optional[redis.Redis] = None
        self._connected = False
        self._default_batch_window = 1800  # 30 minutes
    
    async def connect(self):
        """Establish Redis connection."""
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available. P1BatchService will operate in mock mode.")
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
                logger.info("Redis connection established for P1BatchService")
            except Exception as e:
                logger.warning(f"Redis not available: {e}. Operating in mock mode.")
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
    
    def _get_window_key(self, batch_window: int) -> str:
        """
        Get the batch window identifier.
        
        Uses time-based windows (e.g., "window_202604021030" for 30-min window
        starting at 10:30 on 2026-04-02).
        """
        now = datetime.utcnow()
        # Round down to nearest batch window
        minutes = (now.minute // (batch_window // 60)) * (batch_window // 60)
        window_start = now.replace(minute=minutes, second=0, microsecond=0)
        return window_start.strftime("%Y%m%d%H%M")
    
    async def add_to_batch(
        self,
        msg_type: str,
        title: str,
        content: str,
        url: Optional[str] = None,
        user_id: Optional[str] = None,
        batch_window: Optional[int] = None,
    ) -> str:
        """
        Add a message to the current P1 batch window.
        
        Args:
            msg_type: Message type
            title: Message title
            content: Message content
            url: Optional deep link
            user_id: Optional target user
            batch_window: Batch window in seconds (default 30 min)
            
        Returns:
            Batch entry ID
        """
        await self._ensure_connected()
        
        entry_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        window = batch_window or self._default_batch_window
        window_key = self._get_window_key(window)
        
        entry = {
            "id": entry_id,
            "msg_type": msg_type,
            "title": title,
            "content": content,
            "url": url or "",
            "user_id": user_id or "",
            "created_at": now,
            "window_key": window_key,
        }
        
        if not self._connected:
            logger.info(f"[MOCK] Added to P1 batch: {title}")
            return entry_id
        
        try:
            # Store entry in hash
            entry_key = f"{self.P1_BATCH_KEY}:{window_key}:{entry_id}"
            await self._redis.hset(entry_key, mapping=entry)
            
            # Set expiry for cleanup (2x window to allow for delivery)
            await self._redis.expire(entry_key, window * 2)
            
            # Add to window's entry list
            window_entries_key = f"{self.P1_BATCH_KEY}:entries:{window_key}"
            await self._redis.sadd(window_entries_key, entry_id)
            await self._redis.expire(window_entries_key, window * 2)
            
            # Update window metadata
            meta_key = f"{self.P1_BATCH_META_KEY}:{window_key}"
            await self._redis.hset(meta_key, mapping={
                "window_key": window_key,
                "start_time": now,
                "batch_window": str(window),
                "count": str(await self._redis.scard(window_entries_key)),
            })
            await self._redis.expire(meta_key, window * 2)
            
            logger.info(f"Added to P1 batch window {window_key}: {title}")
            
        except Exception as e:
            logger.error(f"Failed to add to P1 batch: {e}")
        
        return entry_id
    
    async def get_batch_messages(self, window_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all messages in a batch window.
        
        Args:
            window_key: Specific window to retrieve (default: current window)
            
        Returns:
            List of message entries in the window
        """
        await self._ensure_connected()
        
        if not self._connected:
            return []
        
        if window_key is None:
            window_key = self._get_window_key(self._default_batch_window)
        
        try:
            window_entries_key = f"{self.P1_BATCH_KEY}:entries:{window_key}"
            entry_ids = await self._redis.smembers(window_entries_key)
            
            messages = []
            for entry_id in entry_ids:
                entry_key = f"{self.P1_BATCH_KEY}:{window_key}:{entry_id}"
                entry = await self._redis.hgetall(entry_key)
                if entry:
                    messages.append(entry)
            
            return messages
            
        except Exception as e:
            logger.error(f"Failed to get batch messages: {e}")
            return []
    
    async def get_batch_summary(self, window_key: Optional[str] = None) -> str:
        """
        Generate a summary of batched messages for push notification.
        
        Args:
            window_key: Specific window to summarize (default: current window)
            
        Returns:
            Formatted summary string for Feishu message
        """
        messages = await self.get_batch_messages(window_key)
        
        if not messages:
            return ""
        
        # Group by msg_type
        by_type: Dict[str, List] = defaultdict(list)
        for msg in messages:
            by_type[msg.get("msg_type", "unknown")].append(msg)
        
        lines = [f"📬 **P1通知汇总**（{len(messages)}条）\n"]
        
        for msg_type, msgs in by_type.items():
            type_display = {
                "bug_update": "🐛 缺陷更新",
                "bug_new": "🐛 新建缺陷",
                "task_overdue": "⏰ 任务逾期",
                "dr_alert": "📊 DR告警",
                "coverage_update": "📈 覆盖率更新",
            }.get(msg_type, msg_type)
            
            lines.append(f"\n**{type_display}**（{len(msgs)}条）：")
            
            for msg in msgs[:5]:  # Show max 5 per type
                title = msg.get("title", "")[:30]
                lines.append(f"• {title}")
            
            if len(msgs) > 5:
                lines.append(f"• ... 还有{len(msgs) - 5}条")
        
        return "\n".join(lines)
    
    async def is_window_ready(self, window_key: Optional[str] = None) -> bool:
        """
        Check if a batch window is ready for delivery.
        
        A window is ready if:
        1. It has messages
        2. The window time has passed (messages won't be added anymore)
        
        Args:
            window_key: Window to check
            
        Returns:
            True if window is ready for delivery
        """
        await self._ensure_connected()
        
        if window_key is None:
            window_key = self._get_window_key(self._default_batch_window)
        
        if not self._connected:
            return False
        
        try:
            meta_key = f"{self.P1_BATCH_META_KEY}:{window_key}"
            meta = await self._redis.hgetall(meta_key)
            
            if not meta:
                return False
            
            # Parse window start time
            start_time_str = meta.get("start_time", "")
            if not start_time_str:
                return False
            
            start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            batch_window = int(meta.get("batch_window", self._default_batch_window))
            
            # Check if window has passed
            elapsed = (datetime.utcnow() - start_time.replace(tzinfo=None)).total_seconds()
            return elapsed >= batch_window
            
        except Exception as e:
            logger.error(f"Failed to check window readiness: {e}")
            return False
    
    async def clear_batch_window(self, window_key: str) -> bool:
        """
        Clear all messages in a batch window (after delivery).
        
        Args:
            window_key: Window to clear
            
        Returns:
            True if successful
        """
        await self._ensure_connected()
        
        if not self._connected:
            return True
        
        try:
            window_entries_key = f"{self.P1_BATCH_KEY}:entries:{window_key}"
            entry_ids = await self._redis.smembers(window_entries_key)
            
            # Delete all entry keys
            for entry_id in entry_ids:
                entry_key = f"{self.P1_BATCH_KEY}:{window_key}:{entry_id}"
                await self._redis.delete(entry_key)
            
            # Delete the entries set
            await self._redis.delete(window_entries_key)
            
            # Delete metadata
            meta_key = f"{self.P1_BATCH_META_KEY}:{window_key}"
            await self._redis.delete(meta_key)
            
            logger.info(f"Cleared P1 batch window {window_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to clear batch window: {e}")
            return False
    
    async def get_pending_windows(self) -> List[str]:
        """Get list of batch windows that have messages but aren't processed."""
        await self._ensure_connected()
        
        if not self._connected:
            return []
        
        try:
            pattern = f"{self.P1_BATCH_META_KEY}:*"
            windows = []
            async for key in self._redis.scan_iter(match=pattern):
                window_key = key.replace(f"{self.P1_BATCH_META_KEY}:", "")
                windows.append(window_key)
            return windows
        except Exception as e:
            logger.error(f"Failed to get pending windows: {e}")
            return []


# Singleton
_p1_batch_service: Optional[P1BatchService] = None


def get_p1_batch_service() -> P1BatchService:
    """Get singleton P1BatchService instance."""
    global _p1_batch_service
    if _p1_batch_service is None:
        _p1_batch_service = P1BatchService()
    return _p1_batch_service