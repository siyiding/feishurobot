"""
M6: Change Awareness & Regression Assistant Service.

Provides:
- Change-Case matching based on Git MR changes
- Regression test suggestion generation
- Push to test engineers
- OTA change awareness
- Test case failure → DR association (framework)

8.2 变更-用例匹配 (3 days)
8.3 回归建议生成 (2 days)
8.4 OTA变更感知 (2 days)
8.5 用例失败→DR关联 (3 days) - Framework only, details pending DR CLI format
"""
import json
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    GitMergeRequest, GitChangedFile, GitMRStatus, GitProvider,
    TestCaseInfo, TestCaseStatus, TestCaseType,
    ChangeCaseMatch, RegressionSuggestion, ModuleMapping,
    OTAChangeInfo, OTAChangeMatch, BugPriority,
    TestCaseFailure, DRTripInfo, FailureDRAssociation,
    PushMessage
)
from app.services.git_service import get_git_service, GitService
from app.services.feishu_sheet_client import get_feishu_sheet_client, FeishuSheetClient
from app.services.push_service import get_push_service

logger = get_logger(__name__)


# ==================== 8.2 Change-Case Matching ====================

class ChangeCaseMatcher:
    """
    Match changed files from Git MRs to test cases.
    
    Uses module-to-testcase mapping from test case library.
    """
    
    # Module keywords for implicit mapping
    MODULE_KEYWORDS = {
        "ICC-AEB": ["aeb", "autonomous_emergency_braking", "紧急制动", "前向碰撞"],
        "ICC-LCC": ["lcc", "lane_centering", "车道居中", "车道保持"],
        "ICC-ACC": ["acc", "adaptive_cruise", "自适应巡航", "定速巡航"],
        "ICC-FCW": ["fcw", "forward_collision", "前向碰撞预警"],
        "ICC-LDW": ["ldw", "lane_departure", "车道偏离"],
        "ICC-APA": ["apa", "parking_assist", "泊车辅助", "自动泊车"],
        "ICC-SLIF": ["slif", "speed_limit", "限速", "交通标志"],
        "ADAS": ["adas", "advanced_driver", "高级辅助驾驶"],
        "COCKPIT": ["cockpit", "座舱", "hmi", "仪表"],
        "CAN": ["can", "canbus", "总线", "通信"],
        "PERCEPTION": ["perception", "感知", "sensor", "传感器", "radar", "lidar", "camera"],
        "PLANNING": ["planning", "规划", "path", "路径"],
        "CONTROL": ["control", "控制", "vehicle", "车辆"],
    }
    
    def __init__(self, sheet_client: Optional[FeishuSheetClient] = None):
        self._sheet_client = sheet_client
        self._git_service = get_git_service()
    
    @property
    def sheet_client(self) -> FeishuSheetClient:
        """Get sheet client lazily."""
        if self._sheet_client is None:
            self._sheet_client = get_feishu_sheet_client()
        return self._sheet_client
    
    @property
    def git_service(self) -> GitService:
        """Get git service."""
        return self._git_service
    
    def extract_modules_from_files(
        self,
        changed_files: List[GitChangedFile]
    ) -> List[Tuple[str, float]]:
        """
        Extract affected modules from changed files.
        
        Returns list of (module_name, confidence) tuples.
        """
        module_scores: Dict[str, float] = defaultdict(float)
        
        for file in changed_files:
            filename = file.filename.lower()
            
            for module, keywords in self.MODULE_KEYWORDS.items():
                for keyword in keywords:
                    if keyword.lower() in filename:
                        # Higher score for module-specific files
                        base_score = 0.7
                        if filename.startswith(f"src/{module.lower().split('-')[0].lower()}"):
                            base_score = 0.9
                        module_scores[module] = max(module_scores[module], base_score)
                        break
        
        return sorted(module_scores.items(), key=lambda x: -x[1])
    
    async def get_test_cases_by_modules(
        self,
        modules: List[str]
    ) -> Dict[str, List[TestCaseInfo]]:
        """
        Get test cases filtered by modules.
        
        Uses the 'module' field in test case library.
        """
        result: Dict[str, List[TestCaseInfo]] = {}
        
        for module in modules:
            try:
                # Query test cases by module
                cases = await self.sheet_client.query_cases_by_module(module)
                result[module] = cases
            except Exception as e:
                logger.warning(f"Failed to query cases for module {module}: {e}")
                result[module] = []
        
        return result
    
    async def match_mr_to_test_cases(
        self,
        mr: GitMergeRequest
    ) -> List[ChangeCaseMatch]:
        """
        Match changed files in a MR to test cases.
        
        Algorithm:
        1. Extract modules from changed file paths
        2. Get test cases for each module
        3. Build ChangeCaseMatch results
        """
        matches: List[ChangeCaseMatch] = []
        
        # Get module scores from file changes
        module_scores = self.extract_modules_from_files(mr.changed_files)
        
        # Also use git service module mappings
        git_matches = self._git_service.match_changed_files_to_modules(mr.changed_files)
        for gm in git_matches:
            module_scores.append((gm.matched_module, gm.match_confidence))
        
        # Deduplicate and get test cases
        seen_modules = set()
        for module, score in module_scores:
            if module in seen_modules:
                continue
            seen_modules.add(module)
            
            try:
                cases = await self.get_test_cases_by_modules([module])
                case_ids = [c.case_id for c in cases.get(module, [])]
                
                matches.append(ChangeCaseMatch(
                    changed_file=", ".join([f.filename for f in mr.changed_files[:3]]),
                    matched_module=module,
                    matched_cases=case_ids,
                    match_confidence=score,
                ))
            except Exception as e:
                logger.warning(f"Failed to get cases for module {module}: {e}")
        
        return matches
    
    async def get_regression_cases_for_mr(
        self,
        mr: GitMergeRequest,
        max_cases: int = 50
    ) -> List[TestCaseInfo]:
        """
        Get recommended regression test cases for a MR.
        
        Returns test cases that should be run based on changed files.
        """
        matches = await self.match_mr_to_test_cases(mr)
        
        all_cases: Dict[str, TestCaseInfo] = {}
        for match in matches:
            for module in [match.matched_module]:
                try:
                    cases = await self.get_test_cases_by_modules([module])
                    for case in cases.get(module, []):
                        if case.case_id not in all_cases:
                            all_cases[case.case_id] = case
                except Exception as e:
                    logger.warning(f"Failed to get cases for module {module}: {e}")
        
        # Sort by priority and limit
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        sorted_cases = sorted(
            all_cases.values(),
            key=lambda c: priority_order.get(c.priority or "P2", 2)
        )
        
        return sorted_cases[:max_cases]


# ==================== 8.3 Regression Suggestion Generation ====================

class RegressionSuggestionGenerator:
    """
    Generate regression test suggestions from Git MRs.
    
    Creates formatted reports and pushes to test engineers.
    """
    
    def __init__(self):
        self._sheet_client = get_feishu_sheet_client()
        self._push_service = get_push_service()
        self._matcher = ChangeCaseMatcher()
    
    async def generate_suggestion(
        self,
        mr: GitMergeRequest,
        affected_cases: List[TestCaseInfo],
        match_details: List[ChangeCaseMatch],
        reason: str = ""
    ) -> RegressionSuggestion:
        """
        Generate a regression suggestion for a MR.
        
        Args:
            mr: Git Merge Request
            affected_cases: Test cases affected by the MR
            match_details: Detailed matching information
            reason: Human-readable reason for the suggestion
        
        Returns:
            RegressionSuggestion object
        """
        # Determine priority based on MR characteristics
        priority = BugPriority.P2
        
        if "[P0]" in mr.title or "[P1]" in mr.title:
            priority = BugPriority.P1 if "[P1]" in mr.title else BugPriority.P0
        elif "hotfix" in mr.title.lower() or "critical" in mr.title.lower():
            priority = BugPriority.P0
        elif "bugfix" in mr.title.lower() or "fix" in mr.title.lower():
            priority = BugPriority.P1
        
        # Extract affected modules
        changed_modules = list(set(d.matched_module for d in match_details))
        
        return RegressionSuggestion(
            mr_id=mr.mr_id,
            mr_title=mr.title,
            mr_url=mr.web_url,
            changed_modules=changed_modules,
            affected_cases=affected_cases,
            match_details=match_details,
            priority=priority,
            reason=reason or f"MR modifies {len(mr.changed_files)} files in {len(changed_modules)} modules",
        )
    
    def format_suggestion_as_markdown(
        self,
        suggestion: RegressionSuggestion
    ) -> str:
        """
        Format regression suggestion as markdown for Feishu.
        """
        priority_emoji = {
            BugPriority.P0: "🔴 P0",
            BugPriority.P1: "🟠 P1",
            BugPriority.P2: "🟡 P2",
            BugPriority.P3: "🟢 P3",
        }
        
        lines = [
            f"## 📋 回归测试建议",
            "",
            f"**MR**: [{suggestion.mr_title}]({suggestion.mr_url})",
            f"**优先级**: {priority_emoji.get(suggestion.priority, '🟡 P2')}",
            f"**原因**: {suggestion.reason}",
            "",
            f"### 🔧 变更模块",
        ]
        
        for module in suggestion.changed_modules:
            lines.append(f"- {module}")
        
        lines.extend([
            "",
            f"### ✅ 建议执行的用例（共 {len(suggestion.affected_cases)} 个）",
            "",
            "| 用例ID | 用例名称 | 模块 | 优先级 | 状态 |",
            "|------|---------|------|--------|------|",
        ])
        
        for case in suggestion.affected_cases[:20]:  # Limit to 20 in table
            lines.append(
                f"| {case.case_id} | {case.case_name} | {case.module or '-'} | "
                f"{case.priority or '-'} | {case.status.value if case.status else '-'} |"
            )
        
        if len(suggestion.affected_cases) > 20:
            lines.append(f"",
                f"_...还有 {len(suggestion.affected_cases) - 20} 个用例_")
        
        if suggestion.match_details:
            lines.extend([
                "",
                "### 📁 变更-用例匹配详情",
            ])
            for detail in suggestion.match_details[:5]:
                lines.append(
                    f"- **{detail.matched_module}**: {detail.changed_file} "
                    f"(匹配 {len(detail.matched_cases)} 个用例, 置信度 {detail.match_confidence:.0%})"
                )
        
        lines.extend([
            "",
            "---",
            "_由整车测试助手自动生成_",
        ])
        
        return "\n".join(lines)
    
    async def push_suggestion_to_engineer(
        self,
        suggestion: RegressionSuggestion,
        engineer_open_id: str = ""
    ) -> bool:
        """
        Push regression suggestion to test engineer.
        
        Args:
            suggestion: The regression suggestion
            engineer_open_id: Feishu open_id of the engineer (optional)
        
        Returns:
            True if push was successful
        """
        try:
            content = self.format_suggestion_as_markdown(suggestion)
            
            push_msg = PushMessage(
                id=f"regression_{suggestion.mr_id}_{int(time.time())}",
                level=suggestion.priority.value.upper() if suggestion.priority else "P2",
                msg_type="regression_suggestion",
                title=f"📋 回归测试建议: {suggestion.mr_title[:30]}...",
                content=content,
                user_id=engineer_open_id or None,
                created_at=datetime.now().isoformat(),
            )
            
            # Use push service based on priority
            if suggestion.priority == BugPriority.P0:
                await self._push_service.enqueue_p0_alert(push_msg)
            else:
                await self._push_service.enqueue_p1_notification(push_msg)
            
            logger.info(f"Pushed regression suggestion for MR {suggestion.mr_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to push regression suggestion: {e}")
            return False


# ==================== 8.4 OTA Change Awareness ====================

class OTAChangeAnalyzer:
    """
    Analyze OTA (Over-The-Air) change logs and match to test cases.
    
    Parses OTA change descriptions and keywords to identify affected
    test cases.
    """
    
    # OTA change type keywords
    CHANGE_TYPE_KEYWORDS = {
        "feature": ["新增", "新功能", "添加", "功能升级", "new feature", "add"],
        "bugfix": ["修复", "bugfix", "fix", "问题修复", "缺陷修复", "热修复"],
        "improvement": ["优化", "改进", "enhance", "improve", "性能优化"],
        "security": ["安全", "security", "加密", "认证"],
    }
    
    # Module extraction keywords
    MODULE_KEYWORDS = {
        "ICC-AEB": ["aeb", "紧急制动", "前向碰撞", "autonomous emergency"],
        "ICC-LCC": ["lcc", "车道居中", "车道保持", "lane centering"],
        "ICC-ACC": ["acc", "自适应巡航", "定速巡航", "adaptive cruise"],
        "ICC-FCW": ["fcw", "前向碰撞预警", "forward collision warning"],
        "ICC-LDW": ["ldw", "车道偏离", "lane departure"],
        "ICC-APA": ["apa", "泊车辅助", "自动泊车", "parking assist"],
        "ADAS": ["adas", "高级辅助驾驶", "高级驾驶辅助"],
        "COCKPIT": ["座舱", "仪表", "hmi", "人机界面"],
        "PERCEPTION": ["感知", "传感器", "radar", "lidar", "camera", "视觉"],
        "PLANNING": ["规划", "path", "路径规划"],
        "CONTROL": ["控制", "车辆控制", "动力", "底盘"],
    }
    
    def __init__(self):
        self._sheet_client = get_feishu_sheet_client()
        self._matcher = ChangeCaseMatcher()
    
    def parse_change_type(self, title: str, description: str = "") -> Tuple[str, float]:
        """
        Parse OTA change type from title and description.
        
        Returns (change_type, confidence).
        """
        text = f"{title} {description}".lower()
        
        for change_type, keywords in self.CHANGE_TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword.lower() in text:
                    return change_type, 0.8
        
        return "improvement", 0.5  # Default
    
    def extract_keywords(self, title: str, description: str = "") -> List[str]:
        """
        Extract keywords from OTA change description.
        
        Extracts significant words, filtering out common words.
        """
        # Common words to filter
        stop_words = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "那",
            "the", "a", "an", "and", "or", "but", "in", "on", "at",
            "to", "for", "of", "is", "are", "was", "were", "be", "been",
            "this", "that", "these", "those", "it", "its",
        }
        
        text = f"{title} {description}".lower()
        # Split on non-alphanumeric characters
        words = re.findall(r'\b[a-zA-Z0-9\u4e00-\u9fff]+\b', text)
        
        # Filter short words and stop words
        keywords = [
            w for w in words
            if len(w) >= 2 and w not in stop_words
        ]
        
        # Also add specific keywords from change type
        change_type, _ = self.parse_change_type(title, description)
        keywords.append(change_type)
        
        return list(set(keywords))
    
    def extract_affected_modules(
        self,
        title: str,
        description: str = ""
    ) -> List[Tuple[str, float]]:
        """
        Extract affected modules from OTA change.
        
        Returns list of (module_name, confidence) tuples.
        """
        text = f"{title} {description}".lower()
        module_scores: Dict[str, float] = {}
        
        for module, keywords in self.MODULE_KEYWORDS.items():
            score = 0.0
            for keyword in keywords:
                if keyword.lower() in text:
                    score = max(score, 0.7)
                    # Extra weight for keyword appearing multiple times
                    count = text.count(keyword.lower())
                    if count >= 2:
                        score = max(score, 0.9)
            
            if score > 0:
                module_scores[module] = score
        
        return sorted(module_scores.items(), key=lambda x: -x[1])
    
    async def analyze_ota_change(
        self,
        ota_change: OTAChangeInfo
    ) -> OTAChangeMatch:
        """
        Analyze an OTA change and match to test cases.
        
        Args:
            ota_change: OTA change information
        
        Returns:
            OTAChangeMatch with matched test cases
        """
        # Extract affected modules
        modules = self.extract_affected_modules(
            ota_change.title,
            ota_change.description or ""
        )
        
        # Get test cases for each module
        all_cases: Dict[str, TestCaseInfo] = {}
        for module, score in modules:
            try:
                cases = await self._sheet_client.query_cases_by_module(module)
                for case in cases:
                    if case.case_id not in all_cases:
                        all_cases[case.case_id] = case
            except Exception as e:
                logger.warning(f"Failed to query cases for module {module}: {e}")
        
        # Filter cases based on change type
        filtered_cases = []
        change_type = ota_change.change_type
        
        for case in all_cases.values():
            # For bugfix, prioritize failed/blocked cases
            if change_type == "bugfix":
                if case.status in [TestCaseStatus.FAILED, TestCaseStatus.BLOCKED]:
                    filtered_cases.append(case)
                elif case.module and any(m in case.module for m, _ in modules[:2]):
                    filtered_cases.append(case)
            # For feature, prioritize related modules
            elif change_type == "feature":
                if case.module and any(m in case.module for m, _ in modules[:2]):
                    filtered_cases.append(case)
            else:
                filtered_cases.append(case)
        
        # Sort by priority
        priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        filtered_cases.sort(
            key=lambda c: priority_order.get(c.priority or "P2", 2)
        )
        
        reason = f"OTA变更 [{ota_change.change_type}] 影响模块: {', '.join([m for m, _ in modules[:3]])}"
        
        return OTAChangeMatch(
            ota_change=ota_change,
            matched_cases=filtered_cases,
            match_reason=reason,
        )
    
    def format_ota_match_as_markdown(
        self,
        match: OTAChangeMatch
    ) -> str:
        """
        Format OTA change match as markdown.
        """
        change_type_emoji = {
            "feature": "✨",
            "bugfix": "🐛",
            "improvement": "⚡️",
            "security": "🔒",
        }
        
        emoji = change_type_emoji.get(match.ota_change.change_type, "📦")
        
        lines = [
            f"## {emoji} OTA变更感知",
            "",
            f"**版本**: {match.ota_change.version}",
            f"**标题**: {match.ota_change.title}",
            f"**变更类型**: {match.ota_change.change_type}",
            f"**原因**: {match.match_reason}",
            "",
            f"### 📋 建议关注的用例（共 {len(match.matched_cases)} 个）",
            "",
            "| 用例ID | 用例名称 | 模块 | 优先级 | 当前状态 |",
            "|------|---------|------|--------|------|",
        ]
        
        for case in match.matched_cases[:15]:
            lines.append(
                f"| {case.case_id} | {case.case_name} | {case.module or '-'} | "
                f"{case.priority or '-'} | {case.status.value if case.status else '-'} |"
            )
        
        if len(match.matched_cases) > 15:
            lines.append(f"",
                f"_...还有 {len(match.matched_cases) - 15} 个用例_")
        
        lines.extend([
            "",
            "---",
            "_由整车测试助手自动生成_",
        ])
        
        return "\n".join(lines)


# ==================== 8.5 Test Case Failure → DR Association ====================

class TestCaseDRAssociator:
    """
    Associate test case failures with DR (Data Report) data.
    
    Framework implementation - details pending DR CLI format confirmation.
    """
    
    def __init__(self):
        self._failure_records: Dict[str, TestCaseFailure] = {}
    
    def record_failure(
        self,
        case_id: str,
        case_name: str,
        module: str,
        failure_time: str,
        failure_reason: Optional[str] = None,
        executor: Optional[str] = None
    ) -> TestCaseFailure:
        """
        Record a test case failure.
        
        Args:
            case_id: Test case ID
            case_name: Test case name
            module: Module name
            failure_time: ISO format failure timestamp
            failure_reason: Optional failure reason
            executor: Optional executor name
        
        Returns:
            Created TestCaseFailure record
        """
        failure = TestCaseFailure(
            case_id=case_id,
            case_name=case_name,
            module=module,
            failure_time=failure_time,
            failure_reason=failure_reason,
            executor=executor,
            related_dr_trips=[],
        )
        
        self._failure_records[case_id] = failure
        logger.info(f"Recorded test case failure: {case_id}")
        
        return failure
    
    async def query_recent_dr_trips(
        self,
        vehicle_id: str = "",
        hours: int = 24
    ) -> List[DRTripInfo]:
        """
        Query DR trips within the specified time window.
        
        This is a placeholder - actual implementation depends on DR CLI format.
        
        Args:
            vehicle_id: Vehicle identifier (optional)
            hours: Time window in hours (default 24)
        
        Returns:
            List of DRTripInfo objects
        """
        # Placeholder: In production, this would call DR CLI
        # dr trip --list --vehicle <vehicle_id> --since <timestamp>
        
        logger.warning(
            "DR trip query is a placeholder - pending DR CLI format confirmation. "
            "Returns empty list for now."
        )
        
        return []
    
    async def associate_dr_data(
        self,
        failure: TestCaseFailure,
        hours: int = 24
    ) -> FailureDRAssociation:
        """
        Associate a test case failure with recent DR data.
        
        Looks for DR trips within the failure time window and associates
        relevant screenshots/data.
        
        Args:
            failure: The test case failure record
            hours: Time window to search for DR data (default 24h)
        
        Returns:
            FailureDRAssociation with associated DR trips
        """
        # Parse failure time
        try:
            failure_dt = datetime.fromisoformat(failure.failure_time.replace("Z", "+00:00"))
        except Exception:
            failure_dt = datetime.now()
        
        # Calculate time window
        start_time = failure_dt - timedelta(hours=hours)
        
        # Query DR trips (placeholder)
        dr_trips = await self.query_recent_dr_trips(
            hours=hours
        )
        
        # Filter trips by time window
        relevant_trips = []
        for trip in dr_trips:
            try:
                trip_dt = datetime.fromisoformat(trip.start_time.replace("Z", "+00:00"))
                if start_time <= trip_dt <= failure_dt:
                    relevant_trips.append(trip)
            except Exception:
                continue
        
        # Update failure record with related trips
        failure.related_dr_trips = [t.trip_id for t in relevant_trips]
        
        # Calculate confidence based on trip issues
        confidence = 0.0
        if relevant_trips:
            issues_count = sum(1 for t in relevant_trips if t.has_issues)
            confidence = min(0.5 + (issues_count * 0.1), 0.9)
        
        return FailureDRAssociation(
            failure=failure,
            dr_trips=relevant_trips,
            associated_at=datetime.now().isoformat(),
            confidence=confidence,
        )
    
    def format_association_as_markdown(
        self,
        association: FailureDRAssociation
    ) -> str:
        """
        Format failure-DR association as markdown.
        """
        lines = [
            f"## 🔍 用例失败 → DR数据关联",
            "",
            f"**用例ID**: {association.failure.case_id}",
            f"**用例名称**: {association.failure.case_name}",
            f"**模块**: {association.failure.module}",
            f"**失败时间**: {association.failure.failure_time}",
            f"**失败原因**: {association.failure.failure_reason or '未知'}",
            "",
            f"### 🚗 关联的DR行程（共 {len(association.dr_trips)} 个）",
            "",
        ]
        
        if not association.dr_trips:
            lines.append("_⚠️ 在24小时内未找到相关DR行程_")
        else:
            lines.extend([
                "| 行程ID | 车辆 | 开始时间 | 距离(km) | 有问题 |",
                "|------|------|---------|--------|--------|",
            ])
            
            for trip in association.dr_trips:
                issues_str = "⚠️ 是" if trip.has_issues else "✅ 否"
                lines.append(
                    f"| {trip.trip_id} | {trip.vehicle_id} | "
                    f"{trip.start_time} | {trip.distance_km:.1f} | {issues_str} |"
                )
        
        lines.extend([
            "",
            f"**关联置信度**: {association.confidence:.0%}",
            "",
            "---",
            "_由整车测试助手自动生成_",
        ])
        
        return "\n".join(lines)


# Global singleton instances
_change_matcher: Optional[ChangeCaseMatcher] = None
_regression_generator: Optional[RegressionSuggestionGenerator] = None
_ota_analyzer: Optional[OTAChangeAnalyzer] = None
_failure_associator: Optional[TestCaseDRAssociator] = None


def get_change_case_matcher() -> ChangeCaseMatcher:
    """Get the global ChangeCaseMatcher instance."""
    global _change_matcher
    if _change_matcher is None:
        _change_matcher = ChangeCaseMatcher()
    return _change_matcher


def get_regression_generator() -> RegressionSuggestionGenerator:
    """Get the global RegressionSuggestionGenerator instance."""
    global _regression_generator
    if _regression_generator is None:
        _regression_generator = RegressionSuggestionGenerator()
    return _regression_generator


def get_ota_analyzer() -> OTAChangeAnalyzer:
    """Get the global OTAChangeAnalyzer instance."""
    global _ota_analyzer
    if _ota_analyzer is None:
        _ota_analyzer = OTAChangeAnalyzer()
    return _ota_analyzer


def get_failure_associator() -> TestCaseDRAssociator:
    """Get the global TestCaseDRAssociator instance."""
    global _failure_associator
    if _failure_associator is None:
        _failure_associator = TestCaseDRAssociator()
    return _failure_associator
