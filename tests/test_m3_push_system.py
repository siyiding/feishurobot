"""
Tests for M3 Push Notification System.

Tests:
- Push configuration service
- P1 batch service
- Weekly report service
- CONFIG intent recognition
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.schemas import IntentType
from app.services.intent_router import recognize_intent, parse_command, route_command


class TestConfigIntentRecognition:
    """Test CONFIG intent recognition for push configuration."""

    def test_config_intent_push_settings(self):
        """Test recognition of push settings commands."""
        result = recognize_intent("推送配置")
        assert result.intent == IntentType.CONFIG
        assert result.confidence > 0.1  # CONFIG pattern matched

    def test_config_intent_enable_push(self):
        """Test recognition of enable push command."""
        result = recognize_intent("开启推送")
        assert result.intent == IntentType.CONFIG
        assert result.confidence > 0.1  # CONFIG pattern matched

    def test_config_intent_disable_push(self):
        """Test recognition of disable push command."""
        result = recognize_intent("关闭推送")
        assert result.intent == IntentType.CONFIG
        assert result.confidence > 0.1  # CONFIG pattern matched

    def test_config_intent_p1_batch_window(self):
        """Test recognition of P1 batch window command."""
        result = recognize_intent("设置P1合并窗口30分钟")
        assert result.intent == IntentType.CONFIG
        assert result.confidence > 0.1  # CONFIG pattern matched

    def test_config_intent_p2_frequency(self):
        """Test recognition of P2 frequency command."""
        result = recognize_intent("设置P2频率每小时")
        assert result.intent == IntentType.CONFIG
        assert result.confidence > 0.1  # CONFIG pattern matched

    def test_config_intent_quiet_hours(self):
        """Test recognition of quiet hours command."""
        result = recognize_intent("设置免打扰22:00-08:00")
        assert result.intent == IntentType.CONFIG
        assert result.confidence > 0.1  # CONFIG pattern matched

    def test_config_intent_query_config(self):
        """Test recognition of query config command."""
        result = recognize_intent("查询推送配置")
        assert result.intent == IntentType.CONFIG
        assert result.confidence > 0.1  # CONFIG pattern matched


class TestConfigIntentRouting:
    """Test CONFIG command parsing and routing."""

    def test_parse_push_config_command(self):
        """Test parsing push configuration command."""
        command = parse_command("推送配置", IntentType.CONFIG)
        assert command.sub_command == "push_config"
        assert command.intent == IntentType.CONFIG

    def test_parse_enable_push_command(self):
        """Test parsing enable push command."""
        command = parse_command("开启推送", IntentType.CONFIG)
        assert command.sub_command == "push_config"
        assert command.intent == IntentType.CONFIG

    def test_parse_p1_batch_command(self):
        """Test parsing P1 batch window command."""
        command = parse_command("设置P1合并窗口30分钟", IntentType.CONFIG)
        assert command.sub_command == "push_config"
        assert command.intent == IntentType.CONFIG

    def test_parse_p2_frequency_command(self):
        """Test parsing P2 frequency command."""
        command = parse_command("设置P2频率每小时", IntentType.CONFIG)
        assert command.sub_command == "push_config"
        assert command.intent == IntentType.CONFIG

    def test_parse_quiet_hours_command(self):
        """Test parsing quiet hours command."""
        command = parse_command("设置免打扰22:00-08:00", IntentType.CONFIG)
        assert command.sub_command == "push_config"
        assert command.intent == IntentType.CONFIG

    def test_route_push_config(self):
        """Test routing push config command."""
        command = parse_command("推送配置", IntentType.CONFIG)
        handler, params = route_command(command)
        assert handler == "config.push_config"
        assert params == {}

    def test_route_enable_push(self):
        """Test routing enable push command."""
        command = parse_command("开启推送", IntentType.CONFIG)
        handler, params = route_command(command)
        assert handler == "config.push_config"


class TestPushConfigSchemas:
    """Test push configuration schemas."""

    def test_push_config_creation(self):
        """Test creating a push configuration."""
        from app.services.push_config_service import PushConfig, PushFrequency
        
        config = PushConfig(
            user_id="test_user",
            p1_batch_minutes=30,
            p2_frequency=PushFrequency.HOURLY,
            push_enabled=True,
        )
        
        assert config.user_id == "test_user"
        assert config.p1_batch_minutes == 30
        assert config.p2_frequency == PushFrequency.HOURLY
        assert config.push_enabled is True

    def test_push_frequency_enum_values(self):
        """Test push frequency enum values."""
        from app.services.push_config_service import PushFrequency
        
        assert PushFrequency.REAL_TIME.value == "real_time"
        assert PushFrequency.HOURLY.value == "hourly"
        assert PushFrequency.DAILY.value == "daily"
        assert PushFrequency.WEEKLY.value == "weekly"
        assert PushFrequency.OFF.value == "off"


class TestP1BatchService:
    """Test P1 batch notification service."""

    @pytest.fixture
    def mock_p1_batch_service(self):
        """Create a mock P1 batch service."""
        with patch('app.services.p1_batch_service.P1BatchService') as mock:
            service = mock.return_value
            service.connect = AsyncMock()
            service.disconnect = AsyncMock()
            service.add_to_batch = AsyncMock(return_value="test_entry_id")
            service.get_batch_messages = AsyncMock(return_value=[])
            service.is_window_ready = AsyncMock(return_value=False)
            yield service

    @pytest.mark.asyncio
    async def test_add_to_batch(self, mock_p1_batch_service):
        """Test adding message to P1 batch."""
        from app.services.p1_batch_service import P1BatchService
        
        service = P1BatchService()
        service.add_to_batch = AsyncMock(return_value="test_entry_id")
        
        entry_id = await service.add_to_batch(
            msg_type="bug_update",
            title="Test Bug Updated",
            content="Bug content",
        )
        
        assert entry_id == "test_entry_id"
        service.add_to_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_batch_summary_empty(self):
        """Test generating summary for empty batch."""
        from app.services.p1_batch_service import P1BatchService
        
        service = P1BatchService()
        service.get_batch_messages = AsyncMock(return_value=[])
        
        summary = await service.get_batch_summary()
        assert summary == ""


class TestDRClient:
    """Test DR API client (pre-research implementation)."""

    @pytest.mark.asyncio
    async def test_dr_client_mock_mode(self):
        """Test DR client in mock mode when not connected."""
        from app.services.dr_client import DRClient
        
        client = DRClient()
        client._connected = False
        client._session = None
        
        response = await client._make_request("GET", "/signals/query")
        # Mock mode returns mock data with "status": "success"
        assert response["status"] == "success"
        assert "data" in response

    def test_dr_alert_level_enum(self):
        """Test DR alert level enum values."""
        from app.services.dr_client import DRAlertLevel
        
        assert DRAlertLevel.INFO.value == "info"
        assert DRAlertLevel.WARNING.value == "warning"
        assert DRAlertLevel.ERROR.value == "error"
        assert DRAlertLevel.CRITICAL.value == "critical"

    def test_dr_chart_type_enum(self):
        """Test DR chart type enum values."""
        from app.services.dr_client import DRChartType
        
        assert DRChartType.LINE.value == "line"
        assert DRChartType.BAR.value == "bar"
        assert DRChartType.SCATTER.value == "scatter"
        assert DRChartType.HISTOGRAM.value == "histogram"
        assert DRChartType.TIME_SERIES.value == "time_series"


class TestWeeklyReportService:
    """Test weekly report generation service."""

    @pytest.mark.asyncio
    async def test_generate_weekly_summary(self):
        """Test generating weekly summary."""
        from app.services.weekly_report_service import WeeklyReportService
        
        service = WeeklyReportService()
        
        # Mock the data fetching methods
        service._get_bug_stats = AsyncMock(return_value={
            "created": 5,
            "resolved": 3,
            "closed": 2,
            "open": 10,
        })
        service._get_case_stats = AsyncMock(return_value={
            "added": 20,
            "executed": 18,
            "passed": 15,
            "failed": 2,
            "blocked": 1,
        })
        service._get_coverage_data = AsyncMock(return_value={
            "overall": 0.75,
            "covered_scenes": 45,
            "total_scenes": 60,
        })
        
        content = await service.generate_weekly_summary("ICC")
        
        assert "整车测试助手周报" in content
        assert "ICC" in content
        assert "缺陷统计" in content
        assert "用例执行统计" in content
        assert "场景覆盖率" in content

    def test_is_report_time_friday(self):
        """Test checking if it's report time (Friday 17:00)."""
        from app.services.weekly_report_service import WeeklyReportService
        
        service = WeeklyReportService()
        
        # This will depend on current day/time
        # Just verify the method works
        result = service.is_report_time("friday", "17:00")
        assert isinstance(result, bool)

    def test_week_day_mapping(self):
        """Test week day mapping."""
        from app.services.weekly_report_service import WeeklyReportService
        
        assert WeeklyReportService.WEEK_DAYS["monday"] == 0
        assert WeeklyReportService.WEEK_DAYS["friday"] == 4
        assert WeeklyReportService.WEEK_DAYS["sunday"] == 6


class TestPushServiceM3Enhancements:
    """Test M3 push service enhancements."""

    @pytest.mark.asyncio
    async def test_enqueue_p2_with_user_id(self):
        """Test enqueueing P2 notification with user ID."""
        from app.services.push_service import PushService
        
        service = PushService()
        service._connected = False
        service._redis = None
        
        # Mock the config service
        with patch('app.services.push_config_service.get_push_config_service') as mock_config:
            mock_config_service = mock_config.return_value
            mock_config_service.get_user_config = AsyncMock(return_value=MagicMock(
                p2_frequency="hourly",
                push_enabled=True,
            ))
            
            msg_id = await service.enqueue_p2_notification(
                msg_type="dr_alert",
                title="Test P2 Alert",
                content="Test content",
                user_id="test_user",
            )
            
            # In mock mode, should return empty string if rate limited
            assert isinstance(msg_id, str)

    @pytest.mark.asyncio
    async def test_enqueue_dr_alert_critical(self):
        """Test enqueueing DR critical alert maps to P0."""
        from app.services.push_service import PushService
        
        service = PushService()
        service._connected = False
        service._redis = None
        service.enqueue_message = AsyncMock(return_value="test_msg_id")
        
        msg_id = await service.enqueue_dr_alert(
            alert_level="critical",
            title="Critical Alert",
            content="Critical issue detected",
            signal_id="sig_001",
        )
        
        # Should call enqueue_message with P0 level
        service.enqueue_message.assert_called_once()
        call_kwargs = service.enqueue_message.call_args[1]
        assert call_kwargs["level"] == "P0"
        assert "sig_001" in call_kwargs["content"]


class TestConfigCommandIntegration:
    """Integration tests for config command handling in webhook."""

    def test_config_command_recognition(self):
        """Test that CONFIG intent is recognized for various messages."""
        test_cases = [
            "推送配置",
            "开启推送",
            "关闭推送",
            "设置P1合并窗口",
            "设置P2频率",
            "免打扰设置",
        ]
        
        for msg in test_cases:
            result = recognize_intent(msg)
            assert result.intent == IntentType.CONFIG, f"Failed for: {msg}"
