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


class BugCreateRequest(BaseModel):
    """Bug creation request."""
    title: str
    project_key: str
    priority: BugPriority = BugPriority.P2
    description: Optional[str] = None
    assignee: Optional[str] = None


class BugUpdateRequest(BaseModel):
    """Bug update request."""
    bug_id: str
    status: Optional[BugStatus] = None
    priority: Optional[BugPriority] = None
    assignee: Optional[str] = None
    description: Optional[str] = None


class BugCreateResponse(BaseModel):
    """Bug creation response."""
    bug_id: str
    title: str
    status: BugStatus
    priority: BugPriority
    created: bool
    message: str


class BugUpdateResponse(BaseModel):
    """Bug update response."""
    bug_id: str
    updated: bool
    message: str


class PushMessage(BaseModel):
    """Push message for Redis queue."""
    id: str
    level: str = Field(default="P2")  # P0, P1, P2
    msg_type: str = Field(default="bug_update")  # bug_new, bug_update, task_overdue, dr_alert
    title: str
    content: str
    url: Optional[str] = None
    user_id: Optional[str] = None  # open_id for targeted push
    created_at: str
    enqueue_at: Optional[str] = None


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


# ============== Test Case (用例) Schemas ==============

class TestCaseType(str, Enum):
    """Test case type enum."""
    FUNCTION = "功能测试"
    PERFORMANCE = "性能测试"
    INTEGRATION = "集成测试"
    SYSTEM = "系统测试"
    SMOKE = "冒烟测试"
    REGRESSION = "回归测试"


class TestCaseStatus(str, Enum):
    """Test case execution status."""
    PENDING = "待执行"
    PASSED = "通过"
    FAILED = "失败"
    BLOCKED = "阻塞"
    SKIPPED = "跳过"


class TestCaseInfo(BaseModel):
    """Test case info from Feishu Sheets."""
    case_id: str
    case_name: str
    case_type: Optional[TestCaseType] = None
    module: Optional[str] = None
    related_requirement: Optional[str] = None
    priority: Optional[str] = None
    status: TestCaseStatus = TestCaseStatus.PENDING
    executor: Optional[str] = None
    execution_date: Optional[str] = None
    related_scene_ids: Optional[List[str]] = None
    notes: Optional[str] = None
    updater: Optional[str] = None
    updated_at: Optional[str] = None


class TestCaseQueryRequest(BaseModel):
    """Test case query request."""
    case_type: Optional[TestCaseType] = None
    module: Optional[str] = None
    status: Optional[TestCaseStatus] = None
    priority: Optional[str] = None
    executor: Optional[str] = None
    page_size: int = Field(default=20, ge=1, le=100)


class TestCaseQueryResponse(BaseModel):
    """Test case query response."""
    total: int
    cases: List[TestCaseInfo]
    page_token: Optional[str] = None


class TestCaseUpdateRequest(BaseModel):
    """Test case update request."""
    case_id: str
    status: Optional[TestCaseStatus] = None
    executor: Optional[str] = None
    execution_date: Optional[str] = None
    related_scene_ids: Optional[List[str]] = None
    notes: Optional[str] = None


class TestCaseUpdateResponse(BaseModel):
    """Test case update response."""
    case_id: str
    updated: bool
    message: str


# ============== Scene (场景) Schemas ==============

class SceneInfo(BaseModel):
    """Scene info for coverage tracking."""
    scene_id: str
    scene_name: str
    module: str
    related_requirements: List[str] = []
    coverage_rate: float = 0.0  # 0.0 - 1.0
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    blocked_cases: int = 0


class SceneCoverageUpdateRequest(BaseModel):
    """Scene coverage update request."""
    scene_id: str
    executed_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    blocked_cases: int = 0
