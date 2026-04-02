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
            # M3: 周报摘要推送
            try:
                from app.services.weekly_report_service import get_weekly_report_service
                report_service = get_weekly_report_service()
                project_key = params.get("project_key", "ICC")
                content = await report_service.generate_weekly_summary(project_key)
                
                # Push the report
                from app.services.push_service import get_push_service
                push_svc = get_push_service()
                await push_svc.enqueue_p1_notification(
                    msg_type="weekly_report",
                    title="📊 周报摘要",
                    content=content,
                )
                return "周报已生成并推送，请查收。"
            except Exception as e:
                logger.error(f"Failed to generate weekly report: {e}")
                return f"周报生成失败: {str(e)}"
        
        elif action == "monthly_report":
            return "月报生成功能正在开发中，预计M3阶段完成。"
        else:
            return f"未知的报告类型: {action}"

    elif category == "config":
        # M3: 推送配置管理
        if action == "push_config":
            return await handle_push_config(command, params, sender_id)
        else:
            return f"未知的配置类型: {action}"

    return "无法识别命令类型，请重试。"


async def handle_push_config(command, params: dict, sender_id: str) -> str:
    """
    Handle push configuration commands.
    
    Commands:
    - 查询推送配置: 查看当前配置
    - 设置P1合并窗口30分钟: 设置P1批处理窗口
    - 设置P2频率每小时: 设置P2推送频率
    - 开启推送/关闭推送: 启用/禁用推送
    - 设置免打扰22:00-08:00: 设置免打扰时段
    """
    try:
        from app.services.push_config_service import get_push_config_service, PushConfig, PushFrequency
        
        config_service = get_push_config_service()
        config = await config_service.get_user_config(sender_id)
        
        raw_msg = command.raw_message.lower()
        
        # Query current config
        if "查询" in raw_msg or "查看" in raw_msg or "当前" in raw_msg:
            return format_push_config(config)
        
        # Toggle push on/off
        if "开启推送" in raw_msg or "启用推送" in raw_msg:
            config.push_enabled = True
            await config_service.update_user_config(sender_id, config)
            return "✅ 推送已开启"
        
        if "关闭推送" in raw_msg or "禁用推送" in raw_msg:
            config.push_enabled = False
            await config_service.update_user_config(sender_id, config)
            return "✅ 推送已关闭"
        
        # Set P1 batch window
        if "P1" in raw_msg and "合并窗口" in raw_msg:
            import re
            match = re.search(r"(\\d+)\\s*分钟", raw_msg)
            if match:
                minutes = int(match.group(1))
                config.p1_batch_minutes = minutes
                await config_service.update_user_config(sender_id, config)
                return f"✅ P1合并窗口已设置为 {minutes} 分钟"
        
        # Set P2 frequency
        if "P2" in raw_msg and "频率" in raw_msg:
            if "实时" in raw_msg or "real" in raw_msg:
                config.p2_frequency = PushFrequency.REAL_TIME
            elif "每小时" in raw_msg or "hourly" in raw_msg:
                config.p2_frequency = PushFrequency.HOURLY
            elif "每日" in raw_msg or "daily" in raw_msg:
                config.p2_frequency = PushFrequency.DAILY
            elif "每周" in raw_msg or "weekly" in raw_msg:
                config.p2_frequency = PushFrequency.WEEKLY
            elif "关闭" in raw_msg or "off" in raw_msg:
                config.p2_frequency = PushFrequency.OFF
            else:
                return "未知频率设置"
            
            await config_service.update_user_config(sender_id, config)
            freq_display = {
                "real_time": "实时",
                "hourly": "每小时",
                "daily": "每日",
                "weekly": "每周",
                "off": "关闭",
            }.get(config.p2_frequency.value, config.p2_frequency.value)
            return f"✅ P2推送频率已设置为 {freq_display}"
        
        # Set quiet hours
        if "免打扰" in raw_msg or "quiet" in raw_msg:
            import re
            match = re.search(r"(\\d{1,2}):(\\d{2})\\s*[-~]\\s*(\\d{1,2}):(\\d{2})", raw_msg)
            if match:
                start_hour, start_min = int(match.group(1)), int(match.group(2))
                end_hour, end_min = int(match.group(3)), int(match.group(4))
                
                if 0 <= start_hour <= 23 and 0 <= end_hour <= 23:
                    config.quiet_hours_start = f"{start_hour:02d}:{start_min:02d}"
                    config.quiet_hours_end = f"{end_hour:02d}:{end_min:02d}"
                    await config_service.update_user_config(sender_id, config)
                    return f"✅ 免打扰时段已设置为 {config.quiet_hours_start} - {config.quiet_hours_end}"
        
        # Default: show current config
        return format_push_config(config)
        
    except Exception as e:
        logger.error(f"Failed to handle push config: {e}")
        return f"配置操作失败: {str(e)}"


def format_push_config(config) -> str:
    """Format push configuration for display."""
    freq_display = {
        "real_time": "实时",
        "hourly": "每小时",
        "daily": "每日",
        "weekly": "每周",
        "off": "关闭",
    }.get(config.p2_frequency.value if hasattr(config.p2_frequency, 'value') else config.p2_frequency, str(config.p2_frequency))
    
    quiet = f"{config.quiet_hours_start} - {config.quiet_hours_end}" if config.quiet_hours_start and config.quiet_hours_end else "未设置"
    
    lines = [
        "📬 **推送配置**\n",
        f"- 推送状态: {'✅ 开启' if config.push_enabled else '❌ 关闭'}",
        f"- P1合并窗口: {config.p1_batch_minutes} 分钟",
        f"- P2推送频率: {freq_display}",
        f"- 免打扰时段: {quiet}",
        f"- 周报推送: 每周 {config.weekly_report_day} {config.weekly_report_time}",
        "",
        "可用命令：",
        "• 开启推送 / 关闭推送",
        "• 设置P1合并窗口30分钟",
        "• 设置P2频率每小时/每日/每周/关闭",
        "• 设置免打扰22:00-08:00",
    ]
    return "\n".join(lines)
