"""
M4 Natural Language Query and Report Generation Tests.

Tests for:
- 20 intent templates matching
- Conversation context management
- NL Query service
- Report generation (weekly + special reports)
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.nl_query_service import NLQueryService, INTENT_TEMPLATES
from app.services.conversation_service import ConversationService, ConversationContext


class TestIntentTemplates:
    """Test 20 intent templates coverage."""
    
    def test_template_count(self):
        """Verify we have 20 intent templates."""
        assert len(INTENT_TEMPLATES) == 20, f"Expected 20 templates, got {len(INTENT_TEMPLATES)}"
    
    def test_query_bugs_templates(self):
        """Test bug query templates (4)."""
        bug_templates = [k for k in INTENT_TEMPLATES.keys() if k.startswith("query_bugs")]
        assert len(bug_templates) == 4  # open, by_status, by_priority, by_project
    
    def test_query_testcases_templates(self):
        """Test test case query templates (4)."""
        case_templates = [k for k in INTENT_TEMPLATES.keys() if k.startswith("query_testcases")]
        assert len(case_templates) == 4
    
    def test_query_progress_templates(self):
        """Test progress query templates (3)."""
        progress_templates = [k for k in INTENT_TEMPLATES.keys() if k.startswith("query_progress") or k.startswith("query_schedule") or k.startswith("query_weekly")]
        assert len(progress_templates) >= 2
    
    def test_query_mileage_templates(self):
        """Test mileage query templates (2)."""
        mileage_templates = [k for k in INTENT_TEMPLATES.keys() if k.startswith("query_mileage") or k.startswith("query_coverage")]
        assert len(mileage_templates) == 2
    
    def test_query_project_templates(self):
        """Test project query templates (2)."""
        project_templates = [k for k in INTENT_TEMPLATES.keys() if k.startswith("query_project")]
        assert len(project_templates) == 2
    
    def test_action_templates(self):
        """Test action templates (2)."""
        action_templates = [k for k in INTENT_TEMPLATES.keys() if k.startswith("create_") or k.startswith("update_")]
        assert len(action_templates) == 2
    
    def test_report_templates(self):
        """Test report templates (2)."""
        report_templates = [k for k in INTENT_TEMPLATES.keys() if k.startswith("generate_")]
        assert len(report_templates) == 2


class TestNLQueryService:
    """Test Natural Language Query Service."""
    
    @pytest.fixture
    def nl_service(self):
        return NLQueryService()
    
    @pytest.mark.asyncio
    async def test_process_query_query_bugs(self, nl_service):
        """Test processing bug query."""
        result = await nl_service.process_query(
            user_id="test_user",
            message="查ICC缺陷",
        )
        
        assert result["intent"] == "query_bugs_by_project"
        assert "params" in result
        assert result["params"]["project_key"] == "ICC"
    
    @pytest.mark.asyncio
    async def test_process_query_query_testcases(self, nl_service):
        """Test processing test case query - tests intent matching only."""
        # Test intent recognition directly without API calls
        matched = nl_service._match_intent_template("查功能测试用例")
        assert matched is not None
        template_name, params = matched
        assert template_name == "query_testcases_by_type"
        assert params["case_type"] == "功能测试"
    
    @pytest.mark.asyncio
    async def test_process_query_progress(self, nl_service):
        """Test processing progress query - tests intent matching only."""
        # Test intent recognition directly without API calls
        matched = nl_service._match_intent_template("查项目进度")
        assert matched is not None
        template_name, params = matched
        assert template_name == "query_progress"
    
    @pytest.mark.asyncio
    async def test_process_query_mileage(self, nl_service):
        """Test processing mileage query."""
        result = await nl_service.process_query(
            user_id="test_user",
            message="查本周里程",
        )
        
        assert result["intent"] == "query_mileage"
        assert result["params"]["time_range"] == "week"
    
    @pytest.mark.asyncio
    async def test_process_query_coverage(self, nl_service):
        """Test processing coverage query."""
        result = await nl_service.process_query(
            user_id="test_user",
            message="查场景覆盖率",
        )
        
        assert result["intent"] == "query_coverage"
    
    @pytest.mark.asyncio
    async def test_process_query_unknown(self, nl_service):
        """Test processing unknown query."""
        result = await nl_service.process_query(
            user_id="test_user",
            message="啦啦啦什么乱七八糟",
        )
        
        assert result["intent"] == "unknown"
    
    def test_extract_bug_filters_with_status(self, nl_service):
        """Test bug filter extraction with status."""
        params = nl_service.extract_bug_filters("查新建缺陷")
        assert params["status"] == "open"
    
    def test_extract_bug_filters_with_priority(self, nl_service):
        """Test bug filter extraction with priority."""
        params = nl_service.extract_bug_filters("查P0缺陷")
        assert params["priority"] == "p0"
    
    def test_extract_case_filters_with_type(self, nl_service):
        """Test case filter extraction with type."""
        params = nl_service.extract_case_filters("查性能测试用例")
        assert params["case_type"] == "性能测试"
    
    def test_extract_report_params(self, nl_service):
        """Test report parameter extraction."""
        params = nl_service.extract_report_params("生成AEB专项报告")
        assert params["project_key"] == "AEB"
        assert params["report_subtype"] == "aeb"


class TestConversationService:
    """Test conversation state management."""
    
    @pytest.fixture
    def conv_service(self):
        return ConversationService()
    
    @pytest.mark.asyncio
    async def test_get_context_new_user(self, conv_service):
        """Test getting context for new user."""
        ctx = await conv_service.get_context("new_user_123")
        
        assert ctx.user_id == "new_user_123"
        assert ctx.messages == []
        assert ctx.turn_count == 0
    
    @pytest.mark.asyncio
    async def test_add_message(self, conv_service):
        """Test adding messages to conversation."""
        ctx = await conv_service.add_message(
            user_id="test_user",
            role="user",
            content="查ICC缺陷",
            intent="query_bugs",
        )
        
        assert len(ctx.messages) == 1
        assert ctx.messages[0].role == "user"
        assert ctx.messages[0].content == "查ICC缺陷"
        assert ctx.turn_count == 1
    
    @pytest.mark.asyncio
    async def test_context_project_update(self, conv_service):
        """Test updating project context."""
        await conv_service.update_project_context("test_user", "AEB")
        ctx = await conv_service.get_context("test_user")
        
        assert ctx.project_key == "AEB"
    
    @pytest.mark.asyncio
    async def test_recent_messages(self, conv_service):
        """Test getting recent messages."""
        await conv_service.add_message("test_user", "user", "消息1")
        await conv_service.add_message("test_user", "user", "消息2")
        await conv_service.add_message("test_user", "assistant", "回复1")
        
        recent = await conv_service.get_recent_messages("test_user", count=2)
        
        assert len(recent) == 2
        assert recent[0].content == "消息2"
        assert recent[1].content == "回复1"
    
    @pytest.mark.asyncio
    async def test_format_conversation_history(self, conv_service):
        """Test formatting conversation history."""
        await conv_service.add_message("test_user", "user", "查缺陷")
        await conv_service.add_message("test_user", "assistant", "缺陷列表如下")
        
        ctx = await conv_service.get_context("test_user")
        formatted = conv_service.format_conversation_history(ctx)
        
        assert "最近对话" in formatted
        assert "查缺陷" in formatted


class TestConversationContext:
    """Test ConversationContext dataclass."""
    
    def test_to_dict_and_back(self):
        """Test serialization/deserialization."""
        ctx = ConversationContext(
            user_id="test_user",
            conversation_id="test_user_2026-04-02",
            project_key="ICC",
            turn_count=3,
        )
        ctx.add_message("user", "查ICC缺陷", "query_bugs")
        ctx.add_message("assistant", "这是缺陷列表", "query_bugs")
        
        # Serialize
        data = ctx.to_dict()
        
        # Deserialize
        ctx2 = ConversationContext.from_dict(data)
        
        assert ctx2.user_id == ctx.user_id
        assert ctx2.conversation_id == ctx.conversation_id
        assert ctx2.project_key == ctx.project_key
        assert len(ctx2.messages) == 2


class TestReportGeneration:
    """Test report generation service."""
    
    @pytest.mark.asyncio
    async def test_generate_weekly_report_message_format(self):
        """Test weekly report message format."""
        from app.services.report_generation_service import ReportGenerationService
        
        service = ReportGenerationService()
        
        # Mock the Feishu API calls
        with patch.object(service, '_create_feishu_doc', new_callable=AsyncMock) as mock_doc:
            mock_doc.return_value = {"doc_url": "https://feishu.cn/docx/test123"}
            
            result = await service.generate_weekly_report(
                project_key="ICC",
                time_range="last_week",
                user_id="test_user",
            )
        
        assert "message" in result
        assert "doc_url" in result
        assert "stats" in result
        assert "ICC" in result["message"]
    
    @pytest.mark.asyncio
    async def test_generate_special_report_icc(self):
        """Test ICC special report generation."""
        from app.services.report_generation_service import ReportGenerationService
        
        service = ReportGenerationService()
        
        with patch.object(service, '_create_feishu_doc', new_callable=AsyncMock) as mock_doc:
            mock_doc.return_value = {"doc_url": "https://feishu.cn/docx/test456"}
            
            result = await service.generate_special_report(
                project_key="ICC",
                report_subtype="icc",
                user_id="test_user",
            )
        
        assert "message" in result
        assert "ICC整车测试" in result["message"]
    
    @pytest.mark.asyncio
    async def test_generate_special_report_aeb(self):
        """Test AEB special report generation."""
        from app.services.report_generation_service import ReportGenerationService
        
        service = ReportGenerationService()
        
        with patch.object(service, '_create_feishu_doc', new_callable=AsyncMock) as mock_doc:
            mock_doc.return_value = {"doc_url": "https://feishu.cn/docx/test789"}
            
            result = await service.generate_special_report(
                project_key="AEB",
                report_subtype="aeb",
                user_id="test_user",
            )
        
        assert "message" in result
        assert "AEB自动紧急制动" in result["message"]
    
    def test_markdown_to_blocks(self):
        """Test markdown to Feishu blocks conversion."""
        from app.services.report_generation_service import ReportGenerationService
        
        service = ReportGenerationService()
        
        markdown = """# 标题
## 二级标题
正文内容
- 列表项1
- 列表项2
"""
        blocks = service._markdown_to_blocks(markdown)
        
        assert len(blocks) >= 3  # At least heading and paragraph blocks


class TestWeeklyReportService:
    """Test weekly report service integration."""
    
    @pytest.mark.asyncio
    async def test_weekly_summary_contains_required_data(self):
        """Test that weekly summary contains required data fields."""
        from app.services.weekly_report_service import WeeklyReportService
        from app.services import feishu_project_client, feishu_sheet_client
        
        service = WeeklyReportService()
        
        with patch.object(feishu_project_client, 'get_project_client') as mock_client, \
             patch.object(feishu_sheet_client, 'get_sheet_client') as mock_sheet:
            
            # Setup mocks
            mock_proj = MagicMock()
            mock_proj.query_bugs = AsyncMock(return_value=MagicMock(
                total=10,
                bugs=[],
            ))
            mock_client.return_value = mock_proj
            
            mock_sheet_inst = MagicMock()
            mock_sheet_inst.query_test_cases = AsyncMock(return_value=MagicMock(
                total=50,
                cases=[],
            ))
            mock_sheet.return_value = mock_sheet_inst
            
            content = await service.generate_weekly_summary("ICC")
        
        assert "缺陷统计" in content or "bug" in content.lower()
        assert "用例执行" in content or "用例" in content
        # Check for coverage section (covers mileage data indirectly)
        assert "覆盖率" in content or "coverage" in content.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
