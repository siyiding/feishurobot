"""
Tests for M5 Bug Management Automation.

Tests:
- 7.1 L1 Automatic Bug Creation from natural language
- 7.2 Status Change Auto-Sync
- 7.3 Overdue Reminder
- 7.4 DR Suspicious Anomaly (optional)
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.models.schemas import BugStatus, BugPriority
from app.services.bug_automation_service import (
    BugCreationExtractor,
    BugChangeEvent,
    OverdueReminderService,
)


class TestBugCreationExtractor:
    """Tests for 7.1 L1 Automatic Bug Creation - NL extraction."""

    def test_extract_simple_bug_title(self):
        """Test extracting bug title from simple message."""
        message = "帮我提个缺陷：CAN总线通信异常"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["title"] == "CAN总线通信异常"
        assert result["confidence"] > 0

    def test_extract_bug_with_priority(self):
        """Test extracting bug with priority."""
        message = "帮我提个缺陷：方向盘助力失效，P0优先级"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["title"] == "方向盘助力失效"
        assert result["priority"] == "p0"

    def test_extract_bug_with_project(self):
        """Test extracting bug with project key."""
        message = "创建ICC项目缺陷：雷达检测异常，P1"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["title"] == "雷达检测异常"
        assert result["project_key"] == "ICC"
        assert result["priority"] == "p1"

    def test_extract_bug_with_assignee(self):
        """Test extracting bug with assignee."""
        message = "帮我提个缺陷：CAN总线异常，P2，指派给张三"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["title"] == "CAN总线异常"
        assert result["priority"] == "p2"
        assert result["assignee"] == "张三"

    def test_extract_bug_with_description(self):
        """Test extracting bug with description."""
        message = "帮我提个缺陷：CAN总线通信异常，P1，描述是在高速行驶时出现通信中断"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["title"] == "CAN总线通信异常"
        assert "高速行驶" in result["description"]

    def test_extract_aeb_project(self):
        """Test extracting AEB project bug."""
        message = "创建AEB项目问题：前方障碍物误检"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["project_key"] == "AEB"
        assert "前方障碍物误检" in result["title"]

    def test_extract_lcc_project(self):
        """Test extracting LCC project bug."""
        message = "新建LCC项目缺陷：车道居中偏移"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["project_key"] == "LCC"

    def test_default_priority_is_p2(self):
        """Test default priority is p2 when not specified."""
        message = "帮我提个缺陷：仪表盘花屏"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["priority"] == "p2"

    def test_default_project_is_icc(self):
        """Test default project is ICC when not specified."""
        message = "帮我提个缺陷：仪表盘花屏"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["project_key"] == "ICC"

    def test_extract_severity_keywords(self):
        """Test extracting severity keywords."""
        message = "帮我提个缺陷：严重交通事故预警失效，紧急"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["priority"] == "p0"

    def test_extract_p1_high_priority(self):
        """Test extracting P1 high priority."""
        message = "帮我提个缺陷：高优缺陷，P1"
        result = BugCreationExtractor.extract_from_message(message)
        
        assert result["priority"] == "p1"


class TestBugChangeEvent:
    """Tests for 7.2 Status Change Auto-Sync."""

    def test_status_change_detection(self):
        """Test detecting status change."""
        event = BugChangeEvent(
            bug_id="bug_001",
            title="Test bug",
            old_status=BugStatus.OPEN,
            new_status=BugStatus.IN_PROGRESS,
            old_priority=None,
            new_priority=None,
            old_assignee=None,
            new_assignee=None,
            changed_by="user_001",
        )
        
        assert event.has_status_change() is True
        assert event.has_priority_change() is False
        assert event.has_assignee_change() is False

    def test_priority_change_detection(self):
        """Test detecting priority change."""
        event = BugChangeEvent(
            bug_id="bug_001",
            title="Test bug",
            old_status=None,
            new_status=BugStatus.OPEN,
            old_priority=BugPriority.P2,
            new_priority=BugPriority.P0,
            old_assignee=None,
            new_assignee=None,
            changed_by="user_001",
        )
        
        assert event.has_priority_change() is True
        assert event.has_status_change() is False

    def test_assignee_change_detection(self):
        """Test detecting assignee change."""
        event = BugChangeEvent(
            bug_id="bug_001",
            title="Test bug",
            old_status=None,
            new_status=BugStatus.OPEN,
            old_priority=None,
            new_priority=None,
            old_assignee="张三",
            new_assignee="李四",
            changed_by="user_001",
        )
        
        assert event.has_assignee_change() is True

    def test_get_change_summary(self):
        """Test generating change summary."""
        event = BugChangeEvent(
            bug_id="bug_001",
            title="Test bug",
            old_status=BugStatus.OPEN,
            new_status=BugStatus.RESOLVED,
            old_priority=BugPriority.P2,
            new_priority=BugPriority.P0,
            old_assignee="张三",
            new_assignee="李四",
            changed_by="user_001",
        )
        
        summary = event.get_change_summary()
        assert "状态" in summary
        assert "优先级" in summary
        assert "负责人" in summary


class TestOverdueReminderService:
    """Tests for 7.3 Overdue Reminder."""

    def test_sla_hours_for_p0(self):
        """Test SLA hours for P0 priority."""
        service = OverdueReminderService()
        
        assert service.calculate_sla_hours(BugPriority.P0) == 1

    def test_sla_hours_for_p1(self):
        """Test SLA hours for P1 priority."""
        service = OverdueReminderService()
        
        assert service.calculate_sla_hours(BugPriority.P1) == 24

    def test_sla_hours_for_p2(self):
        """Test SLA hours for P2 priority."""
        service = OverdueReminderService()
        
        assert service.calculate_sla_hours(BugPriority.P2) == 72

    def test_sla_hours_for_p3(self):
        """Test SLA hours for P3 priority."""
        service = OverdueReminderService()
        
        assert service.calculate_sla_hours(BugPriority.P3) == 168

    def test_is_overdue_for_open_bug(self):
        """Test checking if open bug is overdue."""
        from app.models.schemas import BugInfo
        
        service = OverdueReminderService()
        
        # Bug created 2 hours ago, P0 SLA is 1 hour - should be overdue
        bug = BugInfo(
            bug_id="bug_001",
            title="Test bug",
            status=BugStatus.OPEN,
            priority=BugPriority.P0,
            project_key="ICC",
            project_name="ICC整车测试",
            created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        )
        
        assert service.is_overdue(bug) is True

    def test_is_not_overdue_for_new_bug(self):
        """Test new bug is not overdue."""
        from app.models.schemas import BugInfo
        
        service = OverdueReminderService()
        
        # Bug created 30 min ago, P0 SLA is 1 hour - should not be overdue
        bug = BugInfo(
            bug_id="bug_001",
            title="Test bug",
            status=BugStatus.OPEN,
            priority=BugPriority.P0,
            project_key="ICC",
            project_name="ICC整车测试",
            created_at=(datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        )
        
        assert service.is_overdue(bug) is False

    def test_closed_bug_not_overdue(self):
        """Test closed bug is not overdue regardless of age."""
        from app.models.schemas import BugInfo
        
        service = OverdueReminderService()
        
        # Bug created 100 hours ago, but closed - should not be overdue
        bug = BugInfo(
            bug_id="bug_001",
            title="Test bug",
            status=BugStatus.CLOSED,
            priority=BugPriority.P0,
            project_key="ICC",
            project_name="ICC整车测试",
            created_at=(datetime.now() - timedelta(hours=100)).isoformat() + "Z",
        )
        
        assert service.is_overdue(bug) is False

    def test_resolved_bug_not_overdue(self):
        """Test resolved bug is not overdue."""
        from app.models.schemas import BugInfo
        
        service = OverdueReminderService()
        
        bug = BugInfo(
            bug_id="bug_001",
            title="Test bug",
            status=BugStatus.RESOLVED,
            priority=BugPriority.P0,
            project_key="ICC",
            project_name="ICC整车测试",
            created_at=(datetime.now() - timedelta(hours=100)).isoformat() + "Z",
        )
        
        assert service.is_overdue(bug) is False

    def test_get_overdue_days(self):
        """Test calculating overdue days."""
        from app.models.schemas import BugInfo
        
        service = OverdueReminderService()
        
        # Bug created 3 days ago, P2 SLA is 72 hours (3 days)
        # With 1 hour buffer, should be about 2-3 days overdue
        bug = BugInfo(
            bug_id="bug_001",
            title="Test bug",
            status=BugStatus.OPEN,
            priority=BugPriority.P2,
            project_key="ICC",
            project_name="ICC整车测试",
            created_at=(datetime.now(timezone.utc) - timedelta(days=4)).isoformat().replace("+00:00", "Z"),
        )
        
        overdue_days = service.get_overdue_days(bug)
        assert overdue_days >= 1  # At least 1 day overdue


class TestNLQueryServicePriorityFix:
    """Test that the priority bug fix is in place."""

    def test_query_bugs_by_priority_priority(self):
        """Test query_bugs_by_priority has correct priority 11."""
        from app.services.nl_query_service import INTENT_TEMPLATES
        
        # The fix: query_bugs_by_priority should have priority 11
        # to avoid conflict with query_bugs_open (priority 10)
        template = INTENT_TEMPLATES.get("query_bugs_by_priority")
        assert template is not None
        assert template["priority"] == 11, "query_bugs_by_priority should have priority 11"

    def test_query_bugs_open_priority(self):
        """Test query_bugs_open has priority 10."""
        from app.services.nl_query_service import INTENT_TEMPLATES
        
        template = INTENT_TEMPLATES.get("query_bugs_open")
        assert template is not None
        assert template["priority"] == 10

    def test_query_bugs_by_project_has_higher_priority(self):
        """Test query_bugs_by_project has higher priority than query_bugs_open."""
        from app.services.nl_query_service import INTENT_TEMPLATES
        
        project_template = INTENT_TEMPLATES.get("query_bugs_by_project")
        open_template = INTENT_TEMPLATES.get("query_bugs_open")
        
        assert project_template["priority"] > open_template["priority"]
