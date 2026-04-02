"""Pydantic schemas for the application."""
from enum import Enum
from typing import List, Optional, Any
from pydantic import BaseModel, Field


class IntentType(str, Enum):
    """Three types of user intent."""
    QUERY = "query"      # 查缺陷、查用例、查里程
    ACTION = "action"   # 创建缺陷、更新状态
    REPORT = "report"   # 生成周报/专项报告


class IntentConfidence(BaseModel):
    """Intent with confidence score."""
    intent: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    reason: Optional[str] = None


class BugStatus(str, Enum):
    """Bug status enum."""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REJECTED = "rejected"


class BugPriority(str, Enum):
    """Bug priority enum."""
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class ProjectInfo(BaseModel):
    """Project basic info."""
    project_id: str
    name: str
    key: str  # project key like "ICC"


class BugInfo(BaseModel):
    """Bug/Defect info from Feishu Project."""
    bug_id: str
    title: str
    status: BugStatus
    priority: BugPriority
    project_key: str
    project_name: str
    assignee: Optional[str] = None
    creator: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    description: Optional[str] = None


class BugQueryRequest(BaseModel):
    """Bug query request."""
    project_key: Optional[str] = None
    status: Optional[BugStatus] = None
    priority: Optional[BugPriority] = None
    assignee: Optional[str] = None
    page_size: int = Field(default=20, ge=1, le=100)


class BugQueryResponse(BaseModel):
    """Bug query response."""
    total: int
    bugs: List[BugInfo]
    page_token: Optional[str] = None


class FeishuWebhookEvent(BaseModel):
    """Feishu webhook event payload."""
    schema_: str = Field(alias="schema")
    event: str
    tenant_key: str
    app_id: str
    event_id: str
    event_time: int
    data: dict


class BotCommand(BaseModel):
    """Parsed bot command from user message."""
    raw_message: str
    intent: IntentType
    sub_command: Optional[str] = None
    params: dict = Field(default_factory=dict)


class BotResponse(BaseModel):
    """Bot response to user."""
    content: str
    intent: IntentType
    data: Optional[Any] = None
    error: Optional[str] = None
