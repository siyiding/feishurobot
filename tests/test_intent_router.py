"""Unit tests for intent recognition module."""
import pytest

from app.models.schemas import IntentType
from app.services.intent_router import recognize_intent, parse_command, route_command


class TestRecognizeIntent:
    """Tests for recognize_intent function."""

    def test_query_intent_查缺陷(self):
        """Test QUERY intent recognition for bug queries."""
        result = recognize_intent("查一下ICC项目的缺陷")
        assert result.intent == IntentType.QUERY
        assert result.confidence > 0.3

    def test_query_intent_未关闭(self):
        """Test QUERY intent with '未关闭' keyword."""
        result = recognize_intent("ICC项目未关闭的缺陷有哪些")
        assert result.intent == IntentType.QUERY
        assert "未关闭" in result.reason or "缺陷" in result.reason.lower()

    def test_action_intent_创建(self):
        """Test ACTION intent for creating bugs."""
        result = recognize_intent("创建一个缺陷：CAN总线异常")
        assert result.intent == IntentType.ACTION
        assert result.confidence > 0.1  # ACTION detected with reasonable confidence

    def test_action_intent_更新(self):
        """Test ACTION intent for updating status."""
        result = recognize_intent("把bug_001状态改成已解决")
        assert result.intent == IntentType.ACTION

    def test_report_intent_周报(self):
        """Test REPORT intent for weekly reports."""
        result = recognize_intent("生成本周周报")
        assert result.intent == IntentType.REPORT

    def test_report_intent_月报(self):
        """Test REPORT intent for monthly reports."""
        result = recognize_intent("生成3月份月报")
        assert result.intent == IntentType.REPORT

    def test_default_intent_empty_message(self):
        """Test default QUERY intent for empty messages."""
        result = recognize_intent("")
        assert result.intent == IntentType.QUERY
        assert result.confidence == 0.0

    def test_query_intent_项目列表(self):
        """Test QUERY intent for project list."""
        result = recognize_intent("有哪些项目")
        assert result.intent == IntentType.QUERY


class TestParseCommand:
    """Tests for parse_command function."""

    def test_parse_project_key(self):
        """Test project key extraction."""
        command = parse_command("查ICC项目的缺陷", IntentType.QUERY)
        assert command.params.get("project_key") == "ICC"

    def test_parse_project_key_with_hyphen(self):
        """Test project key extraction with hyphen."""
        command = parse_command("查ADAS-2024项目", IntentType.QUERY)
        # Key might include number part
        assert "ADAS" in (command.params.get("project_key") or "")

    def test_parse_status_open(self):
        """Test status extraction for open bugs."""
        command = parse_command("查未关闭的缺陷", IntentType.QUERY)
        assert command.params.get("status") == "open"

    def test_parse_status_in_progress(self):
        """Test status extraction for in-progress bugs."""
        command = parse_command("in_progress状态的缺陷", IntentType.QUERY)
        assert command.params.get("status") == "in_progress"

    def test_parse_priority_p0(self):
        """Test priority extraction."""
        command = parse_command("P0严重缺陷", IntentType.QUERY)
        assert command.params.get("priority") == "p0"

    def test_sub_command_query_bugs(self):
        """Test sub_command for bug query."""
        command = parse_command("查缺陷列表", IntentType.QUERY)
        assert command.sub_command == "query_bugs"

    def test_sub_command_create_bug(self):
        """Test sub_command for bug creation."""
        command = parse_command("创建一个缺陷", IntentType.ACTION)
        assert command.sub_command == "create_bug"

    def test_sub_command_weekly_report(self):
        """Test sub_command for weekly report."""
        command = parse_command("生成周报", IntentType.REPORT)
        assert command.sub_command == "weekly_report"


class TestRouteCommand:
    """Tests for route_command function."""

    def test_route_query_bugs(self):
        """Test routing for query_bugs."""
        command = parse_command("查ICC缺陷", IntentType.QUERY)
        handler, params = route_command(command)
        assert handler == "query.query_bugs"

    def test_route_query_projects(self):
        """Test routing for query_projects."""
        command = parse_command("项目列表", IntentType.QUERY)
        command.sub_command = "query_projects"
        handler, params = route_command(command)
        assert handler == "query.query_projects"

    def test_route_create_bug(self):
        """Test routing for create_bug."""
        command = parse_command("创建缺陷", IntentType.ACTION)
        handler, params = route_command(command)
        assert handler == "action.create_bug"

    def test_route_weekly_report(self):
        """Test routing for weekly_report."""
        command = parse_command("周报", IntentType.REPORT)
        handler, params = route_command(command)
        assert handler == "report.weekly_report"


class TestIntentConfidence:
    """Tests for intent confidence scoring."""

    def test_confidence_in_range(self):
        """Test confidence is always in [0, 1]."""
        messages = [
            "查ICC项目缺陷",
            "创建一个缺陷",
            "生成周报",
            "项目列表",
        ]
        for msg in messages:
            result = recognize_intent(msg)
            assert 0.0 <= result.confidence <= 1.0

    def test_empty_message_zero_confidence(self):
        """Test empty message has zero confidence."""
        result = recognize_intent("")
        assert result.confidence == 0.0
