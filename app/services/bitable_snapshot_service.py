"""
Bitable Snapshot Service for Conversation Persistence.

Provides Bitable-backed storage for conversation contexts:
- Saves conversation snapshots to Feishu Bitable
- Loads conversation history on demand
- Used as backup for in-memory conversation state
"""
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.conversation_service import ConversationContext

logger = get_logger(__name__)


class BitableSnapshotService:
    """
    Bitable-backed snapshot storage for conversations.
    
    Stores conversation contexts in a Feishu Bitable table
    with the following fields:
    - user_id: User identifier
    - conversation_id: Daily conversation ID
    - context_data: JSON serialized conversation
    - last_update: Last update timestamp
    - turn_count: Number of turns in this session
    """
    
    # Bitable configuration
    APP_TOKEN = "EU27soF8whsnFmtF6MCc59HqnWh"  # Same as sheet for now
    TABLE_NAME = "conversation_snapshots"
    TABLE_ID = "tblconversation"  # Will be created
    
    def __init__(self):
        settings = get_settings()
        self.app_id = settings.feishu_app_id
        self.app_secret = settings.feishu_app_secret
        self._token: Optional[str] = None
        self._token_expires_at: int = 0
        self._base_url = "https://open.feishu.cn/open-apis"
        self._table_id: Optional[str] = None
    
    async def get_access_token(self) -> str:
        """Get Feishu tenant access token."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        
        import urllib.request
        
        url = f"{self._base_url}/auth/v3/tenant_access_token/internal"
        data = json.dumps({
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }).encode('utf-8')
        
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            
            if result.get("code") == 0:
                self._token = result["tenant_access_token"]
                self._token_expires_at = time.time() + result.get("expire", 7200)
                return self._token
            else:
                raise Exception(f"Token error: {result.get('msg')}")
        except Exception as e:
            logger.error(f"Token request failed: {e}")
            raise
    
    async def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        """Make an API request to Feishu."""
        import urllib.request
        
        token = await self.get_access_token()
        url = f"{self._base_url}{path}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        try:
            if data:
                req_data = json.dumps(data).encode('utf-8')
                req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
            else:
                req = urllib.request.Request(url, headers=headers, method=method)
            
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            
            return result
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return {"code": -1, "msg": str(e)}
    
    async def _ensure_table(self) -> str:
        """Ensure the conversation snapshots table exists."""
        if self._table_id:
            return self._table_id
        
        # List tables in the app
        result = await self._request(
            "GET",
            f"/bitable/v1/apps/{self.APP_TOKEN}/tables"
        )
        
        if result.get("code") == 0:
            tables = result.get("data", {}).get("items", [])
            for table in tables:
                if table.get("name") == self.TABLE_NAME:
                    self._table_id = table.get("table_id")
                    return self._table_id
        
        # Create table if not exists
        result = await self._request(
            "POST",
            f"/bitable/v1/apps/{self.APP_TOKEN}/tables",
            {"table": {"name": self.TABLE_NAME}}
        )
        
        if result.get("code") == 0:
            self._table_id = result.get("data", {}).get("table_id")
            
            # Add fields to the table
            await self._add_table_fields(self._table_id)
            
            return self._table_id
        else:
            logger.error(f"Failed to create table: {result}")
            raise Exception(f"Table creation failed: {result.get('msg')}")
    
    async def _add_table_fields(self, table_id: str):
        """Add required fields to the snapshots table."""
        fields = [
            {"field_name": "user_id", "type": 1},  # Text
            {"field_name": "conversation_id", "type": 1},  # Text
            {"field_name": "context_data", "type": 1},  # Text (JSON)
            {"field_name": "last_update", "type": 5},  # DateTime
            {"field_name": "turn_count", "type": 2},  # Number
            {"field_name": "project_key", "type": 1},  # Text
        ]
        
        for field_def in fields:
            try:
                await self._request(
                    "POST",
                    f"/bitable/v1/apps/{self.APP_TOKEN}/tables/{table_id}/fields",
                    field_def
                )
            except Exception as e:
                logger.debug(f"Field creation warning (may already exist): {e}")
    
    async def save_conversation(self, ctx: ConversationContext) -> bool:
        """
        Save conversation context to Bitable.
        
        Updates existing record if exists, otherwise creates new.
        """
        try:
            table_id = await self._ensure_table()
            
            # First, try to find existing record
            filter_cond = {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "user_id",
                        "operator": "is",
                        "value": [ctx.user_id]
                    },
                    {
                        "field_name": "conversation_id", 
                        "operator": "is",
                        "value": [ctx.conversation_id]
                    }
                ]
            }
            
            # Search for existing record
            result = await self._request(
                "POST",
                f"/bitable/v1/apps/{self.APP_TOKEN}/tables/{table_id}/records/search",
                {
                    "page_size": 1,
                    "filter": filter_cond,
                }
            )
            
            record_data = {
                "fields": {
                    "user_id": ctx.user_id,
                    "conversation_id": ctx.conversation_id,
                    "context_data": json.dumps(ctx.to_dict(), ensure_ascii=False),
                    "last_update": int(ctx.last_update.timestamp() * 1000),
                    "turn_count": ctx.turn_count,
                    "project_key": ctx.project_key or "ICC",
                }
            }
            
            if result.get("code") == 0 and result.get("data", {}).get("items"):
                # Update existing record
                record_id = result["data"]["items"][0]["record_id"]
                await self._request(
                    "PUT",
                    f"/bitable/v1/apps/{self.APP_TOKEN}/tables/{table_id}/records/{record_id}",
                    record_data
                )
                logger.debug(f"Updated conversation snapshot: {ctx.conversation_id}")
            else:
                # Create new record
                await self._request(
                    "POST",
                    f"/bitable/v1/apps/{self.APP_TOKEN}/tables/{table_id}/records",
                    record_data
                )
                logger.debug(f"Created conversation snapshot: {ctx.conversation_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save conversation: {e}")
            return False
    
    async def load_conversation(self, user_id: str, conversation_id: str) -> Optional[ConversationContext]:
        """
        Load conversation context from Bitable.
        
        Returns None if not found.
        """
        try:
            table_id = await self._ensure_table()
            
            # Search for the record
            filter_cond = {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "user_id",
                        "operator": "is",
                        "value": [user_id]
                    },
                    {
                        "field_name": "conversation_id",
                        "operator": "is", 
                        "value": [conversation_id]
                    }
                ]
            }
            
            result = await self._request(
                "POST",
                f"/bitable/v1/apps/{self.APP_TOKEN}/tables/{table_id}/records/search",
                {
                    "page_size": 1,
                    "filter": filter_cond,
                }
            )
            
            if result.get("code") == 0 and result.get("data", {}).get("items"):
                fields = result["data"]["items"][0].get("fields", {})
                context_data = fields.get("context_data")
                
                if context_data:
                    data = json.loads(context_data)
                    return ConversationContext.from_dict(data)
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to load conversation: {e}")
            return None


# Singleton
_bitable_snapshot_service: Optional[BitableSnapshotService] = None


def get_bitable_snapshot_service() -> BitableSnapshotService:
    """Get singleton BitableSnapshotService instance."""
    global _bitable_snapshot_service
    if _bitable_snapshot_service is None:
        _bitable_snapshot_service = BitableSnapshotService()
    return _bitable_snapshot_service
