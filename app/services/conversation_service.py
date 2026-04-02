"""
Multi-turn Conversation State Management.

Provides in-memory conversation context with Bitable persistence:
- Memory-first storage for fast access
- Bitable snapshot every 30 minutes
- Forced snapshot after 10 turns
- 2-hour context expiry
"""
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ConversationMessage:
    """A single message in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    intent: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationContext:
    """Context for a single conversation session."""
    user_id: str
    conversation_id: str
    messages: List[ConversationMessage] = field(default_factory=list)
    last_update: datetime = field(default_factory=datetime.now)
    project_key: Optional[str] = "ICC"  # Current project context
    last_query_type: Optional[str] = None  # Last query type for follow-ups
    turn_count: int = 0
    
    def add_message(self, role: str, content: str, intent: Optional[str] = None, params: Optional[Dict] = None):
        """Add a message to the conversation."""
        self.messages.append(ConversationMessage(
            role=role,
            content=content,
            timestamp=datetime.now(),
            intent=intent,
            params=params or {},
        ))
        self.last_update = datetime.now()
        self.turn_count += 1
    
    def to_dict(self) -> dict:
        """Serialize to dict for Bitable storage."""
        return {
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp.isoformat(),
                    "intent": m.intent,
                    "params": m.params,
                }
                for m in self.messages
            ],
            "last_update": self.last_update.isoformat(),
            "project_key": self.project_key,
            "last_query_type": self.last_query_type,
            "turn_count": self.turn_count,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ConversationContext":
        """Deserialize from dict."""
        messages = [
            ConversationMessage(
                role=m["role"],
                content=m["content"],
                timestamp=datetime.fromisoformat(m["timestamp"]),
                intent=m.get("intent"),
                params=m.get("params", {}),
            )
            for m in data.get("messages", [])
        ]
        return cls(
            user_id=data["user_id"],
            conversation_id=data["conversation_id"],
            messages=messages,
            last_update=datetime.fromisoformat(data["last_update"]),
            project_key=data.get("project_key"),
            last_query_type=data.get("last_query_type"),
            turn_count=data.get("turn_count", len(messages)),
        )


class ConversationService:
    """
    Multi-turn conversation state manager.
    
    Features:
    - In-memory storage for fast access
    - Bitable persistence for snapshots
    - Automatic cleanup of expired contexts
    - Context continuation for follow-up queries
    """
    
    # Configuration
    MAX_TURNS_BEFORE_SNAPSHOT = 10  # Force snapshot after 10 turns
    SNAPSHOT_INTERVAL_MINUTES = 30   # Bitable snapshot interval
    CONTEXT_EXPIRY_HOURS = 2        # Context expires after 2 hours
    
    def __init__(self):
        # In-memory storage: user_id -> conversation_id -> Context
        self._contexts: Dict[str, Dict[str, ConversationContext]] = defaultdict(dict)
        self._snapshot_timers: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
    
    def _get_conversation_id(self, user_id: str) -> str:
        """Get or create conversation ID for user."""
        # Use date-based conversation ID for daily context
        today = datetime.now().strftime("%Y-%m-%d")
        return f"{user_id}_{today}"
    
    async def get_context(self, user_id: str) -> ConversationContext:
        """
        Get or create conversation context for a user.
        
        First checks in-memory, then loads from Bitable if needed.
        """
        conv_id = self._get_conversation_id(user_id)
        
        # Check memory first
        if conv_id in self._contexts.get(user_id, {}):
            ctx = self._contexts[user_id][conv_id]
            # Check expiry
            if self._is_expired(ctx):
                logger.info(f"Context expired for {user_id}, creating new")
                del self._contexts[user_id][conv_id]
            else:
                return ctx
        
        # Try to load from Bitable
        ctx = await self._load_from_bitable(user_id, conv_id)
        if ctx is None:
            # Create new context
            ctx = ConversationContext(
                user_id=user_id,
                conversation_id=conv_id,
            )
        
        # Store in memory
        self._contexts[user_id][conv_id] = ctx
        
        # Start snapshot timer if needed
        self._ensure_snapshot_timer(user_id, conv_id)
        
        return ctx
    
    async def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        intent: Optional[str] = None,
        params: Optional[Dict] = None,
    ) -> ConversationContext:
        """Add a message to user's conversation and handle snapshots."""
        ctx = await self.get_context(user_id)
        
        # Add message
        ctx.add_message(role, content, intent, params)
        
        # Check if forced snapshot needed
        if ctx.turn_count >= self.MAX_TURNS_BEFORE_SNAPSHOT:
            logger.info(f"Forced snapshot after {ctx.turn_count} turns for {user_id}")
            await self._save_to_bitable(ctx)
            # Reset turn counter but keep context
            ctx.turn_count = 0
        
        # Update query type for follow-up context
        if intent:
            ctx.last_query_type = intent
        
        return ctx
    
    async def update_project_context(self, user_id: str, project_key: str):
        """Update the current project context for a user."""
        ctx = await self.get_context(user_id)
        ctx.project_key = project_key
        await self._save_to_bitable(ctx)
    
    async def get_recent_messages(self, user_id: str, count: int = 5) -> List[ConversationMessage]:
        """Get recent messages for context."""
        ctx = await self.get_context(user_id)
        return ctx.messages[-count:] if ctx.messages else []
    
    def _is_expired(self, ctx: ConversationContext) -> bool:
        """Check if context has expired."""
        expiry_time = ctx.last_update + timedelta(hours=self.CONTEXT_EXPIRY_HOURS)
        return datetime.now() > expiry_time
    
    async def _save_to_bitable(self, ctx: ConversationContext) -> bool:
        """
        Save context to Bitable for persistence.
        
        Returns True if saved successfully.
        """
        try:
            # Import here to avoid circular dependency
            from app.services.bitable_snapshot_service import get_bitable_snapshot_service
            snapshot_service = get_bitable_snapshot_service()
            await snapshot_service.save_conversation(ctx)
            logger.debug(f"Saved context to Bitable: {ctx.conversation_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to save context to Bitable: {e}")
            return False
    
    async def _load_from_bitable(self, user_id: str, conv_id: str) -> Optional[ConversationContext]:
        """Load context from Bitable if available."""
        try:
            from app.services.bitable_snapshot_service import get_bitable_snapshot_service
            snapshot_service = get_bitable_snapshot_service()
            ctx = await snapshot_service.load_conversation(user_id, conv_id)
            if ctx:
                logger.debug(f"Loaded context from Bitable: {conv_id}")
            return ctx
        except Exception as e:
            logger.warning(f"Failed to load context from Bitable: {e}")
            return None
    
    def _ensure_snapshot_timer(self, user_id: str, conv_id: str):
        """Ensure a snapshot timer is running for this conversation."""
        timer_key = f"{user_id}:{conv_id}"
        
        if timer_key in self._snapshot_timers:
            # Timer already running
            return
        
        # Create periodic snapshot task
        async def snapshot_periodically():
            while True:
                await asyncio.sleep(self.SNAPSHOT_INTERVAL_MINUTES * 60)
                
                async with self._lock:
                    if conv_id in self._contexts.get(user_id, {}):
                        ctx = self._contexts[user_id][conv_id]
                        if not self._is_expired(ctx):
                            await self._save_to_bitable(ctx)
                            logger.debug(f"Periodic snapshot saved: {conv_id}")
                        else:
                            # Context expired, stop timer
                            break
        
        task = asyncio.create_task(snapshot_periodically())
        self._snapshot_timers[timer_key] = task
    
    def format_conversation_history(self, ctx: ConversationContext, count: int = 5) -> str:
        """Format recent conversation for display."""
        recent = ctx.messages[-count:] if ctx.messages else []
        if not recent:
            return ""
        
        lines = ["**最近对话：**\n"]
        for msg in recent:
            role_label = "👤" if msg.role == "user" else "🤖"
            time_str = msg.timestamp.strftime("%H:%M")
            lines.append(f"{role_label} [{time_str}] {msg.content[:50]}...")
        
        return "\n".join(lines)
    
    async def clear_context(self, user_id: str) -> bool:
        """Clear conversation context for a user."""
        conv_id = self._get_conversation_id(user_id)
        
        # Remove from memory
        if conv_id in self._contexts.get(user_id, {}):
            del self._contexts[user_id][conv_id]
        
        # Stop timer
        timer_key = f"{user_id}:{conv_id}"
        if timer_key in self._snapshot_timers:
            self._snapshot_timers[timer_key].cancel()
            del self._snapshot_timers[timer_key]
        
        return True


# Singleton
_conversation_service: Optional[ConversationService] = None


def get_conversation_service() -> ConversationService:
    """Get singleton ConversationService instance."""
    global _conversation_service
    if _conversation_service is None:
        _conversation_service = ConversationService()
    return _conversation_service
