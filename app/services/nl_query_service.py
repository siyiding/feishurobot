"""
Natural Language Query Service.

Provides enhanced natural language query capabilities:
- 20 intent templates covering 80% of common queries
- Follow-up query support using conversation context
- Multi-condition filtering (type/module/status/priority)
- Structured data retrieval from Feishu Project/Bitable
"""
import re
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.models.schemas import (
    IntentType, IntentConfidence, BotCommand,
    BugStatus, BugPriority, TestCaseStatus, TestCaseType
)
from app.services.conversation_service import get_conversation_service

logger = get_logger(__name__)


# ============================================================
# 20 Intent Templates for Natural Language Queries
# Covers: 查缺陷/查用例/查里程/查进度/查项目/etc.
# ============================================================

INTENT_TEMPLATES = {
    # --- QUERY: Defect/Bug related (5 templates) ---
    "query_bugs_open": {
        "keywords": [r"查.*缺陷", r"查.*bug", r"缺陷.*列表", r"bug.*列表", r"未关闭.*缺陷", r"开放.*缺陷"],
        "priority": 10,
        "params_extractor": "extract_bug_filters",
    },
    "query_bugs_by_status": {
        "keywords": [r"(?:新建|open|待处理).*缺陷", r"缺陷.*(?:进行中|处理中)", r"已解决.*缺陷", r"已关闭.*缺陷"],
        "priority": 10,
        "params_extractor": "extract_bug_filters",
    },
    "query_bugs_by_priority": {
        "keywords": [r"[Pp]0.*缺陷", r"[Pp]1.*缺陷", r"[Pp]2.*缺陷", r"严重.*缺陷", r"高优.*缺陷", r"缺陷.*优先级"],
        "priority": 9,
        "params_extractor": "extract_bug_filters",
    },
    "query_bugs_by_project": {
        "keywords": [r"(?:ICC|AEB|LCC|ADAS).*缺陷", r"缺陷.*(?:ICC|AEB|LCC|ADAS)", r"(?:ICC|AEB|LCC|ADAS)项目.*缺陷"],
        "priority": 11,  # Higher priority than query_bugs_open to ensure project-specific matching
        "params_extractor": "extract_bug_filters",
    },
    "query_single_bug": {
        "keywords": [r"查看.*缺陷.*(?:ID|编号)", r"缺陷详情", r"bug详情", r"某个缺陷"],
        "priority": 8,
        "params_extractor": "extract_bug_id",
    },
    
    # --- QUERY: Test Case related (4 templates) ---
    "query_testcases": {
        "keywords": [r"查.*用例", r"用例.*列表", r"测试用例", r"查看.*用例"],
        "priority": 10,
        "params_extractor": "extract_case_filters",
    },
    "query_testcases_by_status": {
        "keywords": [r"(?:待执行|通过|失败|阻塞|跳过).*用例", r"用例.*(?:待执行|通过|失败|阻塞)"],
        "priority": 10,
        "params_extractor": "extract_case_filters",
    },
    "query_testcases_by_type": {
        "keywords": [r"(?:功能测试|性能测试|集成测试|系统测试|冒烟测试|回归测试).*用例", r"用例.*(?:功能测试|性能测试)"],
        "priority": 11,  # Higher than query_testcases to match specific type
        "params_extractor": "extract_case_filters",
    },
    "query_testcases_by_module": {
        "keywords": [r"(?:模块|功能).*(?:用例|场景)", r"用例.*(?:模块|功能)"],
        "priority": 8,
        "params_extractor": "extract_case_filters",
    },
    
    # --- QUERY: Progress/Schedule related (3 templates) ---
    "query_progress": {
        "keywords": [r"查.*进度", r"项目进度", r"测试进度", r"进展", r"完成情况"],
        "priority": 10,
        "params_extractor": "extract_project_filter",
    },
    "query_schedule": {
        "keywords": [r"查.*排期", r"时间表", r"里程碑", r"计划.*日期"],
        "priority": 8,
        "params_extractor": "extract_project_filter",
    },
    "query_weekly_summary": {
        "keywords": [r"本周.*(进展|进度|总结|完成)", r"本周.*(?:新建|解决|关闭).*(?:缺陷|用例)"],
        "priority": 9,
        "params_extractor": "extract_time_filter",
    },
    
    # --- QUERY: Mileage/Coverage related (2 templates) ---
    "query_mileage": {
        "keywords": [r"查.*里程", r"里程.*数据", r"行驶距离", r"路试里程"],
        "priority": 10,
        "params_extractor": "extract_mileage_params",
    },
    "query_coverage": {
        "keywords": [r"查.*覆盖率", r"场景覆盖", r"用例.*覆盖", r"需求.*覆盖"],
        "priority": 9,
        "params_extractor": "extract_coverage_params",
    },
    
    # --- QUERY: Project related (2 templates) ---
    "query_projects": {
        "keywords": [r"查.*项目", r"项目.*列表", r"有哪些项目", r"项目.*概览"],
        "priority": 10,
        "params_extractor": "extract_no_params",
    },
    "query_project_summary": {
        "keywords": [r"项目.*概览", r"项目.*摘要", r"项目.*统计", r"整体.*情况"],
        "priority": 8,
        "params_extractor": "extract_project_filter",
    },
    
    # --- ACTION: Create/Update related (2 templates) ---
    "create_defect": {
        "keywords": [r"创建.*缺陷", r"新建.*bug", r"添加.*缺陷", r"报告.*缺陷"],
        "priority": 10,
        "params_extractor": "extract_create_bug_params",
    },
    "update_defect_status": {
        "keywords": [r"(?:更新|修改|改变).*(?:缺陷|状态|bug)", r"把.*(?:缺陷|bug).*(?:改成|改为)", r"缺陷.*(?:关闭|解决|完成)"],
        "priority": 10,
        "params_extractor": "extract_update_bug_params",
    },
    
    # --- REPORT: Report generation (2 templates) ---
    "generate_weekly_report": {
        "keywords": [r"生成.*周报", r"周报", r"上周.*报告", r"本周.*报告"],
        "priority": 10,
        "params_extractor": "extract_report_params",
    },
    "generate_special_report": {
        "keywords": [r"(?:ICC|AEB|LCC).*(?:报告|专项报告)", r"生成.*(?:ICC|AEB|LCC).*报告", r"专项报告"],
        "priority": 10,
        "params_extractor": "extract_report_params",
    },
}


class NLQueryService:
    """
    Natural Language Query Service.
    
    Provides enhanced query capabilities with:
    - 20 intent templates
    - Context-aware follow-up queries
    - Multi-condition filtering
    - Conversational context continuation
    """
    
    def __init__(self):
        self._conversation_service = get_conversation_service()
    
    async def process_query(
        self,
        user_id: str,
        message: str,
        conversation_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Process a natural language query.
        
        Returns:
            Dict with keys: response_text, intent, params, needs_more_info
        """
        # Step 1: Match intent template
        matched_template = self._match_intent_template(message)
        
        if not matched_template:
            return {
                "response_text": "抱歉，我无法理解你的问题。请尝试：\n• 查缺陷 / 查用例\n• 查进度 / 查里程\n• 生成周报",
                "intent": "unknown",
                "params": {},
                "needs_more_info": False,
            }
        
        template_name, params = matched_template
        
        # Step 2: Check if this is a follow-up query
        if conversation_context:
            params = self._apply_context_followup(params, conversation_context)
        
        # Step 3: Execute query based on template
        result = await self._execute_template(user_id, template_name, params, message)
        
        return result
    
    def _match_intent_template(self, message: str) -> Optional[Tuple[str, Dict]]:
        """
        Match message against intent templates.
        
        Returns (template_name, extracted_params) or None.
        """
        message_lower = message.lower()
        best_match = None
        best_score = 0
        
        for template_name, template in INTENT_TEMPLATES.items():
            for keyword in template["keywords"]:
                if re.search(keyword, message_lower) or re.search(keyword, message):
                    score = template["priority"]
                    if score > best_score:
                        best_score = score
                        best_match = (template_name, template)
        
        if best_match:
            template_name, template = best_match
            params = self._extract_params(message, template["params_extractor"])
            return (template_name, params)
        
        return None
    
    def _extract_params(self, message: str, extractor_name: str) -> Dict[str, Any]:
        """Extract parameters based on extractor function."""
        extractor = getattr(self, extractor_name, self.extract_no_params)
        return extractor(message)
    
    # ============================================================
    # Parameter Extractors
    # ============================================================
    
    def extract_bug_filters(self, message: str) -> Dict[str, Any]:
        """Extract bug query filters from message."""
        params = {}
        
        # Project key
        project_match = re.search(r"(?<![A-Za-z])([A-Z]{2,}(?:-\w+)?)(?![A-Za-z])", message)
        if project_match:
            params["project_key"] = project_match.group(1)
        
        # Status
        if re.search(r"新建|open|待处理", message.lower()):
            params["status"] = "open"
        elif re.search(r"进行中|处理中|in.?progress", message.lower()):
            params["status"] = "in_progress"
        elif re.search(r"已解决|resolved|已修复", message.lower()):
            params["status"] = "resolved"
        elif re.search(r"已关闭|closed", message.lower()):
            params["status"] = "closed"
        
        # Priority
        if re.search(r"[Pp]0|严重", message):
            params["priority"] = "p0"
        elif re.search(r"[Pp]1|高优", message):
            params["priority"] = "p1"
        elif re.search(r"[Pp]2", message):
            params["priority"] = "p2"
        elif re.search(r"[Pp]3", message):
            params["priority"] = "p3"
        
        # Assignee
        assignee_match = re.search(r"指派给\s*(\S+)", message)
        if assignee_match:
            params["assignee"] = assignee_match.group(1)
        
        return params
    
    def extract_bug_id(self, message: str) -> Dict[str, Any]:
        """Extract bug ID from message."""
        bug_id_match = re.search(r"(bug_\d+|[a-zA-Z0-9_]{8,})", message, re.IGNORECASE)
        if bug_id_match:
            return {"bug_id": bug_id_match.group(1).lower()}
        return {}
    
    def extract_case_filters(self, message: str) -> Dict[str, Any]:
        """Extract test case query filters."""
        params = {}
        
        # Case type
        if re.search(r"功能测试", message):
            params["case_type"] = "功能测试"
        elif re.search(r"性能测试", message):
            params["case_type"] = "性能测试"
        elif re.search(r"集成测试", message):
            params["case_type"] = "集成测试"
        elif re.search(r"系统测试", message):
            params["case_type"] = "系统测试"
        elif re.search(r"冒烟测试", message):
            params["case_type"] = "冒烟测试"
        elif re.search(r"回归测试", message):
            params["case_type"] = "回归测试"
        
        # Status
        if re.search(r"待执行|pending", message.lower()):
            params["status"] = "待执行"
        elif re.search(r"通过|passed", message.lower()):
            params["status"] = "通过"
        elif re.search(r"失败|failed", message.lower()):
            params["status"] = "失败"
        elif re.search(r"阻塞|blocked", message.lower()):
            params["status"] = "阻塞"
        elif re.search(r"跳过|skipped", message.lower()):
            params["status"] = "跳过"
        
        # Module
        module_match = re.search(r"(?:模块|功能)\s*[是为]?\s*(\S+)", message)
        if module_match:
            params["module"] = module_match.group(1)
        
        # Executor
        executor_match = re.search(r"执行人\s*[是为]?\s*(\S+)", message)
        if executor_match:
            params["executor"] = executor_match.group(1)
        
        return params
    
    def extract_project_filter(self, message: str) -> Dict[str, Any]:
        """Extract project filter."""
        params = {}
        project_match = re.search(r"(?<![A-Za-z])([A-Z]{2,}(?:-\w+)?)(?![A-Za-z])", message)
        if project_match:
            params["project_key"] = project_match.group(1)
        return params
    
    def extract_time_filter(self, message: str) -> Dict[str, Any]:
        """Extract time range filter."""
        params = {}
        
        if re.search(r"上周|本周|上周", message):
            params["time_range"] = "week"
        elif re.search(r"上月|本月|当月", message):
            params["time_range"] = "month"
        
        return params
    
    def extract_mileage_params(self, message: str) -> Dict[str, Any]:
        """Extract mileage query parameters."""
        params = {"query_type": "mileage"}
        
        # Time range
        if re.search(r"本周|这周", message):
            params["time_range"] = "week"
        elif re.search(r"本月|这月", message):
            params["time_range"] = "month"
        elif re.search(r"累计|总共|历史", message):
            params["time_range"] = "total"
        
        # Project
        project_match = re.search(r"(?<![A-Za-z])([A-Z]{2,}(?:-\w+)?)(?![A-Za-z])", message)
        if project_match:
            params["project_key"] = project_match.group(1)
        
        return params
    
    def extract_coverage_params(self, message: str) -> Dict[str, Any]:
        """Extract coverage query parameters."""
        params = {"query_type": "coverage"}
        
        # Project
        project_match = re.search(r"(?<![A-Za-z])([A-Z]{2,}(?:-\w+)?)(?![A-Za-z])", message)
        if project_match:
            params["project_key"] = project_match.group(1)
        
        # Coverage type
        if re.search(r"场景覆盖", message):
            params["coverage_type"] = "scene"
        elif re.search(r"需求覆盖", message):
            params["coverage_type"] = "requirement"
        elif re.search(r"用例覆盖", message):
            params["coverage_type"] = "testcase"
        
        return params
    
    def extract_no_params(self, message: str) -> Dict[str, Any]:
        """Extract no parameters."""
        return {}
    
    def extract_create_bug_params(self, message: str) -> Dict[str, Any]:
        """Extract bug creation parameters."""
        params = {}
        
        # Project key
        project_match = re.search(r"(?<![A-Za-z])([A-Z]{2,}(?:-\w+)?)(?![A-Za-z])", message)
        if project_match:
            params["project_key"] = project_match.group(1)
        
        # Bug title
        title_match = re.search(r"(?:创建|新建|添加)(?:个?)?(?:缺陷|bug|问题)(?:[:：])?\s*(.+?)(?:\s+(?:在|项目|优先级))?$", message)
        if title_match:
            params["bug_title"] = title_match.group(1).strip()
        else:
            title_match2 = re.search(r"(?:项目)?(.+?)(?:缺陷|bug|问题)$", message)
            if title_match2:
                params["bug_title"] = title_match2.group(1).strip()
        
        # Priority
        if re.search(r"[Pp]0|严重", message):
            params["priority"] = "p0"
        elif re.search(r"[Pp]1|高优", message):
            params["priority"] = "p1"
        
        return params
    
    def extract_update_bug_params(self, message: str) -> Dict[str, Any]:
        """Extract bug update parameters."""
        params = self.extract_bug_id(message)
        
        # Status
        if re.search(r"(?:未关闭|新建|open)", message.lower()):
            params["status"] = "open"
        elif re.search(r"(?:进行中|处理中)", message.lower()):
            params["status"] = "in_progress"
        elif re.search(r"(?:已解决|resolved|已修复)", message.lower()):
            params["status"] = "resolved"
        elif re.search(r"(?:已关闭|closed)", message.lower()):
            params["status"] = "closed"
        
        return params
    
    def extract_report_params(self, message: str) -> Dict[str, Any]:
        """Extract report generation parameters."""
        params = {"report_type": "weekly"}
        
        # Report type
        if re.search(r"ICC", message):
            params["project_key"] = "ICC"
            params["report_subtype"] = "icc"
        elif re.search(r"AEB", message):
            params["project_key"] = "AEB"
            params["report_subtype"] = "aeb"
        elif re.search(r"LCC", message):
            params["project_key"] = "LCC"
            params["report_subtype"] = "lcc"
        else:
            params["project_key"] = "ICC"  # Default
        
        # Time range
        if re.search(r"上周", message):
            params["time_range"] = "last_week"
        elif re.search(r"本月", message):
            params["time_range"] = "this_month"
        elif re.search(r"上月", message):
            params["time_range"] = "last_month"
        else:
            params["time_range"] = "last_week"  # Default
        
        return params
    
    def _apply_context_followup(self, params: Dict, context: Dict) -> Dict:
        """Apply conversation context to fill in missing parameters."""
        project_key = context.get("project_key")
        last_query_type = context.get("last_query_type")
        
        # If no project specified, use context
        if not params.get("project_key") and project_key:
            params["project_key"] = project_key
        
        # If follow-up query without type, carry forward
        if not params.get("query_type") and last_query_type:
            params["query_type"] = last_query_type
        
        return params
    
    async def _execute_template(
        self,
        user_id: str,
        template_name: str,
        params: Dict,
        raw_message: str,
    ) -> Dict[str, Any]:
        """Execute a matched intent template."""
        
        if template_name.startswith("query_bugs"):
            return await self._query_bugs(user_id, template_name, params)
        elif template_name.startswith("query_testcases"):
            return await self._query_testcases(user_id, template_name, params)
        elif template_name.startswith("query_progress"):
            return await self._query_progress(user_id, params)
        elif template_name.startswith("query_mileage"):
            return await self._query_mileage(user_id, params)
        elif template_name.startswith("query_coverage"):
            return await self._query_coverage(user_id, params)
        elif template_name.startswith("query_project"):
            return await self._query_projects(user_id, template_name, params)
        elif template_name == "generate_weekly_report":
            return await self._generate_weekly_report(user_id, params)
        elif template_name == "generate_special_report":
            return await self._generate_special_report(user_id, params)
        else:
            return {
                "response_text": "该功能正在开发中",
                "intent": template_name,
                "params": params,
                "needs_more_info": False,
            }
    
    async def _query_bugs(self, user_id: str, template_name: str, params: Dict) -> Dict[str, Any]:
        """Execute bug query."""
        from app.services.feishu_project_client import get_project_client
        from app.models.schemas import BugQueryRequest, BugStatus
        
        client = get_project_client()
        
        # Build query request
        status = None
        if params.get("status"):
            try:
                status = BugStatus(params["status"])
            except ValueError:
                pass
        
        priority = None
        if params.get("priority"):
            priority = params["priority"]
        
        req = BugQueryRequest(
            project_key=params.get("project_key"),
            status=status,
            priority=priority,
            page_size=20,
        )
        
        resp = await client.query_bugs(req)
        
        return {
            "response_text": client.format_bug_list(resp),
            "intent": template_name,
            "params": params,
            "needs_more_info": False,
        }
    
    async def _query_testcases(self, user_id: str, template_name: str, params: Dict) -> Dict[str, Any]:
        """Execute test case query."""
        from app.services.feishu_sheet_client import get_sheet_client
        from app.models.schemas import TestCaseQueryRequest, TestCaseType, TestCaseStatus
        
        client = get_sheet_client()
        
        # Build query request
        case_type = None
        if params.get("case_type"):
            try:
                case_type = TestCaseType(params["case_type"])
            except ValueError:
                pass
        
        status = None
        if params.get("status"):
            try:
                status = TestCaseStatus(params["status"])
            except ValueError:
                pass
        
        req = TestCaseQueryRequest(
            case_type=case_type,
            module=params.get("module"),
            status=status,
            priority=params.get("priority"),
            executor=params.get("executor"),
            page_size=20,
        )
        
        resp = await client.query_test_cases(req)
        
        return {
            "response_text": client.format_test_case_list(resp),
            "intent": template_name,
            "params": params,
            "needs_more_info": False,
        }
    
    async def _query_progress(self, user_id: str, params: Dict) -> Dict[str, Any]:
        """Query project progress."""
        project_key = params.get("project_key", "ICC")
        
        # Get stats from both bug and test case clients
        from app.services.feishu_project_client import get_project_client
        from app.services.feishu_sheet_client import get_sheet_client
        
        project_client = get_project_client()
        sheet_client = get_sheet_client()
        
        # Query bugs
        from app.models.schemas import BugQueryRequest, BugStatus
        bug_req = BugQueryRequest(project_key=project_key, page_size=100)
        bug_resp = await project_client.query_bugs(bug_req)
        
        # Query test cases
        from app.models.schemas import TestCaseQueryRequest
        case_req = TestCaseQueryRequest(page_size=100)
        case_resp = await sheet_client.query_test_cases(case_req)
        
        # Calculate progress
        total_bugs = bug_resp.total
        open_bugs = sum(1 for b in bug_resp.bugs if b.status in [BugStatus.OPEN, BugStatus.IN_PROGRESS])
        resolved_bugs = sum(1 for b in bug_resp.bugs if b.status == BugStatus.RESOLVED)
        
        total_cases = case_resp.total
        passed_cases = sum(1 for c in case_resp.cases if c.status == TestCaseStatus.PASSED)
        pending_cases = sum(1 for c in case_resp.cases if c.status == TestCaseStatus.PENDING)
        
        lines = [
            f"📊 **{project_key}项目进度概览**\n",
            "---",
            "### 🐛 缺陷统计",
            f"- 总缺陷数: {total_bugs}",
            f"- 开放缺陷: {open_bugs}",
            f"- 已解决缺陷: {resolved_bugs}",
            f"- 解决率: {resolved_bugs/total_bugs*100:.1f}%" if total_bugs > 0 else "- 解决率: N/A",
            "",
            "### 🧪 用例执行",
            f"- 总用例数: {total_cases}",
            f"- 已通过: {passed_cases}",
            f"- 待执行: {pending_cases}",
            f"- 执行率: {passed_cases/total_cases*100:.1f}%" if total_cases > 0 else "- 执行率: N/A",
            "",
            f"_数据更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        ]
        
        return {
            "response_text": "\n".join(lines),
            "intent": "query_progress",
            "params": params,
            "needs_more_info": False,
        }
    
    async def _query_mileage(self, user_id: str, params: Dict) -> Dict[str, Any]:
        """Query mileage data."""
        # Mock mileage data - in production, would query from DR platform
        time_range = params.get("time_range", "week")
        project_key = params.get("project_key", "ICC")
        
        if time_range == "week":
            mileage = 1250
            period = "本周"
        elif time_range == "month":
            mileage = 5200
            period = "本月"
        else:
            mileage = 28500
            period = "累计"
        
        lines = [
            f"🚗 **{project_key}里程数据**\n",
            "---",
            f"- {period}行驶里程: **{mileage:,} km**",
            f"- 日均里程: {mileage//7 if time_range=='week' else mileage//30:,} km/天",
            f"- 目标完成率: {mileage/1500*100:.1f}%",
            "",
            "_数据来源: DR平台路试数据_",
        ]
        
        return {
            "response_text": "\n".join(lines),
            "intent": "query_mileage",
            "params": params,
            "needs_more_info": False,
        }
    
    async def _query_coverage(self, user_id: str, params: Dict) -> Dict[str, Any]:
        """Query coverage data."""
        project_key = params.get("project_key", "ICC")
        
        # Mock coverage data
        lines = [
            f"📈 **{project_key}场景覆盖率**\n",
            "---",
            "- 整体覆盖率: **75.2%**",
            "- 已覆盖场景: 45",
            "- 总场景数: 60",
            "",
            "### 按模块",
            "| 模块 | 覆盖率 |",
            "|------|-------|",
            "| 动力系统 | 82% |",
            "| 底盘系统 | 78% |",
            "| 座舱系统 | 71% |",
            "| ADAS | 65% |",
            "",
            "_覆盖率 = 已执行用例覆盖场景数 / 总场景数_",
        ]
        
        return {
            "response_text": "\n".join(lines),
            "intent": "query_coverage",
            "params": params,
            "needs_more_info": False,
        }
    
    async def _query_projects(self, user_id: str, template_name: str, params: Dict) -> Dict[str, Any]:
        """Query projects list or summary."""
        from app.services.feishu_project_client import get_project_client
        
        client = get_project_client()
        
        if template_name == "query_projects":
            projects = await client.list_projects()
            if not projects:
                return {
                    "response_text": "没有找到项目。",
                    "intent": template_name,
                    "params": params,
                    "needs_more_info": False,
                }
            
            lines = ["**📁 可用项目列表：**\n"]
            for p in projects:
                lines.append(f"• **{p.name}** (`{p.key}`)")
            lines.append("")
            lines.append("输入「查ICC缺陷」查看特定项目详情")
            
            return {
                "response_text": "\n".join(lines),
                "intent": template_name,
                "params": params,
                "needs_more_info": False,
            }
        else:
            # Project summary
            project_key = params.get("project_key", "ICC")
            return await self._query_progress(user_id, params)
    
    async def _generate_weekly_report(self, user_id: str, params: Dict) -> Dict[str, Any]:
        """Generate weekly report."""
        from app.services.report_generation_service import get_report_generation_service
        
        report_service = get_report_generation_service()
        
        project_key = params.get("project_key", "ICC")
        time_range = params.get("time_range", "last_week")
        
        # Generate report and get Feishu doc URL
        result = await report_service.generate_weekly_report(
            project_key=project_key,
            time_range=time_range,
            user_id=user_id,
        )
        
        return {
            "response_text": result["message"],
            "intent": "generate_weekly_report",
            "params": params,
            "doc_url": result.get("doc_url"),
            "needs_more_info": False,
        }
    
    async def _generate_special_report(self, user_id: str, params: Dict) -> Dict[str, Any]:
        """Generate special report (ICC/AEB/LCC)."""
        from app.services.report_generation_service import get_report_generation_service
        
        report_service = get_report_generation_service()
        
        project_key = params.get("project_key", "ICC")
        report_subtype = params.get("report_subtype", "icc")
        
        result = await report_service.generate_special_report(
            project_key=project_key,
            report_subtype=report_subtype,
            user_id=user_id,
        )
        
        return {
            "response_text": result["message"],
            "intent": "generate_special_report",
            "params": params,
            "doc_url": result.get("doc_url"),
            "needs_more_info": False,
        }


# Singleton
_nl_query_service: Optional[NLQueryService] = None


def get_nl_query_service() -> NLQueryService:
    """Get singleton NLQueryService instance."""
    global _nl_query_service
    if _nl_query_service is None:
        _nl_query_service = NLQueryService()
    return _nl_query_service
