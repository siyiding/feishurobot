"""
Feishu Project API Client.

Wraps the Feishu Project API for bug/task management.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    BugInfo, BugStatus, BugPriority, BugQueryRequest, BugQueryResponse,
    ProjectInfo, BugCreateRequest, BugCreateResponse, BugUpdateRequest, BugUpdateResponse
)

logger = get_logger(__name__)


class FeishuProjectClient:
    """
    Client for Feishu Project API.
    
    Provides methods for:
    - Listing projects
    - Querying bugs with filters
    - Creating/updating bugs (placeholder for M1)
    """

    def __init__(self, app_id: str = "", app_secret: str = ""):
        settings = get_settings()
        self.app_id = app_id or settings.feishu_app_id
        self.app_secret = app_secret or settings.feishu_app_secret
        self.base_url = settings.feishu_project_api_base
        self._token: Optional[str] = None
        self._token_expires_at: int = 0

    async def get_access_token(self) -> str:
        """Get Feishu API access token."""
        import time
        if self._token and time.time() < self._token_expires_at:
            return self._token
        
        # Placeholder: in production, call Feishu auth API
        # This requires actual app_id/app_secret
        logger.warning("Feishu access token not available - using mock mode")
        self._token = "mock_token"
        self._token_expires_at = int(time.time()) + 3600
        return self._token

    async def list_projects(self) -> List[ProjectInfo]:
        """
        List all accessible projects.
        
        Returns list of ProjectInfo.
        """
        logger.info("Listing projects...")
        # Placeholder implementation - requires actual API credentials
        # In production: call GET /open-apis/project/v2/projects
        return [
            ProjectInfo(project_id="proj_1", name="ICC整车测试", key="ICC"),
            ProjectInfo(project_id="proj_2", name="ADAS功能测试", key="ADAS"),
            ProjectInfo(project_id="proj_3", name="智能座舱测试", key="COCKPIT"),
        ]

    async def query_bugs(self, request: BugQueryRequest) -> BugQueryResponse:
        """
        Query bugs with filters.
        
        Args:
            request: BugQueryRequest with filters
            
        Returns:
            BugQueryResponse with matching bugs
        """
        logger.info(f"Querying bugs: project={request.project_key}, status={request.status}")
        
        # Placeholder - in production, call Feishu Project API
        # Example: GET /open-apis/baike/v1/bug_issues with query params
        mock_bugs = [
            BugInfo(
                bug_id="bug_001",
                title="CAN总线通信异常",
                status=BugStatus.OPEN,
                priority=BugPriority.P0,
                project_key=request.project_key or "ICC",
                project_name="ICC整车测试",
                assignee="张三",
                creator="李四",
                created_at="2026-04-01T10:00:00Z",
                updated_at="2026-04-01T10:00:00Z",
                description="CAN总线在高速行驶时出现通信中断",
            ),
            BugInfo(
                bug_id="bug_002",
                title="方向盘转向助力失效",
                status=BugStatus.IN_PROGRESS,
                priority=BugPriority.P0,
                project_key=request.project_key or "ICC",
                project_name="ICC整车测试",
                assignee="王五",
                creator="赵六",
                created_at="2026-03-30T14:30:00Z",
                updated_at="2026-04-01T09:00:00Z",
                description="方向盘转向助力在低温环境下失效",
            ),
            BugInfo(
                bug_id="bug_003",
                title="仪表盘显示花屏",
                status=BugStatus.RESOLVED,
                priority=BugPriority.P1,
                project_key=request.project_key or "ICC",
                project_name="ICC整车测试",
                assignee="孙七",
                creator="周八",
                created_at="2026-03-25T11:00:00Z",
                updated_at="2026-04-01T18:00:00Z",
                description="仪表盘在启动时偶发花屏问题",
            ),
        ]

        # Apply filters
        filtered = mock_bugs
        if request.project_key:
            filtered = [b for b in filtered if b.project_key == request.project_key]
        if request.status:
            filtered = [b for b in filtered if b.status == request.status]
        if request.priority:
            filtered = [b for b in filtered if b.priority == request.priority]
        if request.assignee:
            filtered = [b for b in filtered if b.assignee == request.assignee]

        return BugQueryResponse(
            total=len(filtered),
            bugs=filtered[: request.page_size],
        )

    async def get_bug(self, bug_id: str) -> Optional[BugInfo]:
        """Get a single bug by ID."""
        logger.info(f"Getting bug: {bug_id}")
        # Initialize mock data if needed
        self._init_mock_bugs()
        
        # Placeholder - in production, call Feishu Project API
        # GET /open-apis/baike/v1/bug_issues/{bug_id}
        for bug in self._mock_bugs:
            if bug.bug_id == bug_id:
                return bug
        return None

    # In-memory mock bug storage for demonstration
    _mock_bugs: List[BugInfo] = []

    def _init_mock_bugs(self):
        """Initialize mock bug data."""
        if not self._mock_bugs:
            self._mock_bugs = [
                BugInfo(
                    bug_id="bug_001",
                    title="CAN总线通信异常",
                    status=BugStatus.OPEN,
                    priority=BugPriority.P0,
                    project_key="ICC",
                    project_name="ICC整车测试",
                    assignee="张三",
                    creator="李四",
                    created_at="2026-04-01T10:00:00Z",
                    updated_at="2026-04-01T10:00:00Z",
                    description="CAN总线在高速行驶时出现通信中断",
                ),
                BugInfo(
                    bug_id="bug_002",
                    title="方向盘转向助力失效",
                    status=BugStatus.IN_PROGRESS,
                    priority=BugPriority.P0,
                    project_key="ICC",
                    project_name="ICC整车测试",
                    assignee="王五",
                    creator="赵六",
                    created_at="2026-03-30T14:30:00Z",
                    updated_at="2026-04-01T09:00:00Z",
                    description="方向盘转向助力在低温环境下失效",
                ),
                BugInfo(
                    bug_id="bug_003",
                    title="仪表盘显示花屏",
                    status=BugStatus.RESOLVED,
                    priority=BugPriority.P1,
                    project_key="ICC",
                    project_name="ICC整车测试",
                    assignee="孙七",
                    creator="周八",
                    created_at="2026-03-25T11:00:00Z",
                    updated_at="2026-04-01T18:00:00Z",
                    description="仪表盘在启动时偶发花屏问题",
                ),
            ]

    async def create_bug(self, request: BugCreateRequest) -> BugCreateResponse:
        """
        Create a new bug/issue.
        
        Args:
            request: BugCreateRequest with bug details
            
        Returns:
            BugCreateResponse with created bug info
        """
        logger.info(f"Creating bug: {request.title} in project {request.project_key}")
        
        # Initialize mock data if needed
        self._init_mock_bugs()
        
        # Generate new bug ID
        bug_id = f"bug_{str(uuid.uuid4())[:8]}"
        now = datetime.utcnow().isoformat() + "Z"
        
        # Get project name from known projects
        project_names = {
            "ICC": "ICC整车测试",
            "ADAS": "ADAS功能测试",
            "COCKPIT": "智能座舱测试",
        }
        project_name = project_names.get(request.project_key, request.project_key)
        
        # Create new bug
        new_bug = BugInfo(
            bug_id=bug_id,
            title=request.title,
            status=BugStatus.OPEN,
            priority=request.priority,
            project_key=request.project_key,
            project_name=project_name,
            assignee=request.assignee,
            creator=request.assignee or "系统",
            created_at=now,
            updated_at=now,
            description=request.description,
        )
        
        self._mock_bugs.append(new_bug)
        logger.info(f"Bug created: {bug_id}")
        
        return BugCreateResponse(
            bug_id=bug_id,
            title=request.title,
            status=BugStatus.OPEN,
            priority=request.priority,
            created=True,
            message=f"缺陷「{request.title}」创建成功！\nID: `{bug_id}`\n优先级: {request.priority.value.upper()}\n状态: 📋 待处理",
        )

    async def update_bug(self, request: BugUpdateRequest) -> BugUpdateResponse:
        """
        Update an existing bug.
        
        Args:
            request: BugUpdateRequest with bug ID and fields to update
            
        Returns:
            BugUpdateResponse with update result
        """
        logger.info(f"Updating bug: {request.bug_id}")
        
        # Initialize mock data if needed
        self._init_mock_bugs()
        
        # Find the bug
        bug = None
        for b in self._mock_bugs:
            if b.bug_id == request.bug_id:
                bug = b
                break
        
        if not bug:
            return BugUpdateResponse(
                bug_id=request.bug_id,
                updated=False,
                message=f"未找到缺陷 `{request.bug_id}`，请检查缺陷ID是否正确。",
            )
        
        # Apply updates
        now = datetime.utcnow().isoformat() + "Z"
        changes = []
        
        if request.status is not None:
            old_status = bug.status
            bug.status = request.status
            changes.append(f"状态: {old_status.value} → {request.status.value}")
        
        if request.priority is not None:
            old_priority = bug.priority
            bug.priority = request.priority
            changes.append(f"优先级: {old_priority.value} → {request.priority.value}")
        
        if request.assignee is not None:
            old_assignee = bug.assignee or "未分配"
            bug.assignee = request.assignee
            changes.append(f"指派人: {old_assignee} → {request.assignee}")
        
        if request.description is not None:
            bug.description = request.description
            changes.append("描述已更新")
        
        bug.updated_at = now
        
        if not changes:
            return BugUpdateResponse(
                bug_id=request.bug_id,
                updated=False,
                message="没有需要更新的字段。",
            )
        
        logger.info(f"Bug {request.bug_id} updated: {', '.join(changes)}")
        
        # Format status text for display
        status_text = {
            BugStatus.OPEN: "📋 待处理",
            BugStatus.IN_PROGRESS: "🔄 处理中",
            BugStatus.RESOLVED: "✅ 已解决",
            BugStatus.CLOSED: "🔒 已关闭",
            BugStatus.REJECTED: "❌ 已拒绝",
        }.get(bug.status, "❓ 未知")
        
        return BugUpdateResponse(
            bug_id=request.bug_id,
            updated=True,
            message=f"缺陷 `{request.bug_id}` 更新成功！\n" + "\n".join([f"• {c}" for c in changes]) + f"\n\n当前状态: {status_text}",
        )

    def format_bug_list(self, response: BugQueryResponse) -> str:
        """Format bug list for Feishu message."""
        if response.total == 0:
            return "没有找到符合条件的缺陷。"

        lines = [f"共找到 **{response.total}** 个缺陷：\n"]
        
        for bug in response.bugs:
            priority_emoji = {"p0": "🔴", "p1": "🟠", "p2": "🟡", "p3": "🟢"}.get(bug.priority.value, "⚪")
            status_text = {
                BugStatus.OPEN: "📋 待处理",
                BugStatus.IN_PROGRESS: "🔄 处理中",
                BugStatus.RESOLVED: "✅ 已解决",
                BugStatus.CLOSED: "🔒 已关闭",
                BugStatus.REJECTED: "❌ 已拒绝",
            }.get(bug.status, "❓ 未知")

            lines.append(
                f"{priority_emoji} **{bug.title}**\n"
                f"   状态: {status_text} | 优先级: {bug.priority.value.upper()}\n"
                f"   项目: {bug.project_name} | 指派给: {bug.assignee or '未分配'}\n"
                f"   ID: `{bug.bug_id}`\n"
            )

        return "\n".join(lines)


# Singleton instance
_project_client: Optional[FeishuProjectClient] = None


def get_project_client() -> FeishuProjectClient:
    """Get singleton FeishuProjectClient instance."""
    global _project_client
    if _project_client is None:
        _project_client = FeishuProjectClient()
    return _project_client
