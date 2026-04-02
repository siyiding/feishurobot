"""
DR (Data Report) Platform Client — CLI Mode.

DR平台对接客户端，基于CLI调用模式重写（2026-04-02修正）。

核心变更：
- 不再使用 HTTP API，改用 subprocess 调用 DR CLI
- 查询维度：按行程（trip）查问题列表，不是信号/时间戳
- 不解析 bag 包，只查询关联元数据
- 图表生成暂不实现（等 CLI 返回格式确认后再设计）

CLI 命令格式（初稿，待 DR 平台确认）：
- dr query --trip <trip_id>         # 按行程查问题列表
- dr tag --info <tag_id>             # 查看 Tag 信息
- dr trip --list --vehicle <vehicle_id>  # 列出车辆行程

返回格式：TBD（可能是结构化文本或 JSON），框架预置解析占位。
"""
import json
import subprocess
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# CLI 命令格式（初稿，待 DR 平台确认）
# ============================================================================

class DR_CLI_CMD:
    """DR CLI 命令占位符。实际命令格式待 DR 平台确认后填充。"""
    
    # base command - may include auth flags like --token or env vars
    BASE = "dr"
    
    @staticmethod
    def query_trip(trip_id: str) -> List[str]:
        """
        按行程ID查询问题列表。
        TODO: 确认具体命令格式，如 dr query --trip <id> 或其他
        """
        return [DR_CLI_CMD.BASE, "query", "--trip", trip_id]
    
    @staticmethod
    def tag_info(tag_id: str) -> List[str]:
        """
        查看 Tag 详情（Tag 与 Bag 包一一对应）。
        TODO: 确认具体命令格式
        """
        return [DR_CLI_CMD.BASE, "tag", "--info", tag_id]
    
    @staticmethod
    def list_trips(vehicle_id: Optional[str] = None, limit: int = 20) -> List[str]:
        """
        列出车辆行程。
        TODO: 确认具体命令格式
        """
        cmd = [DR_CLI_CMD.BASE, "trip", "--list"]
        if vehicle_id:
            cmd.extend(["--vehicle", vehicle_id])
        cmd.extend(["--limit", str(limit)])
        return cmd


# ============================================================================
# 数据模型
# ============================================================================

class DRAlertLevel(str, Enum):
    """DR 问题/告警级别。"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class DRProblem:
    """DR 问题记录（对应 CLI 返回的一条问题）。"""
    
    def __init__(
        self,
        problem_id: str,
        title: str,
        level: DRAlertLevel,
        trip_id: str,
        tag_id: Optional[str] = None,
        description: Optional[str] = None,
        created_at: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.problem_id = problem_id
        self.title = title
        self.level = level if isinstance(level, DRAlertLevel) else DRAlertLevel(level)
        self.trip_id = trip_id
        self.tag_id = tag_id
        self.description = description
        self.created_at = created_at
        self.extra = extra or {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "title": self.title,
            "level": self.level.value,
            "trip_id": self.trip_id,
            "tag_id": self.tag_id,
            "description": self.description,
            "created_at": self.created_at,
            **self.extra,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DRProblem":
        return cls(
            problem_id=str(data.get("problem_id", "")),
            title=str(data.get("title", "")),
            level=DRAlertLevel(data.get("level", "unknown")),
            trip_id=str(data.get("trip_id", "")),
            tag_id=data.get("tag_id"),
            description=data.get("description"),
            created_at=data.get("created_at"),
            extra={k: v for k, v in data.items() 
                   if k not in ("problem_id", "title", "level", "trip_id", "tag_id", "description", "created_at")},
        )
    
    def summary(self) -> str:
        """生成单条问题的简短摘要，用于飞书消息。"""
        level_emoji = {
            DRAlertLevel.INFO: "ℹ️",
            DRAlertLevel.WARNING: "⚠️",
            DRAlertLevel.ERROR: "❌",
            DRAlertLevel.CRITICAL: "🔴",
            DRAlertLevel.UNKNOWN: "❓",
        }
        emoji = level_emoji.get(self.level, "❓")
        return f"{emoji} [{self.level.value.upper()}] {self.title}（行程:{self.trip_id}）"


class DRTag:
    """DR Tag 记录（Tag 与 Bag 包一一对应）。"""
    
    def __init__(
        self,
        tag_id: str,
        vehicle_id: Optional[str] = None,
        trip_id: Optional[str] = None,
        created_at: Optional[str] = None,
        notes: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.tag_id = tag_id
        self.vehicle_id = vehicle_id
        self.trip_id = trip_id
        self.created_at = created_at
        self.notes = notes
        self.extra = extra or {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag_id": self.tag_id,
            "vehicle_id": self.vehicle_id,
            "trip_id": self.trip_id,
            "created_at": self.created_at,
            "notes": self.notes,
            **self.extra,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DRTag":
        return cls(
            tag_id=str(data.get("tag_id", "")),
            vehicle_id=data.get("vehicle_id"),
            trip_id=data.get("trip_id"),
            created_at=data.get("created_at"),
            notes=data.get("notes"),
            extra={k: v for k, v in data.items()
                   if k not in ("tag_id", "vehicle_id", "trip_id", "created_at", "notes")},
        )


class DRTrip:
    """DR 行程记录。"""
    
    def __init__(
        self,
        trip_id: str,
        vehicle_id: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        distance_km: Optional[float] = None,
        tag_ids: Optional[List[str]] = None,
        problem_count: int = 0,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.trip_id = trip_id
        self.vehicle_id = vehicle_id
        self.start_time = start_time
        self.end_time = end_time
        self.distance_km = distance_km
        self.tag_ids = tag_ids or []
        self.problem_count = problem_count
        self.extra = extra or {}
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "trip_id": self.trip_id,
            "vehicle_id": self.vehicle_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "distance_km": self.distance_km,
            "tag_ids": self.tag_ids,
            "problem_count": self.problem_count,
            **self.extra,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DRTrip":
        return cls(
            trip_id=str(data.get("trip_id", "")),
            vehicle_id=data.get("vehicle_id"),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            distance_km=data.get("distance_km"),
            tag_ids=data.get("tag_ids", []),
            problem_count=data.get("problem_count", 0),
            extra={k: v for k, v in data.items()
                   if k not in ("trip_id", "vehicle_id", "start_time", "end_time",
                               "distance_km", "tag_ids", "problem_count")},
        )


# ============================================================================
# CLI 返回解析器（占位，等格式确认后填充）
# ============================================================================

class DRCLIResponseParser:
    """
    DR CLI 返回格式解析器。
    
    TODO: 等 DR 平台确认 CLI 返回格式（结构化文本/JSON）后，
          实现具体的 parse_* 方法。
    
    预期输入格式（初稿猜测，可能是 JSON 或格式化文本）：
    
    JSON 格式示例（猜测）:
    {
      "trip_id": "TRIP-2026-0401-001",
      "problems": [
        {
          "problem_id": "PRB-001",
          "title": "VCAN总线异常",
          "level": "error",
          "tag_id": "TAG-001",
          "created_at": "2026-04-01T10:30:00Z"
        }
      ]
    }
    
    文本格式示例（猜测）:
    TRIP: TRIP-2026-0401-001
    ========================
    Problem List:
      [ERROR] PRB-001 | VCAN总线异常 | TAG-001 | 2026-04-01 10:30:00
      [WARN]  PRB-002 | 方向盘转角超限 | TAG-001 | 2026-04-01 10:35:00
    """
    
    @classmethod
    def parse_problem_list(
        cls,
        raw_output: str,
        trip_id: str,
    ) -> Tuple[List[DRProblem], Optional[str]]:
        """
        解析 dr query --trip <trip_id> 的原始输出。
        
        Args:
            raw_output: CLI stdout 原始输出
            trip_id: 对应的行程 ID
            
        Returns:
            (problems列表, error_message)
        """
        if not raw_output or not raw_output.strip():
            return [], None
        
        # 尝试 JSON 格式
        try:
            data = json.loads(raw_output)
            return cls._parse_json_problems(data, trip_id)
        except (json.JSONDecodeError, ValueError):
            pass
        
        # TODO: 尝试文本格式解析（等格式确认后实现）
        # return cls._parse_text_problems(raw_output, trip_id)
        
        # 降级：返回原始文本作为 description
        logger.warning(f"[DR CLI] Unknown output format, treating as raw text. trip_id={trip_id}")
        return [
            DRProblem(
                problem_id="unknown",
                title=raw_output.strip()[:200],
                level=DRAlertLevel.UNKNOWN,
                trip_id=trip_id,
                description="原始输出（格式待确认）",
            )
        ], None
    
    @classmethod
    def _parse_json_problems(
        cls,
        data: Dict[str, Any],
        trip_id: str,
    ) -> Tuple[List[DRProblem], Optional[str]]:
        """解析 JSON 格式的问题列表。"""
        problems = []
        
        # 支持多种 JSON 结构
        problem_list = data.get("problems", [])
        if isinstance(data, dict) and "problems" not in data:
            # 尝试直接从 data 提取（有些 API 直接返回数组）
            if isinstance(data, list):
                problem_list = data
        
        for item in problem_list:
            if not isinstance(item, dict):
                continue
            try:
                # 确保 trip_id 关联上
                item["trip_id"] = item.get("trip_id", trip_id)
                problems.append(DRProblem.from_dict(item))
            except Exception as e:
                logger.warning(f"[DR CLI] Failed to parse problem item: {e}, item={item}")
                continue
        
        return problems, None
    
    @classmethod
    def parse_tag_info(
        cls,
        raw_output: str,
        tag_id: str,
    ) -> Tuple[Optional[DRTag], Optional[str]]:
        """解析 dr tag --info <tag_id> 的原始输出。"""
        if not raw_output or not raw_output.strip():
            return None, "Empty output"
        
        try:
            data = json.loads(raw_output)
            return DRTag.from_dict(data.get("tag", data)), None
        except json.JSONDecodeError:
            # TODO: 文本格式解析
            logger.warning(f"[DR CLI] Unknown tag info format. tag_id={tag_id}")
            return DRTag(tag_id=tag_id, notes=raw_output.strip()[:500]), None
    
    @classmethod
    def parse_trip_list(
        cls,
        raw_output: str,
    ) -> Tuple[List[DRTrip], Optional[str]]:
        """解析 dr trip --list 的原始输出。"""
        if not raw_output or not raw_output.strip():
            return [], None
        
        try:
            data = json.loads(raw_output)
            trip_list = data.get("trips", []) if isinstance(data, dict) else data
            return [DRTrip.from_dict(t) for t in trip_list], None
        except json.JSONDecodeError:
            logger.warning(f"[DR CLI] Unknown trip list format.")
            return [], None


# ============================================================================
# DR CLI 执行器
# ============================================================================

class DRCLIExecutor:
    """
    DR CLI 命令执行器。
    
    封装 subprocess 调用，处理：
    - 命令执行与超时
    - 返回码校验
    - stderr 日志
    - Mock 模式（CLI 不可用时降级）
    """
    
    def __init__(self, cli_path: str = "dr", timeout: int = 30):
        """
        Args:
            cli_path: DR CLI 可执行文件路径，默认 "dr"（PATH 中查找）
            timeout: 命令超时时间（秒）
        """
        self.cli_path = cli_path
        self.timeout = timeout
    
    def run(self, args: List[str], input_text: Optional[str] = None) -> Tuple[int, str, str]:
        """
        执行 DR CLI 命令。
        
        Args:
            args: 命令参数列表，如 ["query", "--trip", "TRIP-001"]
            input_text: stdin 输入（可选）
            
        Returns:
            (return_code, stdout, stderr)
        """
        cmd = [self.cli_path] + args
        logger.info(f"[DR CLI] Executing: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                input=input_text,
            )
            return result.returncode, result.stdout, result.stderr
        
        except subprocess.TimeoutExpired:
            logger.error(f"[DR CLI] Command timeout after {self.timeout}s: {' '.join(cmd)}")
            return -1, "", f"Command timeout after {self.timeout}s"
        
        except FileNotFoundError:
            logger.error(f"[DR CLI] CLI executable not found: {self.cli_path}")
            return -2, "", f"CLI not found: {self.cli_path}"
        
        except Exception as e:
            logger.error(f"[DR CLI] Unexpected error: {e}")
            return -3, "", str(e)


# ============================================================================
# DR Client 主类（CLI 模式）
# ============================================================================

class DRClient:
    """
    DR 平台客户端（CLI 模式）。
    
    设计原则：
    - subprocess 调用 DR CLI，不走 HTTP API
    - 按行程（trip）查询问题列表，不拉 bag 包
    - 所有方法均为异步（async），方便后续扩展
    - CLI 命令格式初稿，待 DR 平台确认
    
    使用示例：
    
        client = DRClient()
        
        # 按行程查问题列表
        problems, err = await client.query_trip_problems("TRIP-2026-0401-001")
        
        # 查看 Tag 详情
        tag, err = await client.get_tag_info("TAG-001")
        
        # 列出车辆行程
        trips, err = await client.list_trips(vehicle_id="VEH-001")
    
    TODO（等 DR 平台确认后填充）：
    - CLI 实际命令格式
    - CLI 鉴权方式（环境变量？--token 参数？）
    - 返回数据具体字段
    """
    
    def __init__(self):
        settings = get_settings()
        
        # CLI 配置
        self.cli_path = getattr(settings, 'dr_cli_path', 'dr')
        self.cli_timeout = getattr(settings, 'dr_cli_timeout', 30)
        self._executor = DRCLIExecutor(cli_path=self.cli_path, timeout=self.cli_timeout)
        
        # Mock 模式：CLI 不可用时降级
        self._mock_mode = False
    
    # ==================== 公开 API ====================
    
    async def query_trip_problems(
        self,
        trip_id: str,
    ) -> Tuple[List[DRProblem], Optional[str]]:
        """
        按行程ID查询问题列表。
        
        这是 DR 查询的核心方法，机器人对 DR 的主要操作。
        
        Args:
            trip_id: 行程 ID，如 "TRIP-2026-0401-001"
            
        Returns:
            (problems列表, error_message)
            - problems: 问题对象列表（可能为空）
            - error_message: 出错时返回错误信息（此时 problems 可能部分有效）
        """
        cmd = DR_CLI_CMD.query_trip(trip_id)
        returncode, stdout, stderr = self._executor.run(cmd)
        
        if returncode != 0:
            error_msg = f"CLI error (code={returncode}): {stderr}"
            logger.error(f"[DR Client] {error_msg}")
            
            if returncode == -2:  # CLI not found
                self._mock_mode = True
                logger.warning("[DR Client] Falling back to MOCK mode")
                return self._mock_trip_problems(trip_id)
            
            return [], error_msg
        
        problems, parse_err = DRCLIResponseParser.parse_problem_list(stdout, trip_id)
        if parse_err:
            logger.warning(f"[DR Client] Parse warning: {parse_err}")
        
        return problems, parse_err
    
    async def get_tag_info(
        self,
        tag_id: str,
    ) -> Tuple[Optional[DRTag], Optional[str]]:
        """
        查看 Tag 详情（Tag 与 Bag 包一一对应）。
        
        Args:
            tag_id: Tag ID
            
        Returns:
            (tag_info, error_message)
        """
        cmd = DR_CLI_CMD.tag_info(tag_id)
        returncode, stdout, stderr = self._executor.run(cmd)
        
        if returncode != 0:
            if returncode == -2:
                self._mock_mode = True
                return self._mock_tag_info(tag_id)
            return None, f"CLI error: {stderr}"
        
        return DRCLIResponseParser.parse_tag_info(stdout, tag_id)
    
    async def list_trips(
        self,
        vehicle_id: Optional[str] = None,
        limit: int = 20,
    ) -> Tuple[List[DRTrip], Optional[str]]:
        """
        列出车辆行程。
        
        Args:
            vehicle_id: 车辆 ID（可选，不填则查所有）
            limit: 返回数量上限
            
        Returns:
            (trips列表, error_message)
        """
        cmd = DR_CLI_CMD.list_trips(vehicle_id=vehicle_id, limit=limit)
        returncode, stdout, stderr = self._executor.run(cmd)
        
        if returncode != 0:
            if returncode == -2:
                self._mock_mode = True
                return self._mock_list_trips(limit)
            return [], f"CLI error: {stderr}"
        
        return DRCLIResponseParser.parse_trip_list(stdout)
    
    async def query_recent_problems(
        self,
        hours: int = 24,
        level: Optional[DRAlertLevel] = None,
    ) -> Tuple[List[DRProblem], Optional[str]]:
        """
        查询最近 N 小时内的问题列表。
        
        实现方式：先查最近行程，再查每个行程的问题。
        TODO: 如果 DR CLI 有直接的时间过滤命令，可优化此处。
        
        Args:
            hours: 时间窗口（小时）
            level: 按级别过滤（可选）
            
        Returns:
            (problems列表, error_message)
        """
        all_problems = []
        
        # 查最近行程
        trips, err = await self.list_trips(limit=hours)
        if err:
            return [], err
        
        for trip in trips:
            problems, _ = await self.query_trip_problems(trip.trip_id)
            for p in problems:
                if level is None or p.level == level:
                    all_problems.append(p)
        
        # 按时间倒序
        all_problems.sort(key=lambda x: x.created_at or "", reverse=True)
        return all_problems, None
    
    # ==================== 格式化输出 ====================
    
    async def format_problems_for_feishu(
        self,
        problems: List[DRProblem],
        title: Optional[str] = None,
    ) -> str:
        """
        将问题列表格式化为飞书消息文本。
        
        Args:
            problems: 问题列表
            title: 可选的消息标题
            
        Returns:
            格式化的 markdown 文本
        """
        if not problems:
            return "✅ 该行程暂无问题记录"
        
        lines = []
        if title:
            lines.append(f"**{title}**\n")
        
        lines.append(f"共 **{len(problems)}** 个问题：\n")
        
        # 按级别分组
        by_level: Dict[DRAlertLevel, List[DRProblem]] = {}
        for p in problems:
            by_level.setdefault(p.level, []).append(p)
        
        for lvl in [DRAlertLevel.CRITICAL, DRAlertLevel.ERROR,
                    DRAlertLevel.WARNING, DRAlertLevel.INFO, DRAlertLevel.UNKNOWN]:
            if lvl not in by_level:
                continue
            for p in by_level[lvl]:
                lines.append(p.summary())
        
        return "\n".join(lines)
    
    # ==================== Mock 模式（CLI 不可用时降级） ====================
    
    def _mock_trip_problems(self, trip_id: str) -> Tuple[List[DRProblem], None]:
        """Mock：返回假数据，用于 CLI 不可用时的开发调试。"""
        logger.info(f"[DR Client] MOCK: query_trip_problems({trip_id})")
        return [
            DRProblem(
                problem_id="MOCK-PRB-001",
                title="[Mock] VCAN总线异常计数超限",
                level=DRAlertLevel.ERROR,
                trip_id=trip_id,
                tag_id="MOCK-TAG-001",
                description="这是 Mock 数据，CLI 不可用时降级使用",
                created_at=datetime.now().isoformat(),
            ),
            DRProblem(
                problem_id="MOCK-PRB-002",
                title="[Mock] 方向盘转角超限",
                level=DRAlertLevel.WARNING,
                trip_id=trip_id,
                tag_id="MOCK-TAG-001",
                created_at=datetime.now().isoformat(),
            ),
        ], None
    
    def _mock_tag_info(self, tag_id: str) -> Tuple[Optional[DRTag], None]:
        """Mock: Tag 信息。"""
        return DRTag(
            tag_id=tag_id,
            vehicle_id="MOCK-VEH-001",
            trip_id="MOCK-TRIP-001",
            created_at=datetime.now().isoformat(),
            notes="Mock data - CLI unavailable",
        ), None
    
    def _mock_list_trips(self, limit: int) -> Tuple[List[DRTrip], None]:
        """Mock: 行程列表。"""
        now = datetime.now()
        return [
            DRTrip(
                trip_id=f"MOCK-TRIP-{i:03d}",
                vehicle_id="MOCK-VEH-001",
                start_time=(now - timedelta(hours=i)).isoformat(),
                end_time=(now - timedelta(hours=i-1)).isoformat(),
                distance_km=10.5 + i,
                problem_count=i % 3,
            )
            for i in range(1, min(limit, 5) + 1)
        ], None


# ============================================================================
# 异常类
# ============================================================================

class DRClientError(Exception):
    """DR Client 通用错误。"""
    def __init__(self, message: str, cli_code: Optional[int] = None):
        self.message = message
        self.cli_code = cli_code
        super().__init__(message)


class DRParseError(DRClientError):
    """DR CLI 返回数据解析失败。"""
    pass


# ============================================================================
# 单例
# ============================================================================

_dr_client: Optional[DRClient] = None


def get_dr_client() -> DRClient:
    """获取单例 DRClient 实例。"""
    global _dr_client
    if _dr_client is None:
        _dr_client = DRClient()
    return _dr_client
