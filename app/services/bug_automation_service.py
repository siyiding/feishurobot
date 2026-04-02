"""
Bug Management Automation Service — M5 Module 7.

Provides automated bug management capabilities:
- 7.1 L1 Automatic Bug Creation from natural language
- 7.2 Status Change Auto-Sync to all stakeholders  
- 7.3 Overdue Reminder System (auto @ assignee)
- 7.4 L3 Suspicious Anomaly Push (optional, pending DR CLI format confirmation)

M5开发周期：7-10人天
"""
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.models.schemas import BugStatus, BugPriority, BugInfo, BugCreateRequest
from app.services.feishu_project_client import get_project_client, FeishuProjectClient
from app.services.push_service import get_push_service

logger = get_logger(__name__)


# ============================================================================
# 7.1 L1 自动创建 - 自然语言结构化提取
# ============================================================================

class BugCreationExtractor:
    """
    Extract structured bug information from natural language.
    
    Handles patterns like:
    - "帮我提个缺陷：CAN总线通信异常，P1优先级"
    - "创建ICC项目缺陷：方向盘助力失效，P0优先级"
    - "新建一个ADAS项目的问题：雷达检测异常，紧急"
    """
    
    # 项目关键字映射
    PROJECT_KEYWORDS = {
        "ICC": ["icc", "整车", "整车测试"],
        "AEB": ["aeb", "自动紧急制动", "紧急制动"],
        "LCC": ["lcc", "车道居中", "居中控制"],
        "ADAS": ["adas", "高级辅助", "辅助驾驶"],
        "COCKPIT": ["cockpit", "座舱", "智能座舱"],
    }
    
    # 优先级关键字映射
    PRIORITY_KEYWORDS = {
        "p0": ["p0", "严重", "紧急", "立刻", "马上", "致命"],
        "p1": ["p1", "高优", "重要", "优先"],
        "p2": ["p2", "一般", "普通", "常规"],
        "p3": ["p3", "低优", "轻微", "建议"],
    }
    
    # 描述提取正则
    DESCRIPTION_PATTERNS = [
        r"[，,](?:描述|现象|问题|情况|原因)是[：:]?\s*(.+?)(?:[，,]?优先级|$)",
        r"[，,](?:症状|表现)为[：:]?\s*(.+?)(?:[，,]?优先级|$)",
        r"[，,]具体[：:]?\s*(.+?)(?:[，,]?优先级|$)",
    ]
    
    @classmethod
    def extract_from_message(cls, message: str) -> Dict[str, Any]:
        """
        Extract bug creation parameters from natural language message.
        
        Args:
            message: User's natural language message
            
        Returns:
            Dict with keys: title, project_key, priority, description, assignee, confidence
        """
        result = {
            "title": "",
            "project_key": "ICC",  # Default project
            "priority": "p2",       # Default priority
            "description": "",
            "assignee": None,
            "confidence": 0.0,
        }
        
        # Extract title (bug description)
        title = cls._extract_title(message)
        if title:
            result["title"] = title
            result["confidence"] += 0.4
        
        # Extract project key
        project_key = cls._extract_project_key(message)
        if project_key:
            result["project_key"] = project_key
            result["confidence"] += 0.2
        
        # Extract priority
        priority = cls._extract_priority(message)
        if priority:
            result["priority"] = priority
            result["confidence"] += 0.2
        
        # Extract description
        description = cls._extract_description(message)
        if description:
            result["description"] = description
            result["confidence"] += 0.2
        
        # Extract assignee
        assignee = cls._extract_assignee(message)
        if assignee:
            result["assignee"] = assignee
        
        return result
    
    @classmethod
    def _extract_title(cls, message: str) -> str:
        """Extract bug title from message."""
        # Pattern: "帮我提个缺陷：XXX" or "创建XXX缺陷"
        # We need to handle: title might be followed by priority, project, assignee, description
        patterns = [
            # Pattern: "帮我提个缺陷：CAN总线异常，P1，指派给张三"
            r"(?:帮我提|创建|新建|添加)(?:个?|个)?(?:缺陷|bug|问题)(?:[:：])?\s*(.+?)(?:[，,](?:[Pp]\d|严重|高优|紧急|一般|普通)?(?:优先级)?|指派给|$)",
            # Pattern: "创建ICC项目缺陷：雷达检测异常"
            r"(?:ICC|AEB|LCC|ADAS)(?:项目)?.*?(?:缺陷|bug|问题)(?:[:：])?\s*(.+?)(?:[，,]|$)",
            # Pattern: "XXX缺陷" without project prefix
            r"(?:缺陷|bug|问题)(?:[:：])?\s*(.+?)(?:[，,]|$)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                title = match.group(1).strip()
                # Clean up trailing punctuation and extra chars
                title = re.sub(r"[，。、\s]+$", "", title)
                if title and len(title) >= 2:
                    return title
        
        # Fallback: take the whole message cleaned
        cleaned = re.sub(r"(?:帮我提|创建|新建|添加)(?:个?|个)?(?:缺陷|bug|问题)", "", message)
        # Remove priority, project, assignee keywords
        cleaned = re.sub(r"[Pp]0|[Pp]1|[Pp]2|[Pp]3|优先级|项目|指派给|描述是", "", cleaned)
        cleaned = re.sub(r"(?:ICC|AEB|LCC|ADAS)(?:项目)?", "", cleaned)
        cleaned = re.sub(r"[，。、\s]+$", "", cleaned)
        cleaned = cleaned.strip("：:").strip()
        if cleaned:
            return cleaned[:100]
        
        return ""
    
    @classmethod
    def _extract_project_key(cls, message: str) -> Optional[str]:
        """Extract project key from message."""
        msg_lower = message.lower()
        for project_key, keywords in cls.PROJECT_KEYWORDS.items():
            for keyword in keywords:
                if keyword in msg_lower or keyword in message:
                    return project_key
        return None
    
    @classmethod
    def _extract_priority(cls, message: str) -> Optional[str]:
        """Extract priority from message."""
        msg_lower = message.lower()
        for priority, keywords in cls.PRIORITY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in msg_lower or keyword in message:
                    return priority
        return None
    
    @classmethod
    def _extract_description(cls, message: str) -> str:
        """Extract bug description from message."""
        for pattern in cls.DESCRIPTION_PATTERNS:
            match = re.search(pattern, message)
            if match:
                return match.group(1).strip()
        return ""
    
    @classmethod
    def _extract_assignee(cls, message: str) -> Optional[str]:
        """Extract assignee from message."""
        patterns = [
            r"指派给\s*(\S+)",
            r"负责人\s*[是为]?\s*(\S+)",
            r"分配给\s*(\S+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                return match.group(1)
        return None


# ============================================================================
# 7.2 状态变更自动同步
# ============================================================================

@dataclass
class BugChangeEvent:
    """Bug change event for notification tracking."""
    bug_id: str
    title: str
    old_status: Optional[BugStatus]
    new_status: BugStatus
    old_priority: Optional[BugPriority]
    new_priority: Optional[BugPriority]
    old_assignee: Optional[str]
    new_assignee: Optional[str]
    changed_by: str  # user_id who made the change
    changed_at: datetime = field(default_factory=datetime.now)
    
    def has_status_change(self) -> bool:
        if self.old_status is None and self.new_status is not None:
            return False  # Initial status set is not a "change"
        return self.old_status != self.new_status
    
    def has_priority_change(self) -> bool:
        return self.old_priority != self.new_priority
    
    def has_assignee_change(self) -> bool:
        return self.old_assignee != self.new_assignee
    
    def get_change_summary(self) -> str:
        """Generate human-readable change summary."""
        changes = []
        if self.has_status_change():
            changes.append(f"状态: {self.old_status} → {self.new_status}")
        if self.has_priority_change():
            changes.append(f"优先级: {self.old_priority} → {self.new_priority}")
        if self.has_assignee_change():
            changes.append(f"负责人: {self.old_assignee} → {self.new_assignee}")
        return "; ".join(changes) if changes else "无变更"


class BugChangeNotifier:
    """
    Notifies stakeholders when bug status changes.
    
    M4 integration: Uses push_service to send notifications
    to all relevant parties when a bug is updated.
    """
    
    def __init__(self):
        self._push_service = get_push_service()
    
    async def notify_bug_created(
        self,
        bug_id: str,
        title: str,
        project_key: str,
        priority: BugPriority,
        assignee: Optional[str],
        creator: str,
    ) -> None:
        """Notify when a new bug is created."""
        priority_str = priority.value.upper() if hasattr(priority, 'value') else str(priority)
        
        if priority == BugPriority.P0 or priority_str.upper() == "P0":
            # P0 fast lane notification
            await self._push_service.enqueue_p0_alert(
                title=f"【P0告警】新建严重缺陷",
                content=f"📋 项目[{project_key}]新建P0缺陷：{title}\n🆔 ID: `{bug_id}`\n👤 创建者: {creator}",
                bug_id=bug_id,
            )
        else:
            # P1 batch notification
            await self._push_service.enqueue_p1_notification(
                msg_type="bug_new",
                title=f"【缺陷创建】{title}",
                content=f"📋 项目[{project_key}]新建{priority_str}缺陷\n🆔 ID: `{bug_id}`\n👤 创建者: {creator}",
                url=f"https://project.feishu.cn/open-apis/baike/v1/bug_issues/{bug_id}",
            )
    
    async def notify_bug_updated(
        self,
        event: BugChangeEvent,
    ) -> None:
        """Notify when a bug is updated."""
        changes = event.get_change_summary()
        if not changes or changes == "无变更":
            return
        
        priority_str = event.new_priority.value.upper() if event.new_priority and hasattr(event.new_priority, 'value') else "P2"
        
        # P0 fast lane for priority changes or resolution
        if (event.has_priority_change() and priority_str.upper() == "P0") or \
           (event.has_status_change() and event.new_status == BugStatus.RESOLVED):
            await self._push_service.enqueue_p0_alert(
                title=f"【P0告警】缺陷{'升级为P0' if priority_str.upper() == 'P0' else '已解决'}",
                content=f"🆔 缺陷{event.bug_id}更新：{changes}\n📌 {event.title}",
                bug_id=event.bug_id,
            )
        else:
            # P1 batch notification for other changes
            msg_type = "bug_update"
            if event.has_assignee_change():
                msg_type = "bug_reassigned"
            
            await self._push_service.enqueue_p1_notification(
                msg_type=msg_type,
                title=f"【缺陷更新】{event.title}",
                content=f"🆔 缺陷{event.bug_id}已更新\n📝 变更内容：{changes}",
                url=f"https://project.feishu.cn/open-apis/baike/v1/bug_issues/{event.bug_id}",
            )


# ============================================================================
# 7.3 逾期催办系统
# ============================================================================

@dataclass
class OverdueBug:
    """Overdue bug with tracking info."""
    bug_id: str
    title: str
    project_key: str
    assignee: str
    status: BugStatus
    priority: BugPriority
    created_at: datetime
    due_at: datetime
    overdue_days: int
    reminder_count: int  # Number of reminders sent


class OverdueReminderService:
    """
    Automated overdue bug reminder service.
    
    Tracks bugs that exceed their due date and sends
    automated @assignee reminders.
    
    Features:
    - Configurable SLA thresholds (default: P0=1h, P1=24h, P2=72h, P3=168h)
    - Periodic check (configurable, default every 30 minutes)
    - Escalation for repeated non-response
    - Exclusion for weekends/after-hours (configurable)
    """
    
    # Default SLA thresholds (hours)
    DEFAULT_SLA_HOURS = {
        "p0": 1,
        "p1": 24,
        "p2": 72,
        "p3": 168,  # 1 week
    }
    
    # Reminder escalation thresholds
    MAX_REMINDERS = {
        "p0": 5,   # Max 5 reminders for P0
        "p1": 3,   # Max 3 reminders for P1
        "p2": 2,   # Max 2 reminders for P2
        "p3": 1,   # Max 1 reminder for P3
    }
    
    def __init__(self):
        self._push_service = get_push_service()
        self._project_client = get_project_client()
        # In-memory tracking for reminders sent
        self._reminder_tracker: Dict[str, int] = {}  # bug_id -> reminder_count
    
    def calculate_sla_hours(self, priority: BugPriority) -> int:
        """Get SLA hours for a given priority."""
        priority_str = priority.value.lower() if hasattr(priority, 'value') else str(priority).lower()
        return self.DEFAULT_SLA_HOURS.get(priority_str, 72)
    
    def is_overdue(self, bug: BugInfo, due_hours: Optional[int] = None) -> bool:
        """Check if a bug is overdue."""
        if bug.status in [BugStatus.RESOLVED, BugStatus.CLOSED, BugStatus.REJECTED]:
            return False  # Closed bugs are not overdue
        
        if not bug.created_at:
            return False
        
        # Parse created_at
        try:
            if isinstance(bug.created_at, str):
                created = datetime.fromisoformat(bug.created_at.replace("Z", "+00:00"))
            else:
                created = bug.created_at
        except (ValueError, TypeError):
            return False
        
        # Calculate due date
        hours = due_hours or self.calculate_sla_hours(bug.priority)
        due_date = created + timedelta(hours=hours)
        
        # Make datetime.now() UTC for comparison with UTC parsed dates
        from datetime import timezone
        now = datetime.now(timezone.utc)
        return now > due_date
    
    def get_overdue_days(self, bug: BugInfo) -> int:
        """Calculate how many days overdue a bug is."""
        if not bug.created_at:
            return 0
        
        try:
            if isinstance(bug.created_at, str):
                created = datetime.fromisoformat(bug.created_at.replace("Z", "+00:00"))
            else:
                created = bug.created_at
        except (ValueError, TypeError):
            return 0
        
        hours = self.calculate_sla_hours(bug.priority)
        due_date = created + timedelta(hours=hours)
        
        from datetime import timezone
        now = datetime.now(timezone.utc)
        delta = now - due_date
        return max(0, delta.days)
    
    async def check_and_remind(self, project_key: Optional[str] = None) -> List[OverdueBug]:
        """
        Check all open bugs and send reminders for overdue ones.
        
        Returns list of OverdueBug that received reminders.
        """
        from app.models.schemas import BugQueryRequest
        
        client = self._project_client
        
        # Query open bugs
        req = BugQueryRequest(
            project_key=project_key,
            status=BugStatus.OPEN,
            page_size=100,
        )
        resp = await client.query_bugs(req)
        
        overdue_bugs = []
        
        for bug in resp.bugs:
            if self.is_overdue(bug):
                overdue_days = self.get_overdue_days(bug)
                
                # Check reminder count
                reminder_count = self._reminder_tracker.get(bug.bug_id, 0)
                max_reminders = self.MAX_REMINDERS.get(bug.priority.value, 1)
                
                if reminder_count >= max_reminders:
                    logger.info(f"Bug {bug.bug_id} exceeded max reminders ({reminder_count}/{max_reminders}), skipping")
                    continue
                
                # Send reminder
                await self._send_reminder(bug, overdue_days)
                self._reminder_tracker[bug.bug_id] = reminder_count + 1
                
                overdue_bugs.append(OverdueBug(
                    bug_id=bug.bug_id,
                    title=bug.title,
                    project_key=bug.project_key,
                    assignee=bug.assignee or "未分配",
                    status=bug.status,
                    priority=bug.priority,
                    created_at=bug.created_at or datetime.now(),
                    due_at=datetime.now(),  # Approximation
                    overdue_days=overdue_days,
                    reminder_count=reminder_count + 1,
                ))
        
        return overdue_bugs
    
    async def _send_reminder(self, bug: BugInfo, overdue_days: int) -> None:
        """Send a reminder notification for an overdue bug."""
        priority_emoji = {"p0": "🔴", "p1": "🟠", "p2": "🟡", "p3": "🟢"}.get(bug.priority.value, "⚪")
        
        title = f"【逾期催办】{bug.title}"
        content = (
            f"{priority_emoji} 缺陷 `{bug.bug_id}` 已逾期 **{overdue_days}天**\n"
            f"📌 标题：{bug.title}\n"
            f"📋 项目：{bug.project_key}\n"
            f"👤 负责人：@{bug.assignee or '未分配'}\n"
            f"🕐 请尽快处理！"
        )
        
        # Use P1 notification (batched)
        await self._push_service.enqueue_p1_notification(
            msg_type="bug_overdue",
            title=title,
            content=content,
            url=f"https://project.feishu.cn/open-apis/baike/v1/bug_issues/{bug.bug_id}",
        )
        
        logger.info(f"Sent overdue reminder for bug {bug.bug_id} to {bug.assignee}")


# ============================================================================
# 7.4 L3疑似异常推送（选做）
# ============================================================================

class DRSuspiciousAnomalyHandler:
    """
    Handle DR (Data Report) suspicious anomaly detection.
    
    Flow:
    1. DR alert received → push to user for confirmation
    2. User confirms → create bug automatically
    3. User rejects → log and dismiss
    
    Note: Requires DR CLI return format confirmation from DR platform team.
    This is a placeholder implementation.
    """
    
    def __init__(self):
        self._push_service = get_push_service()
    
    async def handle_dr_alert(
        self,
        alert_level: str,
        title: str,
        description: str,
        trip_id: str,
        tag_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Handle a DR alert and push for user confirmation.
        
        Args:
            alert_level: DR alert level (critical/error/warning/info)
            title: Alert title
            description: Alert description
            trip_id: Related trip ID
            tag_id: Optional related tag ID
            
        Returns:
            Dict with confirmation request info
        """
        # Push suspicious anomaly notification for user confirmation
        alert_emoji = {
            "critical": "🔴",
            "error": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
        }.get(alert_level.lower(), "❓")
        
        content = (
            f"{alert_emoji} **DR疑似异常告警**\n\n"
            f"📌 标题：{title}\n"
            f"📝 描述：{description}\n"
            f"🚗 行程ID：{trip_id}\n"
            f"🏷️ TagID：{tag_id or 'N/A'}\n\n"
            f"---\n"
            f"请确认是否需要创建缺陷？\n"
            f"回复「是」创建缺陷 / 「否」忽略"
        )
        
        # Map DR level to push level
        level_map = {
            "critical": "P0",
            "error": "P1", 
            "warning": "P1",
            "info": "P2",
        }
        push_level = level_map.get(alert_level.lower(), "P1")
        
        msg_id = await self._push_service.enqueue_message(
            level=push_level,
            msg_type="dr_suspicious",
            title=f"【DR疑似异常】{title}",
            content=content,
            url=f"https://dr.platform.com/trips/{trip_id}",
        )
        
        return {
            "pending_confirmation": True,
            "msg_id": msg_id,
            "trip_id": trip_id,
            "tag_id": tag_id,
        }
    
    async def create_bug_from_dr(
        self,
        title: str,
        description: str,
        trip_id: str,
        project_key: str = "ICC",
        priority: BugPriority = BugPriority.P1,
    ) -> Dict[str, Any]:
        """
        Create a bug from confirmed DR anomaly.
        
        Args:
            title: Bug title
            description: Bug description
            trip_id: Related DR trip ID
            project_key: Project key
            priority: Bug priority
            
        Returns:
            Dict with created bug info
        """
        client = get_project_client()
        
        # Enhance description with DR trip info
        enhanced_description = (
            f"{description}\n\n"
            f"---DR异常详情---\n"
            f"行程ID：{trip_id}\n"
            f"来源：DR平台自动检测"
        )
        
        req = BugCreateRequest(
            title=f"[DR] {title}",
            project_key=project_key,
            priority=priority,
            description=enhanced_description,
        )
        
        resp = await client.create_bug(req)
        
        return {
            "bug_id": resp.bug_id,
            "title": resp.title,
            "status": resp.status,
            "priority": resp.priority,
            "created": resp.created,
        }


# ============================================================================
# Bug Automation Service - Main Entry Point
# ============================================================================

class BugAutomationService:
    """
    Main entry point for Bug Management Automation.
    
    Coordinates all M5 bug automation features:
    - 7.1 L1 Automatic Bug Creation
    - 7.2 Status Change Auto-Sync
    - 7.3 Overdue Reminder
    - 7.4 DR Suspicious Anomaly (optional)
    """
    
    def __init__(self):
        self.creation_extractor = BugCreationExtractor()
        self.change_notifier = BugChangeNotifier()
        self.overdue_service = OverdueReminderService()
        self.dr_handler = DRSuspiciousAnomalyHandler()
    
    # ==================== 7.1 L1 自动创建 ====================
    
    async def create_bug_from_nl(self, message: str, user_id: str) -> Dict[str, Any]:
        """
        Create a bug from natural language message.
        
        Args:
            message: User's natural language request
            user_id: User ID making the request
            
        Returns:
            Dict with creation result
        """
        # Extract structured info
        extracted = self.creation_extractor.extract_from_message(message)
        
        if not extracted["title"]:
            return {
                "success": False,
                "error": "无法提取缺陷标题，请提供更详细的描述",
                "message": "请告诉我缺陷的具体描述，例如：「帮我提个缺陷：CAN总线通信异常，P1优先级」",
            }
        
        # Create bug
        client = get_project_client()
        
        try:
            priority = BugPriority(extracted["priority"])
        except ValueError:
            priority = BugPriority.P2
        
        req = BugCreateRequest(
            title=extracted["title"],
            project_key=extracted["project_key"],
            priority=priority,
            description=extracted["description"],
            assignee=extracted["assignee"],
        )
        
        resp = await client.create_bug(req)
        
        # Send notifications
        await self.change_notifier.notify_bug_created(
            bug_id=resp.bug_id,
            title=resp.title,
            project_key=extracted["project_key"],
            priority=priority,
            assignee=extracted["assignee"],
            creator=user_id,
        )
        
        return {
            "success": True,
            "bug_id": resp.bug_id,
            "title": resp.title,
            "project_key": extracted["project_key"],
            "priority": priority.value,
            "message": resp.message,
        }
    
    # ==================== 7.2 状态变更同步 ====================
    
    async def handle_bug_change(
        self,
        bug_id: str,
        old_status: Optional[BugStatus],
        new_status: BugStatus,
        old_priority: Optional[BugPriority],
        new_priority: Optional[BugPriority],
        old_assignee: Optional[str],
        new_assignee: Optional[str],
        changed_by: str,
        bug_title: str,
    ) -> None:
        """Handle bug change and notify stakeholders."""
        event = BugChangeEvent(
            bug_id=bug_id,
            title=bug_title,
            old_status=old_status,
            new_status=new_status,
            old_priority=old_priority,
            new_priority=new_priority,
            old_assignee=old_assignee,
            new_assignee=new_assignee,
            changed_by=changed_by,
        )
        
        await self.change_notifier.notify_bug_updated(event)
    
    # ==================== 7.3 逾期催办 ====================
    
    async def run_overdue_check(self, project_key: Optional[str] = None) -> List[OverdueBug]:
        """Run overdue bug check and send reminders."""
        return await self.overdue_service.check_and_remind(project_key)
    
    # ==================== 7.4 DR疑似异常 ====================
    
    async def handle_dr_suspicious(
        self,
        alert_level: str,
        title: str,
        description: str,
        trip_id: str,
        tag_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle DR suspicious anomaly alert."""
        return await self.dr_handler.handle_dr_alert(
            alert_level=alert_level,
            title=title,
            description=description,
            trip_id=trip_id,
            tag_id=tag_id,
        )
    
    async def confirm_dr_anomaly_and_create_bug(
        self,
        title: str,
        description: str,
        trip_id: str,
        project_key: str = "ICC",
        priority: BugPriority = BugPriority.P1,
    ) -> Dict[str, Any]:
        """Create bug from confirmed DR anomaly."""
        return await self.dr_handler.create_bug_from_dr(
            title=title,
            description=description,
            trip_id=trip_id,
            project_key=project_key,
            priority=priority,
        )


# ============================================================================
# Singleton
# ============================================================================

_bug_automation_service: Optional[BugAutomationService] = None


def get_bug_automation_service() -> BugAutomationService:
    """Get singleton BugAutomationService instance."""
    global _bug_automation_service
    if _bug_automation_service is None:
        _bug_automation_service = BugAutomationService()
    return _bug_automation_service
