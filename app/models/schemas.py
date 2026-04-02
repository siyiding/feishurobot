"""Pydantic schemas for the application."""
from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime


class IntentType(str, Enum):
    """Four types of user intent."""
    QUERY = "query"      # 查缺陷、查用例、查里程
    ACTION = "action"   # 创建缺陷、更新状态
    REPORT = "report"    # 生成周报/专项报告
    CONFIG = "config"   # M3: 推送配置管理
    CHANGE_AWARENESS = "change_awareness"  # M6: 变更感知


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


# ============== M6: Change Awareness & Regression (变更感知+回归助手) Schemas ==============

class GitProvider(str, Enum):
    """Git provider type."""
    GITHUB = "github"
    GITLAB = "gitlab"
    UNKNOWN = "unknown"


class GitMRStatus(str, Enum):
    """Merge Request status."""
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class GitChangedFile(BaseModel):
    """A single file changed in a commit/MR."""
    filename: str
    status: str = Field(default="modified")  # added, modified, deleted, renamed
    additions: int = 0
    deletions: int = 0


class GitCommit(BaseModel):
    """A single commit info."""
    sha: str
    message: str
    author: str
    author_email: str
    committed_at: str
    changed_files: List[GitChangedFile] = []


class GitMergeRequest(BaseModel):
    """Git Merge Request info."""
    mr_id: str
    title: str
    description: Optional[str] = None
    source_branch: str
    target_branch: str
    author: str
    status: GitMRStatus
    web_url: str
    created_at: str
    updated_at: str
    commits: List[GitCommit] = []
    changed_files: List[GitChangedFile] = []


class GitRepository(BaseModel):
    """Git repository configuration."""
    repo_id: str
    name: str
    full_name: str  # owner/repo format
    provider: GitProvider
    webhook_secret: Optional[str] = None
    api_url: Optional[str] = None  # For GitLab self-hosted


class ModuleMapping(BaseModel):
    """Module to test case mapping."""
    module_name: str
    file_patterns: List[str] = []  # Regex patterns to match changed files
    related_requirements: List[str] = []
    test_case_ids: List[str] = []


class ChangeCaseMatch(BaseModel):
    """Matching result between changed files and test cases."""
    changed_file: str
    matched_module: str
    matched_cases: List[str] = []
    match_confidence: float = Field(ge=0.0, le=1.0)


class RegressionSuggestion(BaseModel):
    """Regression test suggestion."""
    mr_id: str
    mr_title: str
    mr_url: str
    changed_modules: List[str] = []
    affected_cases: List[TestCaseInfo] = []
    match_details: List[ChangeCaseMatch] = []
    priority: BugPriority = BugPriority.P2
    reason: str
    suggested_by: str = "change_awareness"  # System identifier


class OTAChangeInfo(BaseModel):
    """OTA change information."""
    version: str
    title: str
    change_type: str = Field(default="feature")  # feature, bugfix, improvement, security
    keywords: List[str] = []
    affected_modules: List[str] = []
    description: Optional[str] = None
    released_at: Optional[str] = None


class OTAChangeMatch(BaseModel):
    """Matching result between OTA changes and test cases."""
    ota_change: OTAChangeInfo
    matched_cases: List[TestCaseInfo] = []
    match_reason: str


class TestCaseFailure(BaseModel):
    """Test case failure record."""
    case_id: str
    case_name: str
    module: str
    failure_time: str
    failure_reason: Optional[str] = None
    executor: Optional[str] = None
    related_dr_trips: List[str] = []  # DR trip IDs associated with this failure


class DRTripInfo(BaseModel):
    """DR (Data Report) trip information."""
    trip_id: str
    vehicle_id: str
    start_time: str
    end_time: Optional[str] = None
    distance_km: float = 0.0
    has_issues: bool = False
    screenshot_urls: List[str] = []


class FailureDRAssociation(BaseModel):
    """Association between test case failure and DR data."""
    failure: TestCaseFailure
    dr_trips: List[DRTripInfo] = []
    associated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    confidence: float = 0.0
