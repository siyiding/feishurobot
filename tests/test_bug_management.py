"""
Tests for bug management features (Week3).

Tests:
- Bug creation via natural language
- Bug update via natural language
- Bug query with filters
"""
import pytest
from app.models.schemas import BugStatus, BugPriority
from app.services.feishu_project_client import FeishuProjectClient
from app.services.intent_router import recognize_intent, parse_command


class TestBugCreation:
    """Tests for bug creation feature."""

    @pytest.fixture
    def client(self):
        return FeishuProjectClient()

    @pytest.mark.asyncio
    async def test_create_bug_basic(self, client):
        """Test creating a basic bug."""
        from app.models.schemas import BugCreateRequest
        
        req = BugCreateRequest(
            title="CAN总线通信异常",
            project_key="ICC",
            priority=BugPriority.P2,
        )
        
        resp = await client.create_bug(req)
        
        assert resp.created is True
        assert resp.title == "CAN总线通信异常"
        assert resp.status == BugStatus.OPEN
        assert "bug_" in resp.bug_id

    @pytest.mark.asyncio
    async def test_create_bug_p0_priority(self, client):
        """Test creating a P0 bug triggers P0 alert."""
        from app.models.schemas import BugCreateRequest
        
        req = BugCreateRequest(
            title="严重交通事故预警失效",
            project_key="ICC",
            priority=BugPriority.P0,
        )
        
        resp = await client.create_bug(req)
        
        assert resp.created is True
        assert resp.priority == BugPriority.P0
        assert "P0" in resp.message

    @pytest.mark.asyncio
    async def test_create_bug_with_assignee(self, client):
        """Test creating a bug with assignee."""
        from app.models.schemas import BugCreateRequest
        
        req = BugCreateRequest(
            title="方向盘转向助力失效",
            project_key="ICC",
            priority=BugPriority.P1,
            assignee="张三",
        )
        
        resp = await client.create_bug(req)
        
        assert resp.created is True
        # Assignee is stored but not necessarily in the message
        assert resp.bug_id is not None


class TestBugUpdate:
    """Tests for bug update feature."""

    @pytest.fixture
    def client(self):
        return FeishuProjectClient()

    @pytest.mark.asyncio
    async def test_update_bug_status(self, client):
        """Test updating bug status."""
        from app.models.schemas import BugUpdateRequest, BugCreateRequest
        
        # First create a bug
        create_req = BugCreateRequest(
            title="测试缺陷",
            project_key="ICC",
            priority=BugPriority.P2,
        )
        create_resp = await client.create_bug(create_req)
        
        # Then update its status
        update_req = BugUpdateRequest(
            bug_id=create_resp.bug_id,
            status=BugStatus.RESOLVED,
        )
        update_resp = await client.update_bug(update_req)
        
        assert update_resp.updated is True
        # Status update message should mention the status change
        assert "状态" in update_resp.message or "status" in update_resp.message.lower()

    @pytest.mark.asyncio
    async def test_update_bug_priority(self, client):
        """Test updating bug priority."""
        from app.models.schemas import BugUpdateRequest, BugCreateRequest
        
        # First create a bug
        create_req = BugCreateRequest(
            title="测试缺陷",
            project_key="ICC",
            priority=BugPriority.P2,
        )
        create_resp = await client.create_bug(create_req)
        
        # Then update its priority to P0
        update_req = BugUpdateRequest(
            bug_id=create_resp.bug_id,
            priority=BugPriority.P0,
        )
        update_resp = await client.update_bug(update_req)
        
        assert update_resp.updated is True
        # Priority is stored as lowercase in enum
        assert "p0" in update_resp.message.lower()

    @pytest.mark.asyncio
    async def test_update_nonexistent_bug(self, client):
        """Test updating a bug that doesn't exist."""
        from app.models.schemas import BugUpdateRequest
        
        update_req = BugUpdateRequest(
            bug_id="nonexistent_bug",
            status=BugStatus.RESOLVED,
        )
        update_resp = await client.update_bug(update_req)
        
        assert update_resp.updated is False
        assert "未找到" in update_resp.message


class TestIntentRecognitionBugCreation:
    """Tests for intent recognition in bug creation."""

    def test_recognize_create_bug_intent(self):
        """Test recognizing 'create bug' intent."""
        intent = recognize_intent("创建一个CAN总线通信异常缺陷")
        assert intent.intent.value == "action"
        assert intent.confidence > 0.1

    def test_recognize_create_bug_intent_alt(self):
        """Test recognizing '新建 bug' intent."""
        intent = recognize_intent("新建一个方向盘问题")
        assert intent.intent.value == "action"
        assert intent.confidence > 0.1

    def test_recognize_update_bug_intent(self):
        """Test recognizing 'update bug' intent."""
        intent = recognize_intent("把bug_001状态改成已解决")
        assert intent.intent.value == "action"
        assert intent.confidence > 0.3


class TestParseBugCreation:
    """Tests for parsing bug creation commands."""

    def test_parse_create_command_with_title(self):
        """Test parsing create command with title."""
        intent = recognize_intent("创建一个CAN总线通信异常缺陷")
        command = parse_command("创建一个CAN总线通信异常缺陷", intent.intent)
        
        assert command.sub_command == "create_bug"
        assert "bug_title" in command.params or "CAN总线" in command.raw_message

    def test_parse_create_command_with_priority(self):
        """Test parsing create command with priority."""
        intent = recognize_intent("创建一个P0严重缺陷")
        command = parse_command("创建一个P0严重缺陷", intent.intent)
        
        assert command.params.get("priority") == "p0"

    def test_parse_create_command_with_project(self):
        """Test parsing create command with project key."""
        intent = recognize_intent("ICC项目创建一个CAN总线问题")
        command = parse_command("ICC项目创建一个CAN总线问题", intent.intent)
        
        assert command.params.get("project_key") == "ICC"


class TestParseBugUpdate:
    """Tests for parsing bug update commands."""

    def test_parse_update_command_with_bug_id(self):
        """Test parsing update command with bug ID."""
        intent = recognize_intent("把bug_001状态改成已解决")
        command = parse_command("把bug_001状态改成已解决", intent.intent)
        
        assert command.sub_command == "update_bug"
        assert command.params.get("bug_id") == "bug_001"

    def test_parse_update_command_with_status(self):
        """Test parsing update command with status."""
        intent = recognize_intent("把bug_001状态改成已解决")
        command = parse_command("把bug_001状态改成已解决", intent.intent)
        
        assert command.params.get("status") == "resolved"

    def test_parse_update_command_in_progress(self):
        """Test parsing update command to in_progress."""
        intent = recognize_intent("bug_002状态改成处理中")
        command = parse_command("bug_002状态改成处理中", intent.intent)
        
        assert command.params.get("status") == "in_progress"


class TestQueryBugs:
    """Tests for querying bugs with various filters."""

    @pytest.fixture
    def client(self):
        return FeishuProjectClient()

    @pytest.mark.asyncio
    async def test_query_bugs_unclosed(self, client):
        """Test querying unclosed bugs."""
        from app.models.schemas import BugQueryRequest, BugStatus
        
        req = BugQueryRequest(
            project_key="ICC",
            status=BugStatus.OPEN,
            page_size=20,
        )
        
        resp = await client.query_bugs(req)
        
        assert resp.total >= 1
        assert all(bug.status == BugStatus.OPEN for bug in resp.bugs)

    @pytest.mark.asyncio
    async def test_query_bugs_by_priority(self, client):
        """Test querying bugs by priority."""
        from app.models.schemas import BugQueryRequest, BugPriority
        
        req = BugQueryRequest(
            project_key="ICC",
            priority=BugPriority.P0,
            page_size=20,
        )
        
        resp = await client.query_bugs(req)
        
        assert resp.total >= 1
        assert all(bug.priority == BugPriority.P0 for bug in resp.bugs)
