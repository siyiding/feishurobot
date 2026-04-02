"""
Push Configuration Service.

Stores and manages user push notification preferences:
- P1 batch window preferences
- P2 frequency settings
- Preferred push time windows
- Notification channel preferences
"""
import json
from datetime import datetime
from typing import Optional, List, Dict
from enum import Enum

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class PushFrequency(str, Enum):
    """Push notification frequency options."""
    REAL_TIME = "real_time"      # 实时推送
    HOURLY = "hourly"            # 每小时合并
    DAILY = "daily"              # 每日汇总
    WEEKLY = "weekly"            # 每周汇总
    OFF = "off"                  # 关闭推送


class PushLevel(str, Enum):
    """Push notification level."""
    P0 = "P0"    # 严重告警，立即推送
    P1 = "P1"    # 一般通知，批量推送
    P2 = "P2"    # 提醒，可选频率


class PushConfig(BaseModel):
    """User push notification configuration."""
    model_config = ConfigDict(use_enum_values=True)
    
    user_id: str
    p1_batch_minutes: int = 30           # P1通知合并窗口（分钟），默认30分钟
    p2_frequency: PushFrequency = PushFrequency.HOURLY  # P2推送频率
    push_enabled: bool = True            # 是否启用推送
    quiet_hours_start: Optional[str] = None  # 免打扰开始时间 "22:00"
    quiet_hours_end: Optional[str] = None    # 免打扰结束时间 "08:00"
    channels: List[str] = ["feishu"]     # 推送渠道
    weekly_report_day: str = "friday"     # 周报推送日
    weekly_report_time: str = "17:00"     # 周报推送时间
    
    # P0 always on, no config needed
    # P1: batch window configurable
    # P2: frequency configurable


class PushConfigService:
    """
    Service for managing push notification preferences.
    
    Uses Redis Hash to store per-user configurations.
    """
    
    CONFIG_KEY_PREFIX = "push:config:"
    DEFAULT_CONFIG_KEY = "push:config:default"
    
    def __init__(self):
        settings = get_settings()
        self.redis_url = f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}/{settings.redis_db}" if settings.redis_password else f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        self._redis: Optional[redis.Redis] = None
        self._connected = False
    
    async def connect(self):
        """Establish Redis connection."""
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available. PushConfigService will operate in mock mode.")
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
                logger.info("Redis connection established for PushConfigService")
            except Exception as e:
                logger.warning(f"Redis not available: {e}. Using in-memory config.")
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
    
    def _get_user_key(self, user_id: str) -> str:
        """Get Redis key for user config."""
        return f"{self.CONFIG_KEY_PREFIX}{user_id}"
    
    async def get_user_config(self, user_id: str) -> PushConfig:
        """
        Get push configuration for a user.
        
        Args:
            user_id: User's open_id
            
        Returns:
            PushConfig for the user, or default config if not set
        """
        await self._ensure_connected()
        
        if not self._connected:
            # Return default config in mock mode
            return PushConfig(user_id=user_id)
        
        try:
            key = self._get_user_key(user_id)
            data = await self._redis.hgetall(key)
            
            if not data:
                # Return default config
                return PushConfig(user_id=user_id)
            
            # Parse stored config
            config = PushConfig(
                user_id=user_id,
                p1_batch_minutes=int(data.get("p1_batch_minutes", 30)),
                p2_frequency=PushFrequency(data.get("p2_frequency", "hourly")),
                push_enabled=data.get("push_enabled", "true").lower() == "true",
                quiet_hours_start=data.get("quiet_hours_start") or None,
                quiet_hours_end=data.get("quiet_hours_end") or None,
                channels=json.loads(data.get("channels", '["feishu"]')),
                weekly_report_day=data.get("weekly_report_day", "friday"),
                weekly_report_time=data.get("weekly_report_time", "17:00"),
            )
            return config
            
        except Exception as e:
            logger.error(f"Failed to get user config: {e}")
            return PushConfig(user_id=user_id)
    
    async def update_user_config(self, user_id: str, config: PushConfig) -> bool:
        """
        Update push configuration for a user.
        
        Args:
            user_id: User's open_id
            config: New configuration to save
            
        Returns:
            True if successful
        """
        await self._ensure_connected()
        
        config.user_id = user_id  # Ensure user_id matches
        
        if not self._connected:
            logger.info(f"[MOCK] Updated push config for user {user_id}")
            return True
        
        try:
            key = self._get_user_key(user_id)
            data = {
                "p1_batch_minutes": str(config.p1_batch_minutes),
                "p2_frequency": config.p2_frequency.value if isinstance(config.p2_frequency, Enum) else config.p2_frequency,
                "push_enabled": str(config.push_enabled).lower(),
                "quiet_hours_start": config.quiet_hours_start or "",
                "quiet_hours_end": config.quiet_hours_end or "",
                "channels": json.dumps(config.channels),
                "weekly_report_day": config.weekly_report_day,
                "weekly_report_time": config.weekly_report_time,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
            
            await self._redis.hset(key, mapping=data)
            logger.info(f"Updated push config for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update user config: {e}")
            return False
    
    async def delete_user_config(self, user_id: str) -> bool:
        """Delete push configuration for a user (reset to default)."""
        await self._ensure_connected()
        
        if not self._connected:
            logger.info(f"[MOCK] Deleted push config for user {user_id}")
            return True
        
        try:
            key = self._get_user_key(user_id)
            await self._redis.delete(key)
            logger.info(f"Deleted push config for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete user config: {e}")
            return False
    
    async def is_in_quiet_hours(self, user_id: str) -> bool:
        """
        Check if current time is in user's quiet hours.
        
        Args:
            user_id: User's open_id
            
        Returns:
            True if in quiet hours (push should be suppressed)
        """
        config = await self.get_user_config(user_id)
        
        if not config.quiet_hours_start or not config.quiet_hours_end:
            return False
        
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        
        start = config.quiet_hours_start
        end = config.quiet_hours_end
        
        # Handle overnight quiet hours (e.g., 22:00 - 08:00)
        if start <= end:
            return start <= current_time <= end
        else:
            return current_time >= start or current_time <= end
    
    async def should_push(self, user_id: str, level: PushLevel) -> bool:
        """
        Check if push should be sent based on user preferences.
        
        Args:
            user_id: User's open_id
            level: Push level (P0/P1/P2)
            
        Returns:
            True if push should be sent
        """
        config = await self.get_user_config(user_id)
        
        # P0 always pushed (unless globally disabled - which we don't support)
        if level == PushLevel.P0:
            return True
        
        # Check if push is globally enabled
        if not config.push_enabled:
            return False
        
        # Check quiet hours (except P0)
        if await self.is_in_quiet_hours(user_id):
            return False
        
        return True
    
    async def get_all_user_configs(self) -> List[PushConfig]:
        """Get all user configurations (for admin/monitoring)."""
        await self._ensure_connected()
        
        if not self._connected:
            return []
        
        try:
            pattern = f"{self.CONFIG_KEY_PREFIX}*"
            keys = []
            async for key in self._redis.scan_iter(match=pattern):
                keys.append(key)
            
            configs = []
            for key in keys:
                user_id = key.replace(self.CONFIG_KEY_PREFIX, "")
                config = await self.get_user_config(user_id)
                configs.append(config)
            
            return configs
        except Exception as e:
            logger.error(f"Failed to get all user configs: {e}")
            return []


# Singleton instance
_push_config_service: Optional[PushConfigService] = None


def get_push_config_service() -> PushConfigService:
    """Get singleton PushConfigService instance."""
    global _push_config_service
    if _push_config_service is None:
        _push_config_service = PushConfigService()
    return _push_config_service