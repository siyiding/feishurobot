"""Unit tests for Feishu Project Client."""
import pytest

from app.models.schemas import BugStatus, BugPriority, BugQueryRequest
from app.services.feishu_project_client import FeishuProjectClient


class TestFeishuProjectClient:
    """Tests for FeishuProjectClient."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return FeishuProjectClient(app_id="test_app", app_secret="test_secret")

    @pytest.mark.asyncio
    async def test_list_projects(self, client):
        """Test listing projects."""
        projects = await client.list_projects()
        assert len(projects) == 3
        keys = [p.key for p in projects]
        assert "ICC" in keys
        assert "ADAS" in keys

    @pytest.mark.asyncio
    async def test_query_bugs_no_filter(self, client):
        """Test querying bugs without filters."""
        req = BugQueryRequest()
        resp = await client.query_bugs(req)
        assert resp.total >= 0
        assert isinstance(resp.bugs, list)

    @pytest.mark.asyncio
    async def test_query_bugs_with_project_filter(self, client):
        """Test querying bugs with project filter."""
        req = BugQueryRequest(project_key="ICC")
        resp = await client.query_bugs(req)
        for bug in resp.bugs:
            assert bug.project_key == "ICC"

    @pytest.mark.asyncio
    async def test_query_bugs_with_status_filter(self, client):
        """Test querying bugs with status filter."""
        req = BugQueryRequest(status=BugStatus.OPEN)
        resp = await client.query_bugs(req)
        for bug in resp.bugs:
            assert bug.status == BugStatus.OPEN

    @pytest.mark.asyncio
    async def test_query_bugs_with_priority_filter(self, client):
        """Test querying bugs with priority filter."""
        req = BugQueryRequest(priority=BugPriority.P0)
        resp = await client.query_bugs(req)
        for bug in resp.bugs:
            assert bug.priority == BugPriority.P0

    @pytest.mark.asyncio
    async def test_get_bug(self, client):
        """Test getting a single bug."""
        bug = await client.get_bug("bug_001")
        assert bug is not None
        assert bug.bug_id == "bug_001"
        assert bug.title == "CAN总线通信异常"

    def test_format_bug_list(self, client):
        """Test bug list formatting."""
        from app.models.schemas import BugQueryResponse
        
        resp = BugQueryResponse(total=0, bugs=[])
        formatted = client.format_bug_list(resp)
        assert "没有找到" in formatted

        resp = BugQueryResponse(total=2, bugs=[
            {
                "bug_id": "bug_001",
                "title": "测试缺陷",
                "status": BugStatus.OPEN,
                "priority": BugPriority.P0,
                "project_key": "ICC",
                "project_name": "ICC整车测试",
                "assignee": "张三",
                "creator": None,
                "created_at": None,
                "updated_at": None,
                "description": None,
            }
        ])
        formatted = client.format_bug_list(resp)
        assert "测试缺陷" in formatted
        assert "ICC整车测试" in formatted
        assert "张三" in formatted
