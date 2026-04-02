"""
Tests for M6 Change Awareness & Regression Assistant.

Tests:
- 8.1 Git repository integration (GitHub/GitLab webhooks)
- 8.2 Change-case matching
- 8.3 Regression suggestion generation
- 8.4 OTA change awareness
- 8.5 Test case failure → DR association (framework)
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.schemas import (
    GitProvider, GitMRStatus, GitChangedFile, GitMergeRequest,
    TestCaseInfo, TestCaseStatus, TestCaseType,
    OTAChangeInfo, TestCaseFailure, DRTripInfo,
    ChangeCaseMatch, RegressionSuggestion, BugPriority,
    ModuleMapping
)
from app.services.git_service import GitService, GitServiceError
from app.services.change_awareness_service import (
    ChangeCaseMatcher,
    RegressionSuggestionGenerator,
    OTAChangeAnalyzer,
    TestCaseDRAssociator,
    get_change_case_matcher,
)


class TestGitService:
    """Tests for Git repository integration (8.1)."""
    
    @pytest.fixture
    def git_service(self):
        """Create a Git service instance."""
        return GitService()
    
    def test_register_github_repository(self, git_service):
        """Test registering a GitHub repository."""
        repo = git_service.register_repository(
            repo_id="test-repo",
            name="test-repo",
            full_name="owner/test-repo",
            provider=GitProvider.GITHUB,
            api_token="ghp_test_token",
            webhook_secret="webhook_secret",
        )
        
        assert repo.repo_id == "test-repo"
        assert repo.full_name == "owner/test-repo"
        assert repo.provider == GitProvider.GITHUB
    
    def test_register_gitlab_repository(self, git_service):
        """Test registering a GitLab repository."""
        repo = git_service.register_repository(
            repo_id="test-gitlab",
            name="test-gitlab",
            full_name="group/test-gitlab",
            provider=GitProvider.GITLAB,
            api_token="glpat_test_token",
            webhook_secret="webhook_secret",
            api_url="https://gitlab.example.com/api/v4",
        )
        
        assert repo.repo_id == "test-gitlab"
        assert repo.full_name == "group/test-gitlab"
        assert repo.provider == GitProvider.GITLAB
        assert repo.api_url == "https://gitlab.example.com/api/v4"
    
    def test_get_repository(self, git_service):
        """Test getting a registered repository."""
        git_service.register_repository(
            repo_id="test-repo",
            name="test-repo",
            full_name="owner/test-repo",
            provider=GitProvider.GITHUB,
        )
        
        repo = git_service.get_repository("test-repo")
        assert repo is not None
        assert repo.repo_id == "test-repo"
    
    def test_list_repositories(self, git_service):
        """Test listing all repositories."""
        git_service.register_repository(
            repo_id="repo1",
            name="repo1",
            full_name="owner/repo1",
            provider=GitProvider.GITHUB,
        )
        git_service.register_repository(
            repo_id="repo2",
            name="repo2",
            full_name="owner/repo2",
            provider=GitProvider.GITLAB,
        )
        
        repos = git_service.list_repositories()
        assert len(repos) == 2
    
    def test_verify_github_signature(self, git_service):
        """Test GitHub webhook signature verification."""
        git_service.register_repository(
            repo_id="test-repo",
            name="test-repo",
            full_name="owner/test-repo",
            provider=GitProvider.GITHUB,
            webhook_secret="test_secret",
        )
        
        import hashlib
        import hmac
        
        payload = b'{"test": "payload"}'
        secret = "test_secret"
        signature = "sha256=" + hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        assert git_service.verify_github_webhook_signature("test-repo", payload, signature)
    
    def test_verify_github_signature_invalid(self, git_service):
        """Test GitHub webhook with invalid signature."""
        git_service.register_repository(
            repo_id="test-repo",
            name="test-repo",
            full_name="owner/test-repo",
            provider=GitProvider.GITHUB,
            webhook_secret="test_secret",
        )
        
        payload = b'{"test": "payload"}'
        invalid_signature = "sha256=invalid"
        
        assert not git_service.verify_github_webhook_signature("test-repo", payload, invalid_signature)


class TestModuleMapping:
    """Tests for module mapping (8.2)."""
    
    @pytest.fixture
    def git_service(self):
        """Create a Git service instance with module mappings."""
        service = GitService()
        
        # Register module mappings
        service.register_module_mapping(
            module_name="ICC-AEB",
            file_patterns=[r".*aeb.*", r".*emergency_braking.*"],
            related_requirements=["REQ-AEB-001"],
            test_case_ids=["TC-AEB-001", "TC-AEB-002"],
        )
        service.register_module_mapping(
            module_name="ICC-LCC",
            file_patterns=[r".*lcc.*", r".*lane_centering.*"],
            related_requirements=["REQ-LCC-001"],
            test_case_ids=["TC-LCC-001"],
        )
        
        return service
    
    def test_register_module_mapping(self, git_service):
        """Test registering module mappings."""
        mapping = git_service.get_module_mapping("ICC-AEB")
        assert mapping is not None
        assert mapping.module_name == "ICC-AEB"
        assert "TC-AEB-001" in mapping.test_case_ids
    
    def test_match_changed_files_simple(self, git_service):
        """Test matching changed files to modules."""
        files = [
            GitChangedFile(filename="src/aeb/brake_control.cpp", status="modified"),
            GitChangedFile(filename="src/lcc/lane_keep.cpp", status="modified"),
        ]
        
        matches = git_service.match_changed_files_to_modules(files)
        
        assert len(matches) >= 2
        modules = [m.matched_module for m in matches]
        assert "ICC-AEB" in modules
        assert "ICC-LCC" in modules
    
    def test_match_changed_files_no_match(self, git_service):
        """Test matching with no matching modules."""
        files = [
            GitChangedFile(filename="src/utils/helper.cpp", status="modified"),
        ]
        
        matches = git_service.match_changed_files_to_modules(files)
        
        # Should have no matches
        assert len(matches) == 0
    
    def test_update_module_mapping(self, git_service):
        """Test updating existing module mapping."""
        git_service.register_module_mapping(
            module_name="ICC-AEB",
            file_patterns=[r".*aeb_v2.*"],  # Updated pattern
            test_case_ids=["TC-AEB-003"],  # Updated cases
        )
        
        mapping = git_service.get_module_mapping("ICC-AEB")
        assert "TC-AEB-003" in mapping.test_case_ids
        assert "TC-AEB-001" not in mapping.test_case_ids


class TestChangeCaseMatcher:
    """Tests for change-case matching (8.2)."""
    
    def test_extract_modules_from_files(self):
        """Test extracting modules from file paths."""
        matcher = ChangeCaseMatcher()
        
        files = [
            GitChangedFile(filename="src/aeb/aeb_control.cpp", status="modified"),
            GitChangedFile(filename="src/lcc/lane_center.cpp", status="modified"),
            GitChangedFile(filename="src/can/canbus.cpp", status="modified"),
        ]
        
        modules = matcher.extract_modules_from_files(files)
        
        assert len(modules) >= 2
        module_names = [m[0] for m in modules]
        assert any("AEB" in m for m in module_names)
        assert any("LCC" in m for m in module_names)
    
    def test_extract_modules_with_keywords(self):
        """Test extracting modules using keyword matching."""
        matcher = ChangeCaseMatcher()
        
        files = [
            GitChangedFile(filename="modules/adas/perception.cpp", status="modified"),
        ]
        
        modules = matcher.extract_modules_from_files(files)
        
        module_names = [m[0] for m in modules]
        assert "ADAS" in module_names or "PERCEPTION" in module_names


class TestOTAChangeAnalyzer:
    """Tests for OTA change awareness (8.4)."""
    
    @pytest.fixture
    def analyzer(self):
        """Create an OTA change analyzer."""
        return OTAChangeAnalyzer()
    
    def test_parse_change_type_feature(self, analyzer):
        """Test parsing feature change type."""
        change_type, confidence = analyzer.parse_change_type(
            "新增AEB功能",
            "添加了新的紧急制动功能"
        )
        assert change_type == "feature"
        assert confidence > 0.5
    
    def test_parse_change_type_bugfix(self, analyzer):
        """Test parsing bugfix change type."""
        change_type, confidence = analyzer.parse_change_type(
            "修复LCC车道居中问题",
            "修复了车道居中控制的bug"
        )
        assert change_type == "bugfix"
        assert confidence > 0.5
    
    def test_parse_change_type_improvement(self, analyzer):
        """Test parsing improvement change type."""
        change_type, confidence = analyzer.parse_change_type(
            "优化ACC巡航性能",
            "提升了自适应巡航的响应速度"
        )
        assert change_type == "improvement"
    
    def test_extract_keywords(self, analyzer):
        """Test extracting keywords from text."""
        keywords = analyzer.extract_keywords(
            "新增AEB紧急制动功能，修复了感知模块的bug",
            "优化了规划算法的效率"
        )
        
        # The extraction preserves Chinese + English combinations
        # Title starts with "新增" so change_type is "feature"
        assert any("AEB" in k or "aeb" in k for k in keywords)
        assert "feature" in keywords  # Title starts with "新增" (feature)
        # Description "优化了规划算法的效率" contains Chinese phrase (the regex doesn't split it cleanly)
        assert any("优化" in k or "improvement" in k for k in keywords)
    
    def test_extract_affected_modules(self, analyzer):
        """Test extracting affected modules."""
        modules = analyzer.extract_affected_modules(
            "AEB紧急制动功能升级",
            "包含感知模块的改进"
        )
        
        assert len(modules) >= 1
        module_names = [m[0] for m in modules]
        assert "ICC-AEB" in module_names or "PERCEPTION" in module_names
    
    def test_format_ota_match_as_markdown(self, analyzer):
        """Test formatting OTA match as markdown."""
        ota_change = OTAChangeInfo(
            version="v2.1.0",
            title="AEB功能升级",
            change_type="feature",
            keywords=["aeb"],
            affected_modules=["ICC-AEB"],
        )
        
        from app.services.change_awareness_service import OTAChangeMatch
        match = OTAChangeMatch(
            ota_change=ota_change,
            matched_cases=[],
            match_reason="测试原因",
        )
        
        markdown = analyzer.format_ota_match_as_markdown(match)
        
        assert "OTA变更感知" in markdown
        assert "v2.1.0" in markdown
        assert "AEB功能升级" in markdown


class TestRegressionSuggestionGenerator:
    """Tests for regression suggestion generation (8.3)."""
    
    @pytest.mark.asyncio
    async def test_generate_suggestion_basic(self):
        """Test generating basic regression suggestion."""
        generator = RegressionSuggestionGenerator()
        
        mr = GitMergeRequest(
            mr_id="123",
            title="bugfix: 修复AEB功能异常",
            source_branch="feature/aeb",
            target_branch="main",
            author="developer",
            status=GitMRStatus.OPEN,
            web_url="https://github.com/owner/repo/pull/123",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            changed_files=[
                GitChangedFile(filename="src/aeb/aeb.cpp", status="modified"),
            ],
        )
        
        cases = [
            TestCaseInfo(
                case_id="TC-AEB-001",
                case_name="AEB功能测试",
                module="ICC-AEB",
                priority="P1",
                status=TestCaseStatus.PENDING,
            ),
        ]
        
        matches = [
            ChangeCaseMatch(
                changed_file="src/aeb/aeb.cpp",
                matched_module="ICC-AEB",
                matched_cases=["TC-AEB-001"],
                match_confidence=0.8,
            ),
        ]
        
        suggestion = await generator.generate_suggestion(mr, cases, matches)
        
        assert suggestion.mr_id == "123"
        assert suggestion.mr_title == "bugfix: 修复AEB功能异常"
        assert len(suggestion.affected_cases) == 1
        assert suggestion.priority == BugPriority.P1  # bugfix -> P1
    
    @pytest.mark.asyncio
    async def test_generate_suggestion_hotfix_priority(self):
        """Test that hotfix MRs get higher priority."""
        generator = RegressionSuggestionGenerator()
        
        mr = GitMergeRequest(
            mr_id="456",
            title="hotfix: 修复严重安全问题",
            source_branch="hotfix/security",
            target_branch="main",
            author="developer",
            status=GitMRStatus.OPEN,
            web_url="https://github.com/owner/repo/pull/456",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            changed_files=[],
        )
        
        suggestion = await generator.generate_suggestion(mr, [], [])
        
        assert suggestion.priority == BugPriority.P0
    
    def test_format_suggestion_as_markdown(self):
        """Test formatting suggestion as markdown."""
        generator = RegressionSuggestionGenerator()
        
        mr = GitMergeRequest(
            mr_id="123",
            title="Test MR",
            source_branch="feature",
            target_branch="main",
            author="dev",
            status=GitMRStatus.OPEN,
            web_url="https://github.com/test/pull/123",
            created_at="",
            updated_at="",
            changed_files=[],
        )
        
        cases = [
            TestCaseInfo(
                case_id="TC-001",
                case_name="测试用例1",
                module="ICC-AEB",
                priority="P1",
                status=TestCaseStatus.PENDING,
            ),
        ]
        
        matches = [
            ChangeCaseMatch(
                changed_file="src/test.cpp",
                matched_module="ICC-AEB",
                matched_cases=["TC-001"],
                match_confidence=0.9,
            ),
        ]
        
        suggestion = RegressionSuggestion(
            mr_id="123",
            mr_title="Test MR",
            mr_url="https://github.com/test/pull/123",
            changed_modules=["ICC-AEB"],
            affected_cases=cases,
            match_details=matches,
            priority=BugPriority.P1,
            reason="测试原因",
        )
        
        markdown = generator.format_suggestion_as_markdown(suggestion)
        
        assert "回归测试建议" in markdown
        assert "Test MR" in markdown
        assert "ICC-AEB" in markdown
        assert "TC-001" in markdown


class TestTestCaseDRAssociator:
    """Tests for test case failure → DR association (8.5)."""
    
    def test_record_failure(self):
        """Test recording a test case failure."""
        associator = TestCaseDRAssociator()
        
        failure = associator.record_failure(
            case_id="TC-001",
            case_name="AEB测试",
            module="ICC-AEB",
            failure_time=datetime.now().isoformat(),
            failure_reason="感知模块异常",
            executor="tester",
        )
        
        assert failure.case_id == "TC-001"
        assert failure.case_name == "AEB测试"
        assert failure.failure_reason == "感知模块异常"
    
    def test_format_association_as_markdown(self):
        """Test formatting failure-DR association as markdown."""
        associator = TestCaseDRAssociator()
        
        failure = TestCaseFailure(
            case_id="TC-001",
            case_name="AEB测试",
            module="ICC-AEB",
            failure_time=datetime.now().isoformat(),
            failure_reason="感知模块异常",
            related_dr_trips=["TRIP-001", "TRIP-002"],
        )
        
        dr_trips = [
            DRTripInfo(
                trip_id="TRIP-001",
                vehicle_id="VEH-001",
                start_time=datetime.now().isoformat(),
                has_issues=True,
            ),
        ]
        
        from app.services.change_awareness_service import FailureDRAssociation
        association = FailureDRAssociation(
            failure=failure,
            dr_trips=dr_trips,
            confidence=0.7,
        )
        
        markdown = associator.format_association_as_markdown(association)
        
        assert "用例失败" in markdown
        assert "TC-001" in markdown
        assert "TRIP-001" in markdown
        assert "关联置信度" in markdown


class TestIntentRecognitionForChange:
    """Tests for intent recognition for change awareness."""
    
    def test_change_awareness_intent_recognition(self):
        """Test recognizing change awareness intents."""
        from app.models.schemas import IntentType
        from app.services.intent_router import recognize_intent
        
        # Regression query intent
        result = recognize_intent("查一下这个MR要回归哪些用例")
        assert result.intent in [IntentType.QUERY, IntentType.CHANGE_AWARENESS]
        
        # OTA change intent
        result = recognize_intent("OTA v2.0有哪些变更")
        assert result.intent in [IntentType.QUERY, IntentType.CHANGE_AWARENESS]
