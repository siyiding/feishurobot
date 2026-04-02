"""
M5 Bug Automation Webhook Endpoints.

Provides endpoints for:
- POST /webhook/bug/create - Natural language bug creation
- POST /webhook/bug/overdue-check - Trigger overdue bug check
- POST /webhook/bug/dr-alert - Handle DR suspicious anomaly alert
- POST /webhook/bug/dr-confirm - Confirm DR anomaly and create bug
"""
from fastapi import APIRouter, HTTPException, Header
from typing import Optional, Dict, Any
from pydantic import BaseModel

from app.core.logging import get_logger
from app.models.schemas import BugPriority
from app.services.bug_automation_service import get_bug_automation_service

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook/bug", tags=["bug-automation"])


class BugCreateNLRequest(BaseModel):
    """Request for natural language bug creation."""
    message: str  # Natural language message
    user_id: str  # User making the request


class BugCreateNLResponse(BaseModel):
    """Response for natural language bug creation."""
    success: bool
    bug_id: Optional[str] = None
    title: Optional[str] = None
    project_key: Optional[str] = None
    priority: Optional[str] = None
    message: str
    error: Optional[str] = None


class OverdueCheckRequest(BaseModel):
    """Request for overdue bug check."""
    project_key: Optional[str] = None  # Optional project filter


class OverdueCheckResponse(BaseModel):
    """Response for overdue bug check."""
    checked_count: int
    overdue_count: int
    reminded_count: int
    overdue_bugs: list


class DRAlertRequest(BaseModel):
    """Request for DR suspicious anomaly alert."""
    alert_level: str  # critical/error/warning/info
    title: str
    description: str
    trip_id: str
    tag_id: Optional[str] = None


class DRConfirmRequest(BaseModel):
    """Request to confirm DR anomaly and create bug."""
    title: str
    description: str
    trip_id: str
    project_key: str = "ICC"
    priority: str = "p1"


# ==================== M5 API Endpoints ====================

@router.post("/create", response_model=BugCreateNLResponse)
async def create_bug_nl(request: BugCreateNLRequest):
    """
    Create a bug from natural language message.
    
    Example:
    POST /webhook/bug/create
    {
        "message": "帮我提个缺陷：CAN总线通信异常，P1优先级",
        "user_id": "ou_xxx"
    }
    """
    try:
        service = get_bug_automation_service()
        
        result = await service.create_bug_from_nl(
            message=request.message,
            user_id=request.user_id,
        )
        
        if result.get("success"):
            return BugCreateNLResponse(
                success=True,
                bug_id=result.get("bug_id"),
                title=result.get("title"),
                project_key=result.get("project_key"),
                priority=result.get("priority"),
                message=result.get("message", "缺陷创建成功"),
            )
        else:
            return BugCreateNLResponse(
                success=False,
                message=result.get("message", "创建失败"),
                error=result.get("error"),
            )
            
    except Exception as e:
        logger.error(f"Error in create_bug_nl: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/overdue-check", response_model=OverdueCheckResponse)
async def check_overdue_bugs(request: OverdueCheckRequest = None):
    """
    Trigger overdue bug check and send reminders.
    
    This endpoint can be called periodically (e.g., every 30 minutes)
    via cron job or manually triggered.
    
    Example:
    POST /webhook/bug/overdue-check
    {"project_key": "ICC"}
    """
    try:
        service = get_bug_automation_service()
        
        project_key = request.project_key if request else None
        overdue_bugs = await service.run_overdue_check(project_key)
        
        return OverdueCheckResponse(
            checked_count=len(overdue_bugs),
            overdue_count=len(overdue_bugs),
            reminded_count=len(overdue_bugs),
            overdue_bugs=[
                {
                    "bug_id": b.bug_id,
                    "title": b.title,
                    "project_key": b.project_key,
                    "assignee": b.assignee,
                    "overdue_days": b.overdue_days,
                    "reminder_count": b.reminder_count,
                }
                for b in overdue_bugs
            ],
        )
        
    except Exception as e:
        logger.error(f"Error in check_overdue_bugs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dr-alert")
async def dr_suspicious_alert(request: DRAlertRequest):
    """
    Handle DR suspicious anomaly alert.
    
    This pushes the alert to the user for confirmation before
    creating a bug.
    
    Example:
    POST /webhook/bug/dr-alert
    {
        "alert_level": "error",
        "title": "VCAN总线异常",
        "description": "VCAN总线在高速行驶时出现通信中断",
        "trip_id": "TRIP-2026-0401-001",
        "tag_id": "TAG-001"
    }
    """
    try:
        service = get_bug_automation_service()
        
        result = await service.handle_dr_suspicious(
            alert_level=request.alert_level,
            title=request.title,
            description=request.description,
            trip_id=request.trip_id,
            tag_id=request.tag_id,
        )
        
        return {
            "status": "ok",
            "pending_confirmation": result.get("pending_confirmation", True),
            "msg_id": result.get("msg_id"),
        }
        
    except Exception as e:
        logger.error(f"Error in dr_suspicious_alert: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/dr-confirm")
async def dr_confirm_and_create(request: DRConfirmRequest):
    """
    Confirm DR anomaly and create bug.
    
    Called after user confirms the DR alert.
    
    Example:
    POST /webhook/bug/dr-confirm
    {
        "title": "VCAN总线异常",
        "description": "VCAN总线在高速行驶时出现通信中断",
        "trip_id": "TRIP-2026-0401-001",
        "project_key": "ICC",
        "priority": "p1"
    }
    """
    try:
        service = get_bug_automation_service()
        
        try:
            priority = BugPriority(request.priority.lower())
        except ValueError:
            priority = BugPriority.P1
        
        result = await service.confirm_dr_anomaly_and_create_bug(
            title=request.title,
            description=request.description,
            trip_id=request.trip_id,
            project_key=request.project_key,
            priority=priority,
        )
        
        if result.get("created"):
            return {
                "status": "ok",
                "created": True,
                "bug_id": result.get("bug_id"),
                "message": f"✅ 缺陷「{result.get('title')}」已创建！\n🆔 ID: `{result.get('bug_id')}`",
            }
        else:
            return {
                "status": "ok",
                "created": False,
                "message": "缺陷创建失败",
            }
            
    except Exception as e:
        logger.error(f"Error in dr_confirm_and_create: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==================== M5.2 Integration with Main Webhook ====================

async def handle_create_bug_nl(message: str, user_id: str) -> Dict[str, Any]:
    """
    Handle natural language bug creation from main webhook.
    
    Called from the main feishu webhook when user wants to create a bug.
    """
    service = get_bug_automation_service()
    return await service.create_bug_from_nl(message=message, user_id=user_id)
