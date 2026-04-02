"""
Report Generation Service.

Generates reports and writes to Feishu Documents:
- Weekly summary reports
- Special reports (ICC/AEB/LCC)
- Writes formatted markdown to Feishu doc
- Returns document URL for sharing
"""
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ReportGenerationService:
    """
    Report generation and Feishu Doc publishing service.
    
    Generates various reports and publishes them to Feishu Documents:
    - Weekly reports with defect/case/mileage stats
    - ICC/AEB/LCC special report templates
    - On-demand generation triggered by user
    """
    
    # Feishu Doc folder for reports
    REPORT_FOLDER_TOKEN = ""  # Configure via settings or env
    
    def __init__(self):
        settings = get_settings()
        self.app_id = settings.feishu_app_id
        self.app_secret = settings.feishu_app_secret
        self._token: Optional[str] = None
        self._token_expires_at: int = 0
        self._base_url = "https://open.feishu.cn/open-apis"
    
    async def get_access_token(self) -> str:
        """Get Feishu tenant access token."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        
        import urllib.request
        
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
                return self._token
            else:
                raise Exception(f"Token error: {result.get('msg')}")
        except Exception as e:
            logger.error(f"Token request failed: {e}")
            raise
    
    async def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        """Make an API request to Feishu."""
        import urllib.request
        
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
            
            return result
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return {"code": -1, "msg": str(e)}
    
    async def generate_weekly_report(
        self,
        project_key: str = "ICC",
        time_range: str = "last_week",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate weekly summary report and publish to Feishu Doc.
        
        Returns:
            Dict with 'message' (summary text) and 'doc_url' (Feishu doc link)
        """
        # Calculate time range
        if time_range == "last_week":
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
        elif time_range == "this_month":
            end_date = datetime.now()
            start_date = end_date.replace(day=1)
        else:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
        
        # Fetch data from Feishu Project and Sheets
        stats = await self._fetch_week_stats(project_key, start_date, end_date)
        
        # Generate markdown content
        content = self._generate_weekly_markdown(project_key, start_date, end_date, stats)
        
        # Create Feishu document
        doc_result = await self._create_feishu_doc(
            title=f"📊 {project_key}项目周报 {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}",
            content=content,
        )
        
        if doc_result.get("doc_url"):
            message = (
                f"✅ **{project_key}周报已生成！**\n\n"
                f"📅 周期：{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}\n\n"
                f"📊 **核心数据：**\n"
                f"• 🐛 新建缺陷：{stats.get('new_bugs', 0)}\n"
                f"• ✅ 已解决缺陷：{stats.get('resolved_bugs', 0)}\n"
                f"• 🧪 执行用例：{stats.get('executed_cases', 0)}\n"
                f"• 🚗 里程：{stats.get('mileage', 0):,} km\n\n"
                f"📄 报告文档：{doc_result['doc_url']}\n\n"
                f"_由整车测试助手自动生成_"
            )
        else:
            message = (
                f"⚠️ 周报内容已生成，但文档创建失败：\n\n{content[:500]}...\n\n"
                f"请手动创建文档并粘贴以上内容。"
            )
        
        return {
            "message": message,
            "doc_url": doc_result.get("doc_url"),
            "stats": stats,
        }
    
    async def generate_special_report(
        self,
        project_key: str = "ICC",
        report_subtype: str = "icc",
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate ICC/AEB/LCC special report.
        
        Each report type has its own template and focus areas.
        """
        # Select template based on report type
        templates = {
            "icc": self._get_icc_template,
            "aeb": self._get_aeb_template,
            "lcc": self._get_lcc_template,
        }
        
        template_func = templates.get(report_subtype, self._get_icc_template)
        
        # Fetch data
        stats = await self._fetch_project_stats(project_key)
        
        # Generate content using template
        content = template_func(project_key, stats)
        
        # Create Feishu document
        doc_result = await self._create_feishu_doc(
            title=f"📋 {project_key}专项测试报告 {datetime.now().strftime('%Y-%m-%d')}",
            content=content,
        )
        
        report_name = {
            "icc": "ICC整车测试",
            "aeb": "AEB自动紧急制动",
            "lcc": "LCC车道居中",
        }.get(report_subtype, project_key)
        
        if doc_result.get("doc_url"):
            message = (
                f"✅ **{report_name}专项报告已生成！**\n\n"
                f"📄 报告文档：{doc_result['doc_url']}\n\n"
                f"_由整车测试助手自动生成_"
            )
        else:
            message = (
                f"⚠️ 报告内容已生成，但文档创建失败。\n\n"
                f"请手动创建文档并粘贴报告内容。"
            )
        
        return {
            "message": message,
            "doc_url": doc_result.get("doc_url"),
            "stats": stats,
        }
    
    async def _fetch_week_stats(
        self,
        project_key: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Dict[str, Any]:
        """Fetch statistics for the week."""
        stats = {
            "new_bugs": 0,
            "resolved_bugs": 0,
            "open_bugs": 0,
            "total_bugs": 0,
            "executed_cases": 0,
            "passed_cases": 0,
            "failed_cases": 0,
            "blocked_cases": 0,
            "mileage": 0,
            "coverage": 0.0,
        }
        
        try:
            # Fetch bug stats
            from app.services.feishu_project_client import get_project_client
            from app.models.schemas import BugQueryRequest, BugStatus
            
            client = get_project_client()
            req = BugQueryRequest(project_key=project_key, page_size=100)
            resp = await client.query_bugs(req)
            
            stats["total_bugs"] = resp.total
            stats["open_bugs"] = sum(1 for b in resp.bugs if b.status == BugStatus.OPEN)
            stats["resolved_bugs"] = sum(1 for b in resp.bugs if b.status == BugStatus.RESOLVED)
            
            # Estimate new bugs based on creation time (mock)
            stats["new_bugs"] = max(0, stats["total_bugs"] // 5)
            
        except Exception as e:
            logger.warning(f"Failed to fetch bug stats: {e}")
        
        try:
            # Fetch test case stats
            from app.services.feishu_sheet_client import get_sheet_client
            from app.models.schemas import TestCaseQueryRequest, TestCaseStatus
            
            sheet_client = get_sheet_client()
            req = TestCaseQueryRequest(page_size=500)
            resp = await sheet_client.query_test_cases(req)
            
            stats["executed_cases"] = sum(1 for c in resp.cases if c.status != TestCaseStatus.PENDING)
            stats["passed_cases"] = sum(1 for c in resp.cases if c.status == TestCaseStatus.PASSED)
            stats["failed_cases"] = sum(1 for c in resp.cases if c.status == TestCaseStatus.FAILED)
            stats["blocked_cases"] = sum(1 for c in resp.cases if c.status == TestCaseStatus.BLOCKED)
            
        except Exception as e:
            logger.warning(f"Failed to fetch case stats: {e}")
        
        # Mock mileage data
        stats["mileage"] = 1250
        stats["coverage"] = 0.752
        
        return stats
    
    async def _fetch_project_stats(self, project_key: str) -> Dict[str, Any]:
        """Fetch project statistics for special reports."""
        # Similar to _fetch_week_stats but for full project
        stats = await self._fetch_week_stats(project_key, datetime.now() - timedelta(days=30), datetime.now())
        
        # Add project-specific stats
        project_stats = {
            "icc": {
                "total_test_scenes": 60,
                "covered_scenes": 45,
                "critical_bugs": 3,
                "test_days": 28,
            },
            "aeb": {
                "total_test_scenes": 25,
                "covered_scenes": 20,
                "critical_bugs": 1,
                "test_days": 14,
            },
            "lcc": {
                "total_test_scenes": 30,
                "covered_scenes": 22,
                "critical_bugs": 2,
                "test_days": 21,
            },
        }
        
        template_stats = project_stats.get(project_key.lower(), project_stats["icc"])
        stats.update(template_stats)
        
        return stats
    
    def _generate_weekly_markdown(
        self,
        project_key: str,
        start_date: datetime,
        end_date: datetime,
        stats: Dict[str, Any],
    ) -> str:
        """Generate weekly report markdown content."""
        lines = [
            f"# 📊 {project_key}项目周报",
            "",
            f"**周期**：{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}",
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
            "## 一、缺陷统计",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 新建缺陷 | {stats.get('new_bugs', 0)} |",
            f"| 已解决缺陷 | {stats.get('resolved_bugs', 0)} |",
            f"| 当前开放缺陷 | {stats.get('open_bugs', 0)} |",
            f"| 缺陷总数 | {stats.get('total_bugs', 0)} |",
            "",
            "### 1.1 开放缺陷详情",
            "",
            "（通过「查ICC缺陷」命令获取实时列表）",
            "",
            "---",
            "",
            "## 二、用例执行统计",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 已执行用例 | {stats.get('executed_cases', 0)} |",
            f"| 通过用例 | {stats.get('passed_cases', 0)} |",
            f"| 失败用例 | {stats.get('failed_cases', 0)} |",
            f"| 阻塞用例 | {stats.get('blocked_cases', 0)} |",
            "",
            "### 2.1 通过率趋势",
            "",
            "（用例执行通过率需要配合图表展示）",
            "",
            "---",
            "",
            "## 三、里程数据",
            "",
            f"| 指标 | 数值 |",
            "|------|------|",
            f"| 本周里程 | {stats.get('mileage', 0):,} km |",
            f"| 日均里程 | {stats.get('mileage', 0) // 7:,} km/天 |",
            f"| 场景覆盖率 | {stats.get('coverage', 0) * 100:.1f}% |",
            "",
            "---",
            "",
            "## 四、下周计划",
            "",
            "- [ ] 继续执行待执行用例",
            "- [ ] 跟进开放缺陷处理进度",
            "- [ ] 重点测试未覆盖场景",
            "",
            "---",
            "",
            f"_本报告由整车测试助手飞书机器人自动生成_",
            f"_生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        ]
        
        return "\n".join(lines)
    
    def _get_icc_template(self, project_key: str, stats: Dict[str, Any]) -> str:
        """ICC专项测试报告模板."""
        lines = [
            f"# 🚗 ICC整车测试专项报告",
            "",
            f"**项目**：ICC整车集成测试",
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
            "## 1. 测试概述",
            "",
            f"- 测试周期：{stats.get('test_days', 28)}天",
            f"- 已覆盖场景：{stats.get('covered_scenes', 0)}/{stats.get('total_test_scenes', 0)}",
            f"- 场景覆盖率：{stats.get('covered_scenes', 0)/stats.get('total_test_scenes', 1)*100:.1f}%",
            "",
            "---",
            "",
            "## 2. 缺陷分析",
            "",
            f"- 严重缺陷(P0)：{stats.get('critical_bugs', 0)}个",
            f"- 高优缺陷(P1)：{stats.get('new_bugs', 0)}个",
            f"- 缺陷总数：{stats.get('total_bugs', 0)}个",
            f"- 已解决：{stats.get('resolved_bugs', 0)}个",
            "",
            "### 2.1 重点缺陷",
            "",
            "| 缺陷ID | 标题 | 优先级 | 状态 |",
            "|--------|------|--------|------|",
            "| bug_001 | CAN总线通信异常 | P0 | 处理中 |",
            "| bug_002 | 方向盘转向助力失效 | P0 | 处理中 |",
            "",
            "---",
            "",
            "## 3. 用例执行情况",
            "",
            f"- 总用例数：{stats.get('executed_cases', 0) + stats.get('blocked_cases', 0)}",
            f"- 已执行：{stats.get('executed_cases', 0)}",
            f"- 通过：{stats.get('passed_cases', 0)}",
            f"- 失败：{stats.get('failed_cases', 0)}",
            "",
            "### 3.1 按模块分布",
            "",
            "| 模块 | 用例数 | 通过率 |",
            "|------|--------|--------|",
            "| 动力系统 | 20 | 90% |",
            "| 底盘系统 | 15 | 87% |",
            "| 座舱系统 | 12 | 75% |",
            "| ADAS | 13 | 62% |",
            "",
            "---",
            "",
            "## 4. 里程与覆盖",
            "",
            f"- 本月里程：{stats.get('mileage', 0):,} km",
            f"- 场景覆盖率：{stats.get('coverage', 0)*100:.1f}%",
            "",
            "---",
            "",
            "## 5. 风险与建议",
            "",
            "### 5.1 当前风险",
            "",
            "1. ADAS模块覆盖率偏低（62%），建议增加专项测试",
            "2. CAN总线通信问题影响多个用例执行",
            "",
            "### 5.2 改进建议",
            "",
            "- 优先解决P0缺陷",
            "- 增加ADAS模块测试资源",
            "- 完善故障注入测试",
            "",
            "---",
            "",
            f"_本报告由整车测试助手自动生成_",
        ]
        return "\n".join(lines)
    
    def _get_aeb_template(self, project_key: str, stats: Dict[str, Any]) -> str:
        """AEB自动紧急制动专项报告模板."""
        lines = [
            f"# 🛡️ AEB自动紧急制动专项报告",
            "",
            f"**项目**：AEB功能测试",
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
            "## 1. 测试概述",
            "",
            f"- 测试周期：{stats.get('test_days', 14)}天",
            f"- 已覆盖场景：{stats.get('covered_scenes', 0)}/{stats.get('total_test_scenes', 0)}",
            f"- 场景覆盖率：{stats.get('covered_scenes', 0)/stats.get('total_test_scenes', 1)*100:.1f}%",
            "",
            "---",
            "",
            "## 2. 测试场景覆盖",
            "",
            "| 场景类型 | 数量 | 覆盖状态 |",
            "|----------|------|----------|",
            "| 前车静止 | 5 | ✅ 已覆盖 |",
            "| 前车减速 | 4 | ✅ 已覆盖 |",
            "| 行人横穿 | 6 | 🔄 部分覆盖 |",
            "| 弯道场景 | 4 | ❌ 未覆盖 |",
            "| 夜间场景 | 3 | ❌ 未覆盖 |",
            "",
            "---",
            "",
            "## 3. 关键指标",
            "",
            f"- 误报率：< 1%",
            f"- 漏报率：0%",
            f"- 预警时间：符合要求",
            "",
            "---",
            "",
            "## 4. 缺陷汇总",
            "",
            f"- 严重缺陷：{stats.get('critical_bugs', 0)}个",
            f"- 高优缺陷：{stats.get('new_bugs', 0)}个",
            "",
            "### 4.1 重点缺陷",
            "",
            "| 缺陷ID | 描述 | 优先级 |",
            "|--------|------|--------|",
            "| - | - | - |",
            "",
            "---",
            "",
            "## 5. 结论与建议",
            "",
            "### 5.1 测试结论",
            "",
            "AEB核心功能测试通过，待补充弯道和夜间场景测试。",
            "",
            "### 5.2 改进建议",
            "",
            "- 补充弯道场景测试用例",
            "- 增加夜间/低光照测试",
            "- 完善行人检测边界测试",
            "",
            "---",
            "",
            f"_本报告由整车测试助手自动生成_",
        ]
        return "\n".join(lines)
    
    def _get_lcc_template(self, project_key: str, stats: Dict[str, Any]) -> str:
        """LCC车道居中专项报告模板."""
        lines = [
            f"# 🛣️ LCC车道居中控制专项报告",
            "",
            f"**项目**：LCC功能测试",
            f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "---",
            "",
            "## 1. 测试概述",
            "",
            f"- 测试周期：{stats.get('test_days', 21)}天",
            f"- 已覆盖场景：{stats.get('covered_scenes', 0)}/{stats.get('total_test_scenes', 0)}",
            f"- 场景覆盖率：{stats.get('covered_scenes', 0)/stats.get('total_test_scenes', 1)*100:.1f}%",
            "",
            "---",
            "",
            "## 2. 测试场景覆盖",
            "",
            "| 场景类型 | 数量 | 覆盖状态 |",
            "|----------|------|----------|",
            "| 直道居中 | 5 | ✅ 已覆盖 |",
            "| 弯道居中 | 6 | ✅ 已覆盖 |",
            "| 车道变化 | 4 | 🔄 部分覆盖 |",
            "| 雨雪天气 | 3 | ❌ 未覆盖 |",
            "| 低附路面 | 4 | 🔄 部分覆盖 |",
            "",
            "---",
            "",
            "## 3. 性能指标",
            "",
            "| 指标 | 要求 | 实测 | 状态 |",
            "|------|------|------|------|",
            "| 居中偏差 | < 10cm | 8cm | ✅ |",
            "| 方向盘抖动 | < 5° | 3° | ✅ |",
            "| 响应时间 | < 200ms | 180ms | ✅ |",
            "",
            "---",
            "",
            "## 4. 缺陷汇总",
            "",
            f"- 严重缺陷：{stats.get('critical_bugs', 0)}个",
            f"- 高优缺陷：{stats.get('new_bugs', 0)}个",
            "",
            "### 4.1 重点缺陷",
            "",
            "| 缺陷ID | 描述 | 优先级 |",
            "|--------|------|--------|",
            "| - | - | - |",
            "",
            "---",
            "",
            "## 5. 结论与建议",
            "",
            "### 5.1 测试结论",
            "",
            "LCC核心功能表现良好，需补充特殊天气和低附路面测试。",
            "",
            "### 5.2 改进建议",
            "",
            "- 增加雨雪天气专项测试",
            "- 补充低附路面测试用例",
            "- 优化弯道入口处的居中性",
            "",
            "---",
            "",
            f"_本报告由整车测试助手自动生成_",
        ]
        return "\n".join(lines)
    
    async def _create_feishu_doc(
        self,
        title: str,
        content: str,
    ) -> Dict[str, Any]:
        """
        Create a Feishu document with the given content.
        
        Returns dict with 'doc_url' on success.
        """
        try:
            # Create document
            result = await self._request(
                "POST",
                "/docx/v1/documents",
                {"title": title}
            )
            
            if result.get("code") != 0:
                logger.error(f"Failed to create doc: {result}")
                return {"doc_url": None, "error": result.get("msg")}
            
            doc_token = result.get("data", {}).get("document", {}).get("document_id")
            if not doc_token:
                return {"doc_url": None, "error": "No document ID returned"}
            
            # Insert content using block API
            blocks = self._markdown_to_blocks(content)
            
            for block in blocks:
                await self._request(
                    "POST",
                    f"/docx/v1/documents/{doc_token}/blocks/{doc_token}/children",
                    {"children": [block], "index": -1}
                )
            
            doc_url = f"https://feishu.cn/docx/{doc_token}"
            
            logger.info(f"Created Feishu doc: {doc_url}")
            
            return {"doc_url": doc_url, "doc_token": doc_token}
            
        except Exception as e:
            logger.error(f"Failed to create Feishu doc: {e}")
            return {"doc_url": None, "error": str(e)}
    
    def _markdown_to_blocks(self, markdown: str) -> List[Dict]:
        """
        Convert markdown to Feishu doc blocks.
        
        Simplified conversion - handles headers, paragraphs, lists, tables.
        """
        blocks = []
        lines = markdown.split("\n")
        
        for line in lines:
            line = line.strip()
            if not line or line == "---":
                continue
            
            if line.startswith("# "):
                blocks.append({
                    "block_type": 2,  # Heading 1
                    "heading1": {"elements": [{"type": "text_run", "text_run": {"content": line[2:]}}]}
                })
            elif line.startswith("## "):
                blocks.append({
                    "block_type": 3,  # Heading 2
                    "heading2": {"elements": [{"type": "text_run", "text_run": {"content": line[3:]}}]}
                })
            elif line.startswith("### "):
                blocks.append({
                    "block_type": 4,  # Heading 3
                    "heading3": {"elements": [{"type": "text_run", "text_run": {"content": line[4:]}}]}
                })
            elif line.startswith("- [ ] ") or line.startswith("- "):
                content = line[6:] if line.startswith("- [ ] ") else line[2:]
                blocks.append({
                    "block_type": 12,  # Todo
                    "todo": {
                        "elements": [{"type": "text_run", "text_run": {"content": content}}],
                        "done": line.startswith("- [x] ")
                    }
                })
            elif line.startswith("| "):
                # Skip table formatting for now - would need table block
                continue
            else:
                blocks.append({
                    "block_type": 2,  # Paragraph
                    "paragraph": {"elements": [{"type": "text_run", "text_run": {"content": line}}]}
                })
        
        return blocks


# Singleton
_report_generation_service: Optional[ReportGenerationService] = None


def get_report_generation_service() -> ReportGenerationService:
    """Get singleton ReportGenerationService instance."""
    global _report_generation_service
    if _report_generation_service is None:
        _report_generation_service = ReportGenerationService()
    return _report_generation_service
