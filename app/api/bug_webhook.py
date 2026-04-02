"""
Bug Change Webhook Handler.

Listens for bug change events from Feishu Project (or simulated).
When a bug is created or updated, this handler can trigger notifications.
"""
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional

from app.core.logging import get_logger
from app.services.feishu_project_client import get_project_client
from app.services.push_service import get_push_service

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook/bug", tags=["bug-webhook"])


@router.post("/events")
async def receive_bug_event(
    request: Request,
    x_feishu_event_id: Optional[str] = Header(None),
    x_feishu_token: Optional[str] = Header(None),
):
    """
    Receive bug change events.
    
    This endpoint handles:
    - bug.created: New bug created
    - bug.updated: Bug updated (status, priority, assignee change)
    - bug.deleted: Bug deleted
    
    In production, this would be called by Feishu Project webhooks.
    In development, we simulate events for testing.
    """
    try:
        body = await request.json()
        logger.info(f"Received bug event: {body}")
    except Exception as e:
        logger.error(f"Failed to parse webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event_type = body.get("event_type", "")
    data = body.get("data", {})
    
    if event_type == "bug.created":
        return await handle_bug_created(data)
    elif event_type == "bug.updated":
        return await handle_bug_updated(data)
    elif event_type == "bug.deleted":
        return await handle_bug_deleted(data)
    else:
        logger.warning(f"Unknown bug event type: {event_type}")
        return {"status": "ok", "event": event_type, "handled": False}


async def handle_bug_created(data: dict) -> dict:
    """
    Handle bug.created event.
    
    Triggers:
    - P0 notifications if priority is P0
    - P1 notifications for other priorities
    """
    bug_id = data.get("bug_id", "")
    title = data.get("title", "")
    priority = data.get("priority", "p2")
    project_key = data.get("project_key", "")
    
    logger.info(f"Bug created: {bug_id} - {title} (priority: {priority})")
    
    push_svc = get_push_service()
    
    if priority.lower() == "p0":
        # P0 fast lane
        await push_svc.enqueue_p0_alert(
            title=f"【P0告警】新建严重缺陷",
            content=f"项目{project_key}新建P0缺陷：{title}\nID: {bug_id}",
            bug_id=bug_id,
        )
    else:
        # P1 batch notification
        await push_svc.enqueue_p1_notification(
            msg_type="bug_new",
            title=f"【缺陷创建】{title}",
            content=f"项目{project_key}新建{priority.upper()}缺陷：{title}\nID: {bug_id}",
            url=f"https://project.feishu.cn/open-apis/baike/v1/bug_issues/{bug_id}",
        )
    
    return {"status": "ok", "event": "bug.created", "handled": True}


async def handle_bug_updated(data: dict) -> dict:
    """
    Handle bug.updated event.
    
    Triggers notifications based on what changed.
    """
    bug_id = data.get("bug_id", "")
    title = data.get("title", "")
    changes = data.get("changes", {})
    priority = data.get("priority", "")
    
    logger.info(f"Bug updated: {bug_id} - {changes}")
    
    push_svc = get_push_service()
    
    # Determine change type
    if "status" in changes:
        old_status = changes["status"].get("old_value", "")
        new_status = changes["status"].get("new_value", "")
        
        if new_status in ["resolved", "closed"]:
            # Bug resolved or closed - notify
            await push_svc.enqueue_p1_notification(
                msg_type="bug_update",
                title=f"【缺陷已解决】{title}",
                content=f"缺陷{bug_id}状态已更新为：{new_status}",
                url=f"https://project.feishu.cn/open-apis/baike/v1/bug_issues/{bug_id}",
            )
    
    if priority.lower() == "p0":
        # P0 priority change - fast lane
        await push_svc.enqueue_p0_alert(
            title=f"【P0告警】缺陷优先级调整",
            content=f"缺陷{bug_id} ({title}) 调整为P0",
            bug_id=bug_id,
        )
    
    return {"status": "ok", "event": "bug.updated", "handled": True}


async def handle_bug_deleted(data: dict) -> dict:
    """Handle bug.deleted event."""
    bug_id = data.get("bug_id", "")
    logger.info(f"Bug deleted: {bug_id}")
    return {"status": "ok", "event": "bug.deleted", "handled": True}


# Simulated event endpoint for testing
@router.post("/simulate")
async def simulate_bug_event(
    event_type: str,
    bug_id: str = "bug_001",
    title: str = "模拟缺陷",
    priority: str = "p2",
    project_key: str = "ICC",
    status_change: Optional[str] = None,
):
    """
    Simulate a bug event for testing purposes.
    
    This is for development/testing only.
    """
    data = {
        "bug_id": bug_id,
        "title": title,
        "priority": priority,
        "project_key": project_key,
    }
    
    if status_change:
        data["changes"] = {
            "status": {
                "old_value": "open",
                "new_value": status_change,
            }
        }
    
    if event_type == "bug.created":
        return await handle_bug_created(data)
    elif event_type == "bug.updated":
        return await handle_bug_updated(data)
    elif event_type == "bug.deleted":
        return await handle_bug_deleted(data)
    else:
        return {"error": f"Unknown event type: {event_type}"}
