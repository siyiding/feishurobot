"""
Feishu Sheets API Client for Test Case Library.

Manages test cases stored in Feishu Spreadsheets.
"""
import json
import time
from typing import List, Optional, Dict, Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import (
    TestCaseInfo, TestCaseStatus, TestCaseType,
    TestCaseQueryRequest, TestCaseQueryResponse,
    TestCaseUpdateRequest, TestCaseUpdateResponse,
    SceneInfo, SceneCoverageUpdateRequest,
)

logger = get_logger(__name__)


class FeishuSheetClient:
    """
    Client for Feishu Sheets API.
    
    Provides methods for:
    - Querying test cases with filters
    - Updating test case execution status
    - Scene coverage tracking
    """
    
    # Test case library spreadsheet token
    SPREADSHEET_TOKEN = "EU27soF8whsnFmtF6MCc59HqnWh"
    
    # Sheet name and range for test cases
    TEST_CASE_SHEET_NAME = "用例库"
    TEST_CASE_RANGE = "A1:M1000"  # Adjust based on actual column count
    
    # Column mapping (0-indexed)
    COLUMNS = {
        "case_id": 0,          # A: 用例ID
        "case_name": 1,        # B: 用例名称
        "case_type": 2,        # C: 测试类型
        "module": 3,           # D: 功能模块
        "related_requirement": 4,  # E: 关联需求
        "priority": 5,         # F: 优先级
        "status": 6,           # G: 执行状态
        "executor": 7,         # H: 执行人
        "execution_date": 8,   # I: 执行日期
        "related_scene_ids": 9,  # J: 关联场景ID
        "notes": 10,           # K: 备注
        "updater": 11,         # L: 最近更新人
        "updated_at": 12,      # M: 更新时间
    }

    def __init__(self, app_id: str = "", app_secret: str = ""):
        settings = get_settings()
        self.app_id = app_id or settings.feishu_app_id
        self.app_secret = app_secret or settings.feishu_app_secret
        self._token: Optional[str] = None
        self._token_expires_at: int = 0
        self._base_url = "https://open.feishu.cn/open-apis"
        
    async def get_access_token(self) -> str:
        """Get Feishu tenant access token."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        
        import urllib.request
        import urllib.error
        
        url = f"{self._base_url}/auth/v3/tenant_access_token/internal"
        data = json.dumps({
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }).encode('utf-8')
        
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
            
            if result.get("code") == 0:
                self._token = result["tenant_access_token"]
                self._token_expires_at = time.time() + result.get("expire", 7200)
                logger.info("Feishu tenant token refreshed")
                return self._token
            else:
                logger.error(f"Failed to get token: {result}")
                raise Exception(f"Token error: {result.get('msg')}")
        except Exception as e:
            logger.error(f"Token request failed: {e}")
            raise

    async def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        """Make an API request to Feishu Sheets."""
        import urllib.request
        import urllib.error
        
        token = await self.get_access_token()
        url = f"{self._base_url}{path}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        try:
            if data:
                req_data = json.dumps(data).encode('utf-8')
                req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
            else:
                req = urllib.request.Request(url, headers=headers, method=method)
            
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                
            if result.get("code") != 0:
                logger.warning(f"API warning: {result.get('msg')}")
            
            return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else ""
            logger.error(f"HTTP {e.code} error: {error_body}")
            return {"code": e.code, "data": None, "msg": error_body}
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return {"code": -1, "data": None, "msg": str(e)}

    async def get_sheet_meta(self) -> Optional[Dict[str, str]]:
        """Get spreadsheet metadata and sheet IDs."""
        result = await self._request(
            "GET", 
            f"/sheets/v3/spreadsheets/{self.SPREADSHEET_TOKEN}"
        )
        
        if result.get("code") == 0 and result.get("data"):
            sheets = result["data"].get("sheets", [])
            for sheet in sheets:
                if sheet.get("title") == self.TEST_CASE_SHEET_NAME:
                    return {
                        "sheet_id": sheet["sheet_id"],
                        "title": sheet["title"],
                    }
            # Return first sheet if exact name not found
            if sheets:
                return {
                    "sheet_id": sheets[0]["sheet_id"],
                    "title": sheets[0]["title"],
                }
        return None

    async def query_test_cases(self, request: TestCaseQueryRequest) -> TestCaseQueryResponse:
        """
        Query test cases with filters.
        
        Args:
            request: TestCaseQueryRequest with filters
            
        Returns:
            TestCaseQueryResponse with matching test cases
        """
        logger.info(f"Querying test cases: type={request.case_type}, module={request.module}, status={request.status}")
        
        # Get sheet meta first
        sheet_meta = await self.get_sheet_meta()
        if not sheet_meta:
            logger.warning("Could not get sheet meta, using mock data")
            return self._mock_query_test_cases(request)
        
        sheet_id = sheet_meta["sheet_id"]
        
        # Read data from sheet
        range_str = f"{sheet_id}!{self.TEST_CASE_RANGE}"
        result = await self._request(
            "GET",
            f"/sheets/v2/spreadsheets/{self.SPREADSHEET_TOKEN}/values/{range_str}"
        )
        
        if result.get("code") != 0 or not result.get("data"):
            logger.warning(f"Failed to read sheet, using mock data: {result}")
            return self._mock_query_test_cases(request)
        
        values = result.get("data", {}).get("valueRange", {}).get("values", [])
        if not values or len(values) < 2:  # Need header + at least 1 row
            return self._mock_query_test_cases(request)
        
        # Parse header row
        header = values[0]
        data_rows = values[1:]
        
        # Parse test cases
        cases = []
        for row in data_rows:
            if not row or len(row) < 1:
                continue
            
            try:
                case = self._parse_test_case_row(row, header)
                if case:
                    # Apply filters
                    if request.case_type and case.case_type != request.case_type:
                        continue
                    if request.module and case.module != request.module:
                        continue
                    if request.status and case.status != request.status:
                        continue
                    if request.priority and case.priority != request.priority:
                        continue
                    if request.executor and case.executor != request.executor:
                        continue
                    
                    cases.append(case)
            except Exception as e:
                logger.debug(f"Failed to parse row: {e}")
                continue
        
        return TestCaseQueryResponse(
            total=len(cases),
            cases=cases[:request.page_size],
        )

    def _parse_test_case_row(self, row: List[Any], header: List[str]) -> Optional[TestCaseInfo]:
        """Parse a row into TestCaseInfo."""
        def get_val(col_name: str) -> Optional[str]:
            idx = self.COLUMNS.get(col_name)
            if idx is not None and idx < len(row):
                val = row[idx]
                return str(val) if val is not None else None
            return None
        
        case_id = get_val("case_id")
        if not case_id:
            return None
        
        # Parse status
        status_str = get_val("status") or "待执行"
        try:
            status = TestCaseStatus(status_str)
        except ValueError:
            status = TestCaseStatus.PENDING
        
        # Parse type
        type_str = get_val("case_type")
        case_type = None
        if type_str:
            try:
                case_type = TestCaseType(type_str)
            except ValueError:
                pass
        
        return TestCaseInfo(
            case_id=case_id,
            case_name=get_val("case_name") or "",
            case_type=case_type,
            module=get_val("module"),
            related_requirement=get_val("related_requirement"),
            priority=get_val("priority"),
            status=status,
            executor=get_val("executor"),
            execution_date=get_val("execution_date"),
            related_scene_ids=self._parse_list(get_val("related_scene_ids")),
            notes=get_val("notes"),
            updater=get_val("updater"),
            updated_at=get_val("updated_at"),
        )

    def _parse_list(self, val: Optional[str]) -> Optional[List[str]]:
        """Parse comma-separated string into list."""
        if not val:
            return None
        return [s.strip() for s in val.split(",") if s.strip()]

    async def update_test_case(self, request: TestCaseUpdateRequest) -> TestCaseUpdateResponse:
        """
        Update test case execution status.
        
        Args:
            request: TestCaseUpdateRequest with case ID and fields to update
            
        Returns:
            TestCaseUpdateResponse with update result
        """
        logger.info(f"Updating test case: {request.case_id}")
        
        # Get sheet meta
        sheet_meta = await self.get_sheet_meta()
        if not sheet_meta:
            return TestCaseUpdateResponse(
                case_id=request.case_id,
                updated=False,
                message="无法获取表格元数据",
            )
        
        sheet_id = sheet_meta["sheet_id"]
        
        # Find the row for this case_id
        range_str = f"{sheet_id}!A:A"  # Just case_id column first
        result = await self._request(
            "GET",
            f"/sheets/v2/spreadsheets/{self.SPREADSHEET_TOKEN}/values/{range_str}"
        )
        
        if result.get("code") != 0:
            return TestCaseUpdateResponse(
                case_id=request.case_id,
                updated=False,
                message=f"读取失败: {result.get('msg')}",
            )
        
        values = result.get("data", {}).get("valueRange", {}).get("values", [])
        if not values:
            return TestCaseUpdateResponse(
                case_id=request.case_id,
                updated=False,
                message="表格数据为空",
            )
        
        # Find row index (1-indexed, +1 for header)
        row_idx = None
        for idx, row in enumerate(values):
            if row and str(row[0]) == request.case_id:
                row_idx = idx + 1  # 1-indexed
                break
        
        if row_idx is None:
            return TestCaseUpdateResponse(
                case_id=request.case_id,
                updated=False,
                message=f"未找到用例ID: {request.case_id}",
            )
        
        # Build update values
        updates = []
        if request.status is not None:
            col = self._col_letter(self.COLUMNS["status"])
            updates.append({
                "range": f"{sheet_id}!{col}{row_idx}:{col}{row_idx}",
                "values": [[request.status.value]]
            })
        if request.executor is not None:
            col = self._col_letter(self.COLUMNS["executor"])
            updates.append({
                "range": f"{sheet_id}!{col}{row_idx}:{col}{row_idx}",
                "values": [[request.executor]]
            })
        if request.execution_date is not None:
            col = self._col_letter(self.COLUMNS["execution_date"])
            updates.append({
                "range": f"{sheet_id}!{col}{row_idx}:{col}{row_idx}",
                "values": [[request.execution_date]]
            })
        if request.related_scene_ids is not None:
            col = self._col_letter(self.COLUMNS["related_scene_ids"])
            updates.append({
                "range": f"{sheet_id}!{col}{row_idx}:{col}{row_idx}",
                "values": [[",".join(request.related_scene_ids)]]
            })
        if request.notes is not None:
            col = self._col_letter(self.COLUMNS["notes"])
            updates.append({
                "range": f"{sheet_id}!{col}{row_idx}:{col}{row_idx}",
                "values": [[request.notes]]
            })
        
        if not updates:
            return TestCaseUpdateResponse(
                case_id=request.case_id,
                updated=False,
                message="没有需要更新的字段",
            )
        
        # Execute batch update
        update_result = await self._request(
            "PUT",
            f"/sheets/v2/spreadsheets/{self.SPREADSHEET_TOKEN}/values_batch_update",
            {"valueRange": updates}
        )
        
        if update_result.get("code") == 0:
            return TestCaseUpdateResponse(
                case_id=request.case_id,
                updated=True,
                message=f"用例 {request.case_id} 更新成功",
            )
        else:
            return TestCaseUpdateResponse(
                case_id=request.case_id,
                updated=False,
                message=f"更新失败: {update_result.get('msg')}",
            )

    def _col_letter(self, col_idx: int) -> str:
        """Convert 0-indexed column index to Excel-style letter."""
        result = ""
        col_idx += 1  # 1-indexed
        while col_idx > 0:
            col_idx, remainder = divmod(col_idx - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _mock_query_test_cases(self, request: TestCaseQueryRequest) -> TestCaseQueryResponse:
        """Return mock test case data for development/testing."""
        mock_cases = [
            TestCaseInfo(
                case_id="TC-001",
                case_name="CAN总线通信正常",
                case_type=TestCaseType.FUNCTION,
                module="动力系统",
                related_requirement="REQ-POWER-001",
                priority="P0",
                status=TestCaseStatus.PASSED,
                executor="张三",
                execution_date="2026-04-01",
                related_scene_ids=["SC-001"],
                notes="",
                updater="张三",
                updated_at="2026-04-01T10:00:00Z",
            ),
            TestCaseInfo(
                case_id="TC-002",
                case_name="方向盘转向助力正常",
                case_type=TestCaseType.FUNCTION,
                module="底盘系统",
                related_requirement="REQ-CHASSIS-001",
                priority="P0",
                status=TestCaseStatus.PASSED,
                executor="李四",
                execution_date="2026-04-01",
                related_scene_ids=["SC-002"],
                notes="",
                updater="李四",
                updated_at="2026-04-01T11:00:00Z",
            ),
            TestCaseInfo(
                case_id="TC-003",
                case_name="仪表盘显示正常",
                case_type=TestCaseType.SMOKE,
                module="座舱系统",
                related_requirement="REQ-COCKPIT-001",
                priority="P1",
                status=TestCaseStatus.PENDING,
                executor=None,
                execution_date=None,
                related_scene_ids=["SC-003"],
                notes="",
                updater=None,
                updated_at=None,
            ),
            TestCaseInfo(
                case_id="TC-004",
                case_name="ADAS自动紧急制动",
                case_type=TestCaseType.FUNCTION,
                module="ADAS",
                related_requirement="REQ-ADAS-001",
                priority="P0",
                status=TestCaseStatus.BLOCKED,
                executor="王五",
                execution_date="2026-03-30",
                related_scene_ids=["SC-004", "SC-005"],
                notes="等待传感器标定完成",
                updater="王五",
                updated_at="2026-03-30T15:00:00Z",
            ),
            TestCaseInfo(
                case_id="TC-005",
                case_name="高温环境下续航里程",
                case_type=TestCaseType.PERFORMANCE,
                module="续航",
                related_requirement="REQ-ENERGY-001",
                priority="P2",
                status=TestCaseStatus.FAILED,
                executor="赵六",
                execution_date="2026-03-29",
                related_scene_ids=["SC-006"],
                notes="高温环境下续航里程低于预期15%",
                updater="赵六",
                updated_at="2026-03-29T18:00:00Z",
            ),
        ]
        
        # Apply filters
        filtered = mock_cases
        if request.case_type:
            filtered = [c for c in filtered if c.case_type == request.case_type]
        if request.module:
            filtered = [c for c in filtered if c.module == request.module]
        if request.status:
            filtered = [c for c in filtered if c.status == request.status]
        if request.priority:
            filtered = [c for c in filtered if c.priority == request.priority]
        
        return TestCaseQueryResponse(
            total=len(filtered),
            cases=filtered[:request.page_size],
        )

    def format_test_case_list(self, response: TestCaseQueryResponse) -> str:
        """Format test case list for Feishu message."""
        if response.total == 0:
            return "没有找到符合条件的用例。"

        lines = [f"共找到 **{response.total}** 个用例：\n"]
        
        status_emoji = {
            TestCaseStatus.PENDING: "⏳",
            TestCaseStatus.PASSED: "✅",
            TestCaseStatus.FAILED: "❌",
            TestCaseStatus.BLOCKED: "🚫",
            TestCaseStatus.SKIPPED: "⏭️",
        }
        
        for case in response.cases:
            emoji = status_emoji.get(case.status, "❓")
            type_str = case.case_type.value if case.case_type else "未分类"
            
            lines.append(
                f"{emoji} **{case.case_name}**\n"
                f"   ID: `{case.case_id}` | 类型: {type_str} | 模块: {case.module or '未分类'}\n"
                f"   状态: {case.status.value} | 优先级: {case.priority or '未设置'}\n"
                f"   执行人: {case.executor or '未执行'} | 执行日期: {case.execution_date or '未执行'}\n"
            )

        return "\n".join(lines)


# Singleton instance
_sheet_client: Optional[FeishuSheetClient] = None


def get_sheet_client() -> FeishuSheetClient:
    """Get singleton FeishuSheetClient instance."""
    global _sheet_client
    if _sheet_client is None:
        _sheet_client = FeishuSheetClient()
    return _sheet_client
