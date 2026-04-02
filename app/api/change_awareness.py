"""
M6 Change Awareness & Regression Assistant API Endpoints.

Provides endpoints for:
- POST /webhook/git/github - GitHub webhook receiver
- POST /webhook/git/gitlab - GitLab webhook receiver
- POST /webhook/ota - OTA change webhook
- POST /webhook/testcase/failure - Test case failure webhook
- GET /regression/mr/{mr_id} - Get regression suggestion for MR
- GET /regression/suggestions - List pending regression suggestions
- POST /change/match - Manual change-case matching
- GET /ota/analyze - Analyze OTA changes
- POST /failure/associate-dr - Associate failure with DR data

8.1 Git仓接入 (3 days) - Webhook endpoints
8.2 变更-用例匹配 (3 days) - Matching endpoints
8.3 回归建议生成 (2 days) - Suggestion endpoints
8.4 OTA变更感知 (2 days) - OTA endpoints
8.5 用例失败→DR关联 (3 days) - DR association endpoints
"""
from fastapi import APIRouter, HTTPException, Header, Request, Query
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
import json

from app.core.logging import get_logger
from app.models.schemas import (
    GitProvider, GitMRStatus, GitMergeRequest, GitChangedFile,
    RegressionSuggestion, OTAChangeInfo, OTAChangeMatch,
    TestCaseFailure, FailureDRAssociation,
    ChangeCaseMatch, TestCaseInfo, BugPriority,
    PushMessage
)
from app.services.git_service import get_git_service
from app.services.change_awareness_service import (
    get_change_case_matcher,
    get_regression_generator,
    get_ota_analyzer,
    get_failure_associator
)
from app.services.push_service import get_push_service

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["change-awareness"])


# ==================== Request/Response Models ====================

class GitWebhookRequest(BaseModel):
    """Git webhook event payload (generic)."""
    repo_id: str
    provider: str  # "github" or "gitlab"
    event_type: str  # Event type (e.g., "pull_request", "merge_request")
    payload: Dict[str, Any]


class GitWebhookResponse(BaseModel):
    """Response for git webhook."""
    success: bool
    message: str
    mr_id: Optional[str] = None
    regression_triggered: bool = False


class OTAWebhookRequest(BaseModel):
    """OTA change webhook payload."""
    version: str
    title: str
    description: Optional[str] = None
    change_type: Optional[str] = "feature"
    affected_modules: Optional[List[str]] = []
    released_at: Optional[str] = None


class OTAWebhookResponse(BaseModel):
    """Response for OTA webhook."""
    success: bool
    message: str
    matched_cases_count: int = 0


class TestCaseFailureRequest(BaseModel):
    """Test case failure webhook payload."""
    case_id: str
    case_name: str
    module: str
    failure_time: str
    failure_reason: Optional[str] = None
    executor: Optional[str] = None


class TestCaseFailureResponse(BaseModel):
    """Response for test case failure."""
    success: bool
    message: str
    dr_trips_count: int = 0
    association: Optional[Dict[str, Any]] = None


class ChangeMatchRequest(BaseModel):
    """Manual change-case matching request."""
    changed_files: List[str]  # List of changed file paths
    commit_message: Optional[str] = None


class ChangeMatchResponse(BaseModel):
    """Response for change-case matching."""
    success: bool
    matches: List[Dict[str, Any]]
    suggested_cases: List[Dict[str, Any]]


class RegressionPushRequest(BaseModel):
    """Request to push regression suggestion to engineer."""
    suggestion: RegressionSuggestion
    engineer_open_id: Optional[str] = None


class RegressionPushResponse(BaseModel):
    """Response for regression push."""
    success: bool
    message: str


# ==================== Git Webhook Endpoints (8.1) ====================

@router.post("/git/github", response_model=GitWebhookResponse)
async def receive_github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None),
):
    """
    Receive GitHub webhook events.
    
    Supports:
    - pull_request: MR opened, updated, closed
    - push: Push events (for branch-based triggers)
    
    After processing, triggers regression suggestion if it's a relevant MR.
    """
    try:
        body = await request.body()
        payload = json.loads(body)
    except Exception as e:
        logger.error(f"Failed to parse webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    repo_id = payload.get("repository", {}).get("full_name", "")
    
    if not repo_id:
        raise HTTPException(status_code=400, detail="Missing repository info")
    
    git_service = get_git_service()
    
    # Verify signature if secret is configured
    if x_hub_signature_256:
        if not git_service.verify_github_webhook_signature(repo_id, body, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Handle the event
    mr = await git_service.handle_github_webhook_event(repo_id, x_github_event, payload)
    
    if mr is None:
        return GitWebhookResponse(
            success=True,
            message=f"Event {x_github_event} processed (no MR data)"
        )
    
    # Generate regression suggestion
    regression_triggered = False
    if mr.status == GitMRStatus.OPEN:
        try:
            matcher = get_change_case_matcher()
            generator = get_regression_generator()
            
            matches = await matcher.match_mr_to_test_cases(mr)
            affected_cases = await matcher.get_regression_cases_for_mr(mr)
            
            if affected_cases:
                suggestion = await generator.generate_suggestion(
                    mr=mr,
                    affected_cases=affected_cases,
                    match_details=matches,
                    reason=f"GitHub MR modifies {len(mr.changed_files)} files"
                )
                
                # Push to test engineer
                await generator.push_suggestion_to_engineer(suggestion)
                regression_triggered = True
                
                logger.info(f"Regression suggestion triggered for MR #{mr.mr_id}")
        except Exception as e:
            logger.error(f"Failed to generate regression suggestion: {e}")
    
    return GitWebhookResponse(
        success=True,
        message=f"GitHub MR #{mr.mr_id} processed",
        mr_id=mr.mr_id,
        regression_triggered=regression_triggered,
    )


@router.post("/git/gitlab", response_model=GitWebhookResponse)
async def receive_gitlab_webhook(
    request: Request,
    x_gitlab_token: Optional[str] = Header(None),
    x_gitlab_event: Optional[str] = Header(None),
):
    """
    Receive GitLab webhook events.
    
    Supports:
    - merge_request: MR opened, updated, merged, closed
    - push: Push events
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    repo_id = payload.get("project", {}).get("path_with_namespace", "")
    
    if not repo_id:
        raise HTTPException(status_code=400, detail="Missing project info")
    
    git_service = get_git_service()
    
    # Verify token if secret is configured
    if x_gitlab_token:
        if not git_service.verify_gitlab_webhook_token(repo_id, x_gitlab_token):
            raise HTTPException(status_code=401, detail="Invalid token")
    
    # Handle the event
    mr = await git_service.handle_gitlab_webhook_event(repo_id, x_gitlab_event, payload)
    
    if mr is None:
        return GitWebhookResponse(
            success=True,
            message=f"Event {x_gitlab_event} processed (no MR data)"
        )
    
    # Generate regression suggestion
    regression_triggered = False
    if mr.status == GitMRStatus.OPEN:
        try:
            matcher = get_change_case_matcher()
            generator = get_regression_generator()
            
            matches = await matcher.match_mr_to_test_cases(mr)
            affected_cases = await matcher.get_regression_cases_for_mr(mr)
            
            if affected_cases:
                suggestion = await generator.generate_suggestion(
                    mr=mr,
                    affected_cases=affected_cases,
                    match_details=matches,
                    reason=f"GitLab MR modifies {len(mr.changed_files)} files"
                )
                
                await generator.push_suggestion_to_engineer(suggestion)
                regression_triggered = True
                
                logger.info(f"Regression suggestion triggered for MR !{mr.mr_id}")
        except Exception as e:
            logger.error(f"Failed to generate regression suggestion: {e}")
    
    return GitWebhookResponse(
        success=True,
        message=f"GitLab MR !{mr.mr_id} processed",
        mr_id=mr.mr_id,
        regression_triggered=regression_triggered,
    )


# ==================== OTA Change Endpoints (8.4) ====================

@router.post("/ota", response_model=OTAWebhookResponse)
async def receive_ota_change(request: OTAWebhookRequest):
    """
    Receive OTA change notification.
    
    Analyzes the OTA change and matches affected test cases.
    Pushes notification to relevant test engineers.
    """
    try:
        # Create OTA change info
        ota_change = OTAChangeInfo(
            version=request.version,
            title=request.title,
            description=request.description,
            change_type=request.change_type,
            affected_modules=request.affected_modules,
            released_at=request.released_at,
        )
        
        # Analyze OTA change
        analyzer = get_ota_analyzer()
        match = await analyzer.analyze_ota_change(ota_change)
        
        if match.matched_cases:
            # Format and push notification
            content = analyzer.format_ota_match_as_markdown(match)
            
            push_service = get_push_service()
            push_msg = PushMessage(
                id=f"ota_{ota_change.version}_{int(json.loads('{}').get('ts', 0))}",
                level="P1",
                msg_type="ota_change",
                title=f"📦 OTA变更感知: {ota_change.title[:30]}...",
                content=content,
                created_at=ota_change.released_at or "",
            )
            
            await push_service.enqueue_p1_notification(push_msg)
        
        return OTAWebhookResponse(
            success=True,
            message=f"OTA change {ota_change.version} analyzed",
            matched_cases_count=len(match.matched_cases),
        )
        
    except Exception as e:
        logger.error(f"Failed to process OTA change: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Test Case Failure Endpoints (8.5) ====================

@router.post("/testcase/failure", response_model=TestCaseFailureResponse)
async def receive_testcase_failure(request: TestCaseFailureRequest):
    """
    Receive test case failure notification.
    
    Associates the failure with recent DR (Data Report) data.
    Note: DR association details pending DR CLI format confirmation.
    """
    try:
        associator = get_failure_associator()
        
        # Record the failure
        failure = associator.record_failure(
            case_id=request.case_id,
            case_name=request.case_name,
            module=request.module,
            failure_time=request.failure_time,
            failure_reason=request.failure_reason,
            executor=request.executor,
        )
        
        # Associate with DR data
        association = await associator.associate_dr_data(failure)
        
        # Format and push if there are related trips
        if association.dr_trips:
            content = associator.format_association_as_markdown(association)
            
            push_service = get_push_service()
            push_msg = PushMessage(
                id=f"failure_{request.case_id}_{int(json.loads('{}').get('ts', 0))}",
                level="P1",
                msg_type="testcase_failure_dr",
                title=f"🔍 用例失败关联DR: {request.case_name[:30]}...",
                content=content,
                created_at=request.failure_time,
            )
            
            await push_service.enqueue_p1_notification(push_msg)
        
        return TestCaseFailureResponse(
            success=True,
            message=f"Test case failure {request.case_id} recorded",
            dr_trips_count=len(association.dr_trips),
            association={
                "failure_case_id": failure.case_id,
                "dr_trips_count": len(association.dr_trips),
                "confidence": association.confidence,
            },
        )
        
    except Exception as e:
        logger.error(f"Failed to process test case failure: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Manual Matching Endpoints (8.2) ====================

@router.post("/change/match", response_model=ChangeMatchResponse)
async def match_changes(request: ChangeMatchRequest):
    """
    Manually trigger change-case matching.
    
    Useful for testing or when Git webhooks are not available.
    """
    try:
        matcher = get_change_case_matcher()
        
        # Convert file paths to GitChangedFile objects
        changed_files = [
            GitChangedFile(filename=f, status="modified") for f in request.changed_files
        ]
        
        # Match to modules
        git_service = get_git_service()
        matches = git_service.match_changed_files_to_modules(changed_files)
        
        # Get affected cases
        modules = list(set(m.matched_module for m in matches))
        affected_cases = await matcher.get_regression_cases_for_mr(
            GitMergeRequest(
                mr_id="manual",
                title=request.commit_message or "Manual matching",
                source_branch="",
                target_branch="",
                author="",
                status=GitMRStatus.OPEN,
                web_url="",
                created_at="",
                updated_at="",
                changed_files=changed_files,
            )
        )
        
        return ChangeMatchResponse(
            success=True,
            matches=[m.model_dump() for m in matches],
            suggested_cases=[
                {
                    "case_id": c.case_id,
                    "case_name": c.case_name,
                    "module": c.module,
                    "priority": c.priority,
                } for c in affected_cases
            ],
        )
        
    except Exception as e:
        logger.error(f"Failed to match changes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Regression Suggestion Endpoints (8.3) ====================

@router.post("/regression/push", response_model=RegressionPushResponse)
async def push_regression_suggestion(request: RegressionPushRequest):
    """
    Push a regression suggestion to test engineer.
    
    Useful for re-sending or forwarding suggestions.
    """
    try:
        generator = get_regression_generator()
        success = await generator.push_suggestion_to_engineer(
            suggestion=request.suggestion,
            engineer_open_id=request.engineer_open_id,
        )
        
        return RegressionPushResponse(
            success=success,
            message="Regression suggestion pushed" if success else "Failed to push",
        )
        
    except Exception as e:
        logger.error(f"Failed to push regression suggestion: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/regression/suggestions", response_model=List[Dict[str, Any]])
async def list_regression_suggestions(
    limit: int = Query(default=20, ge=1, le=100),
    mr_id: Optional[str] = None,
):
    """
    List regression suggestions.
    
    In a production system, this would query a database.
    Currently returns empty list as suggestions are generated on-demand.
    """
    # Placeholder: In production, query database
    return []


# ==================== OTA Analysis Endpoints (8.4) ====================

@router.get("/ota/analyze", response_model=Dict[str, Any])
async def analyze_ota_changes(
    version: str = Query(..., description="OTA version"),
    title: str = Query(..., description="OTA title"),
    description: Optional[str] = Query(default=None, description="OTA description"),
    change_type: Optional[str] = Query(default="feature", description="Change type"),
):
    """
    Analyze OTA changes and get suggested test cases.
    
    Useful for pre-release testing planning.
    """
    try:
        analyzer = get_ota_analyzer()
        
        ota_change = OTAChangeInfo(
            version=version,
            title=title,
            description=description,
            change_type=change_type,
        )
        
        match = await analyzer.analyze_ota_change(ota_change)
        
        return {
            "success": True,
            "ota_change": match.ota_change.model_dump(),
            "matched_cases_count": len(match.matched_cases),
            "match_reason": match.match_reason,
            "cases": [
                {
                    "case_id": c.case_id,
                    "case_name": c.case_name,
                    "module": c.module,
                    "priority": c.priority,
                    "status": c.status.value if c.status else None,
                } for c in match.matched_cases
            ],
            "formatted_report": analyzer.format_ota_match_as_markdown(match),
        }
        
    except Exception as e:
        logger.error(f"Failed to analyze OTA changes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== DR Association Endpoints (8.5) ====================

@router.post("/failure/associate-dr", response_model=Dict[str, Any])
async def associate_failure_with_dr(
    case_id: str = Query(..., description="Test case ID"),
    case_name: str = Query(..., description="Test case name"),
    module: str = Query(..., description="Module name"),
    failure_time: str = Query(..., description="Failure time (ISO format)"),
    failure_reason: Optional[str] = Query(default=None, description="Failure reason"),
    hours: int = Query(default=24, ge=1, le=168, description="Time window in hours"),
):
    """
    Associate a test case failure with DR data.
    
    Note: DR trip query is a placeholder pending DR CLI format confirmation.
    """
    try:
        associator = get_failure_associator()
        
        # Record failure
        failure = associator.record_failure(
            case_id=case_id,
            case_name=case_name,
            module=module,
            failure_time=failure_time,
            failure_reason=failure_reason,
        )
        
        # Associate with DR
        association = await associator.associate_dr_data(failure, hours=hours)
        
        return {
            "success": True,
            "association": {
                "failure_case_id": failure.case_id,
                "dr_trips_count": len(association.dr_trips),
                "confidence": association.confidence,
                "associated_at": association.associated_at,
            },
            "formatted_report": associator.format_association_as_markdown(association),
        }
        
    except Exception as e:
        logger.error(f"Failed to associate failure with DR: {e}")
        raise HTTPException(status_code=500, detail=str(e))
