"""Feishu webhook endpoints."""
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional

from app.core.logging import get_logger
from app.models.schemas import FeishuWebhookEvent, BotResponse, IntentType
from app.services.intent_router import recognize_intent, parse_command, route_command
from app.services.feishu_project_client import get_project_client, FeishuProjectClient

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "feishurobot"}


@router.post("/feishu")
async def receive_feishu_event(
    request: Request,
    x_feishu_event_id: Optional[str] = Header(None),
    x_feishu_token: Optional[str] = Header(None),
):
    """
    Receive and process Feishu webhook events.
    
    This endpoint handles:
    - im.message.receive_v1: Incoming messages to the bot
    
    The event data is parsed and routed to the appropriate handler
    based on the message content and recognized intent.
    """
    try:
        body = await request.json()
        logger.info(f"Received Feishu webhook: {body.get('event', 'unknown')}")
    except Exception as e:
        logger.error(f"Failed to parse webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = body.get("event", "")
    
    # Handle message receive events
    if event == "im.message.receive_v1":
        return await handle_message_receive(body)
    
    # Acknowledge other event types
    return {"status": "ok", "event": event}


async def handle_message_receive(body: dict) -> dict:
    """
    Handle im.message.receive_v1 event.
    
    Flow:
    1. Parse message content
    2. Recognize intent (QUERY/ACTION/REPORT)
    3. Route to appropriate handler
    4. Return formatted response
    """
    try:
        message_content = body.get("data", {}).get("message", {}).get("content", "")
        sender_id = body.get("data", {}).get("sender", {}).get("sender_id", {}).get("open_id", "unknown")
        
        logger.info(f"Message from {sender_id}: {message_content[:100]}")

        # Parse message - handle JSON content from Feishu
        try:
            import json
            content_data = json.loads(message_content)
            text = content_data.get("text", "")
        except (json.JSONDecodeError, TypeError):
            text = message_content

        if not text:
            return BotResponse(
                content="我收到了你的消息，但无法理解空内容。请重新发送。",
                intent=IntentType.QUERY,
            )

        # Step 1: Intent recognition
        intent_confidence = recognize_intent(text)
        logger.info(f"Recognized intent: {intent_confidence.intent} ({intent_confidence.confidence:.2f})")

        # Step 2: Parse command
        command = parse_command(text, intent_confidence.intent)
        logger.info(f"Parsed command: {command.sub_command}, params={command.params}")

        # Step 3: Route and execute
        handler_name, handler_params = route_command(command)
        logger.info(f"Routing to: {handler_name}")

        response_content = await execute_handler(handler_name, handler_params, command)

        return BotResponse(
            content=response_content,
            intent=intent_confidence.intent,
        )

    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        return BotResponse(
            content=f"处理消息时出现错误: {str(e)}",
            intent=IntentType.QUERY,
            error=str(e),
        )


async def execute_handler(handler_name: str, params: dict, command) -> str:
    """
    Execute the appropriate handler based on routing.
    
    Handler naming: query.query_bugs, action.create_bug, report.weekly_report
    """
    parts = handler_name.split(".")
    category = parts[0]
    action = parts[1] if len(parts) > 1 else "query_bugs"

    client = get_project_client()

    if category == "query":
        if action == "query_bugs":
            from app.models.schemas import BugQueryRequest, BugStatus
            req = BugQueryRequest(
                project_key=params.get("project_key"),
                status=BugStatus(params["status"]) if params.get("status") else None,
                priority=params.get("priority"),
                page_size=20,
            )
            # Filter to only open/未关闭 bugs by default if not specified
            if not params.get("status") and "未关闭" in command.raw_message:
                req.status = BugStatus.OPEN
            
            resp = await client.query_bugs(req)
            return client.format_bug_list(resp)
        
        elif action == "query_projects":
            projects = await client.list_projects()
            if not projects:
                return "没有找到项目。"
            lines = ["**可用项目列表：**\n"]
            for p in projects:
                lines.append(f"• **{p.name}** (`{p.key}`)")
            return "\n".join(lines)
        
        elif action == "query_testcases":
            from app.models.schemas import TestCaseQueryRequest, TestCaseType, TestCaseStatus
            from app.services.feishu_sheet_client import get_sheet_client
            
            # Parse query parameters
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
            
            client_sheet = get_sheet_client()
            resp = await client_sheet.query_test_cases(req)
            return client_sheet.format_test_case_list(resp)
        
        else:
            return f"未知的查询类型: {action}"

    elif category == "action":
        if action == "create_bug":
            from app.models.schemas import BugCreateRequest, BugPriority
            
            # Get bug title from params or raw message
            bug_title = params.get("bug_title", "")
            if not bug_title:
                # Try to extract from raw message
                import re
                title_match = re.search(r"(?:创建|新建|添加)(?:个?)?(?:缺陷|bug|问题)(?:[:：])?\s*(.+?)(?:\s+(?:在|项目|优先级|指派))?$", command.raw_message)
                if title_match:
                    bug_title = title_match.group(1).strip()
                else:
                    # Pattern: "ICC项目创建一个CAN总线异常"
                    title_match2 = re.search(r"(?:项目)?(.+?)(?:缺陷|bug|问题)$", command.raw_message)
                    if title_match2:
                        bug_title = title_match2.group(1).strip()
            
            if not bug_title:
                return "请提供缺陷标题。例如：「创建CAN总线通信异常缺陷」"
            
            # Get project key
            project_key = params.get("project_key", "ICC")  # Default to ICC
            
            # Get priority
            priority_str = params.get("priority", "p2")
            try:
                priority = BugPriority(priority_str.lower())
            except ValueError:
                priority = BugPriority.P2
            
            # Get assignee
            assignee = params.get("assignee")
            
            req = BugCreateRequest(
                title=bug_title,
                project_key=project_key,
                priority=priority,
                assignee=assignee,
            )
            
            resp = await client.create_bug(req)
            
            # Trigger P0 push if priority is P0
            if priority == BugPriority.P0:
                try:
                    from app.services.push_service import get_push_service
                    push_svc = get_push_service()
                    await push_svc.enqueue_p0_alert(
                        title=f"【P0告警】新建严重缺陷",
                        content=f"项目{project_key}新建P0缺陷：{bug_title}",
                        bug_id=resp.bug_id,
                    )
                except Exception as e:
                    logger.warning(f"Failed to enqueue P0 alert: {e}")
            
            return resp.message
        
        elif action == "update_bug":
            from app.models.schemas import BugUpdateRequest, BugStatus, BugPriority
            
            bug_id = params.get("bug_id")
            if not bug_id:
                # Try to extract bug_id from raw message
                import re
                bug_match = re.search(r"(bug_\d+|[a-zA-Z0-9_]{8,})", command.raw_message, re.IGNORECASE)
                if bug_match:
                    bug_id = bug_match.group(1).lower()
            
            if not bug_id:
                return "请提供要更新的缺陷ID。例如：「把bug_001状态改成已解决」"
            
            # Determine status to set
            status = None
            if params.get("status"):
                try:
                    status = BugStatus(params["status"])
                except ValueError:
                    pass
            
            # Determine priority
            priority = None
            if params.get("priority"):
                try:
                    priority = BugPriority(params["priority"].lower())
                except ValueError:
                    pass
            
            assignee = params.get("assignee")
            
            req = BugUpdateRequest(
                bug_id=bug_id,
                status=status,
                priority=priority,
                assignee=assignee,
            )
            
            resp = await client.update_bug(req)
            
            # Trigger P0 push if priority is P0 or status changed to resolved
            if priority == BugPriority.P0 or (status == BugStatus.RESOLVED and resp.updated):
                try:
                    from app.services.push_service import get_push_service
                    push_svc = get_push_service()
                    await push_svc.enqueue_p0_alert(
                        title=f"P0缺陷更新" if priority == BugPriority.P0 else "【P0已解决】",
                        content=f"缺陷{bug_id}状态更新",
                        bug_id=bug_id,
                    )
                except Exception as e:
                    logger.warning(f"Failed to enqueue P0 alert: {e}")
            
            return resp.message
        
        else:
            return f"未知的操作类型: {action}"

    elif category == "report":
        if action == "weekly_report":
            return "周报生成功能正在开发中，预计M3阶段完成。"
        elif action == "monthly_report":
            return "月报生成功能正在开发中，预计M3阶段完成。"
        else:
            return f"未知的报告类型: {action}"

    return "无法识别命令类型，请重试。"
