"""
Intent recognition and routing.

Classifies incoming messages into three types:
- QUERY: 查缺陷、查用例、查里程
- ACTION: 创建缺陷、更新状态
- REPORT: 生成周报/专项报告
"""
import re
from typing import List, Tuple

from app.core.logging import get_logger
from app.models.schemas import IntentType, IntentConfidence, BotCommand

logger = get_logger(__name__)


# Intent keywords - simple rule-based matching
QUERY_PATTERNS = [
    (r"查|查询|看看|有没有|多少|列表", IntentType.QUERY),
    (r"缺陷|bug|问题", IntentType.QUERY),
    (r"用例|场景|测试用例", IntentType.QUERY),
    (r"里程|行驶距离", IntentType.QUERY),
    (r"项目|项目列表", IntentType.QUERY),
]

ACTION_PATTERNS = [
    (r"创建|新建|添加", IntentType.ACTION),
    (r"更新|修改|改变", IntentType.ACTION),
    (r"指派|分配", IntentType.ACTION),
    (r"关闭|完成|解决", IntentType.ACTION),
    (r"状态改成|状态改为|改成|改为", IntentType.ACTION),
]

REPORT_PATTERNS = [
    (r"报告|周报|月报", IntentType.REPORT),
    (r"统计|汇总|总结", IntentType.REPORT),
    (r"导出|生成.*报告", IntentType.REPORT),
]


# Bug creation patterns
CREATE_BUG_PATTERNS = [
    r"创建(?:个?|个)?(?:缺陷|bug|问题)(?:[:：])?(.*)",
    r"新建(?:个?|个)?(?:缺陷|bug|问题)(?:[:：])?(.*)",
    r"添加(?:个?|个)?(?:缺陷|bug|问题)(?:[:：])?(.*)",
    r"(.*)(?:缺陷|bug|问题)",
]

# Bug update patterns
UPDATE_BUG_PATTERNS = [
    r"(bug_\d+|[a-zA-Z0-9_]+)\s*(?:状态|state)?\s*(?:改成|改为|改成|改成|改成)\s*(\w+)",
    r"把\s*(bug_\d+|[a-zA-Z0-9_]+)\s*(?:状态|state)?\s*(?:改成|改为|改成)\s*(\w+)",
    r"更新\s*(?:缺陷)?\s*(?:ID)?\s*(bug_\d+|[a-zA-Z0-9_]+)\s*(?:状态)?\s*(?:改成|改为)?\s*(\w+)",
]


def recognize_intent(message: str) -> IntentConfidence:
    """
    Recognize user intent from message text.
    
    Uses simple keyword matching with confidence scoring.
    """
    if not message or not message.strip():
        return IntentConfidence(
            intent=IntentType.QUERY,
            confidence=0.0,
            reason="Empty message, default to QUERY",
        )

    message_lower = message.lower()
    scores = {IntentType.QUERY: 0.0, IntentType.ACTION: 0.0, IntentType.REPORT: 0.0}
    reasons = []

    # Check QUERY patterns
    for pattern, intent in QUERY_PATTERNS:
        if re.search(pattern, message_lower):
            scores[IntentType.QUERY] += 0.3
            reasons.append(f"QUERY pattern: {pattern}")

    # Check ACTION patterns
    for pattern, intent in ACTION_PATTERNS:
        if re.search(pattern, message_lower):
            scores[IntentType.ACTION] += 0.4
            reasons.append(f"ACTION pattern: {pattern}")

    # Check REPORT patterns
    for pattern, intent in REPORT_PATTERNS:
        if re.search(pattern, message_lower):
            scores[IntentType.REPORT] += 0.35
            reasons.append(f"REPORT pattern: {pattern}")

    # Normalize and pick highest
    total = sum(scores.values())
    if total > 0:
        # Boost highest score
        max_intent = max(scores, key=scores.get)
        max_score = scores[max_intent]
        confidence = min(1.0, max_score / 2.5)  # normalize
    else:
        max_intent = IntentType.QUERY
        confidence = 0.5
        reasons.append("Default to QUERY")

    logger.debug(f"Intent recognition: {message[:50]} -> {max_intent} ({confidence:.2f})")

    return IntentConfidence(
        intent=max_intent,
        confidence=confidence,
        reason="; ".join(reasons[:3]),
    )


def parse_command(message: str, intent: IntentType) -> BotCommand:
    """
    Parse a message into a structured BotCommand.
    
    Extracts sub_command and params based on intent type.
    """
    params = {}

    # Extract project key if present (e.g., "ICC项目", "ICC-xxx")
    # Use negative lookahead/lookbehind to handle mixed ASCII+Chinese text
    project_match = re.search(r"(?<![A-Za-z])([A-Z]{2,}(?:-\w+)?)(?![A-Za-z])", message)
    if project_match:
        params["project_key"] = project_match.group(1).strip()

    # Extract bug_id for update operations
    bug_id_match = re.search(r"(bug_\d+|[a-zA-Z0-9_]{8,})", message, re.IGNORECASE)
    if bug_id_match:
        params["bug_id"] = bug_id_match.group(1).lower()

    # Extract status keywords
    if re.search(r"未关闭|open|新建", message.lower()):
        params["status"] = "open"
    elif re.search(r"进行中|in.?progress|处理中", message.lower()):
        params["status"] = "in_progress"
    elif re.search(r"已解决|resolved|已修复", message.lower()):
        params["status"] = "resolved"
    elif re.search(r"已关闭|closed", message.lower()):
        params["status"] = "closed"
    elif re.search(r"已拒绝|rejected", message.lower()):
        params["status"] = "rejected"

    # Extract priority
    if re.search(r"p0|P0|严重", message):
        params["priority"] = "p0"
    elif re.search(r"p1|P1|高优", message):
        params["priority"] = "p1"
    elif re.search(r"p2|P2", message):
        params["priority"] = "p2"
    elif re.search(r"p3|P3", message):
        params["priority"] = "p3"

    # Extract assignee
    assignee_match = re.search(r"指派给\s*(\S+)", message)
    if assignee_match:
        params["assignee"] = assignee_match.group(1)

    # Extract bug title for creation
    if intent == IntentType.ACTION and ("创建" in message or "新建" in message or "添加" in message):
        # Try to extract the bug title
        title_match = re.search(r"(?:创建|新建|添加)(?:个?)?(?:缺陷|bug|问题)(?:[:：])?\s*(.+?)(?:\s+(?:在|项目|优先级|指派))?$", message)
        if title_match:
            params["bug_title"] = title_match.group(1).strip()
        else:
            # Try alternative pattern: "ICC项目创建一个CAN总线异常缺陷"
            title_match2 = re.search(r"(?:项目)?(.+?)(?:缺陷|bug|问题)$", message)
            if title_match2:
                params["bug_title"] = title_match2.group(1).strip()

    # Sub-command based on intent
    sub_command = None
    if intent == IntentType.QUERY:
        if "缺陷" in message or "bug" in message.lower():
            sub_command = "query_bugs"
        elif "项目" in message:
            sub_command = "query_projects"
        elif "用例" in message:
            sub_command = "query_testcases"
        else:
            sub_command = "query_bugs"  # default query

    elif intent == IntentType.ACTION:
        # Determine if it's create or update
        if "创建" in message or "新建" in message or "添加" in message:
            sub_command = "create_bug"
        elif "更新" in message or "修改" in message or "改成" in message or "改为" in message:
            sub_command = "update_bug"
        elif "指派" in message or "分配" in message:
            sub_command = "update_bug"
        else:
            sub_command = "create_bug"

    elif intent == IntentType.REPORT:
        if "周报" in message:
            sub_command = "weekly_report"
        elif "月报" in message:
            sub_command = "monthly_report"
        else:
            sub_command = "weekly_report"

    return BotCommand(
        raw_message=message,
        intent=intent,
        sub_command=sub_command,
        params=params,
    )


def route_command(command: BotCommand) -> Tuple[str, dict]:
    """
    Route a BotCommand to the appropriate handler.
    
    Returns (handler_name, handler_params).
    """
    if command.intent == IntentType.QUERY:
        return f"query.{command.sub_command or 'query_bugs'}", command.params
    elif command.intent == IntentType.ACTION:
        return f"action.{command.sub_command or 'create_bug'}", command.params
    elif command.intent == IntentType.REPORT:
        return f"report.{command.sub_command or 'weekly_report'}", command.params
    
    return "query.query_bugs", {}
