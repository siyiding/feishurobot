# M6 开发进度报告

> 版本：v0.1 | 作者：Kiro💻 | 日期：2026-04-02
> 状态：**开发完成** ✅

---

## 1. 任务概述

M6 模块8：变更感知+回归助手（10-13人天）

### 1.1 任务分解

| 子任务 | 人天 | 状态 | 说明 |
|--------|------|------|------|
| 8.1 Git仓接入 | 3天 | ✅ | GitHub/GitLab API + Webhook |
| 8.2 变更-用例匹配 | 3天 | ✅ | 基于文件路径匹配模块→用例 |
| 8.3 回归建议生成 | 2天 | ✅ | 格式化为Markdown + 推送 |
| 8.4 OTA变更感知 | 2天 | ✅ | 解析OTA关键词 + 匹配用例 |
| 8.5 用例失败→DR关联 | 3天 | ✅ | 框架就绪（等DR CLI格式） |

---

## 2. 已完成内容

### 2.1 Git仓接入（8.1）✅

**新增文件**：`app/services/git_service.py`

**核心类**：`GitService`

**功能**：
- GitHub API 集成（PR/MR 查询）
- GitLab API 集成（MR 查询）
- Webhook 签名验证（GitHub HMAC-SHA256、GitLab Token）
- MR 事件处理（opened、synchronize、closed、reopened）
- 仓库注册与管理

**API端点**：
- `POST /webhook/git/github` - GitHub Webhook 接收器
- `POST /webhook/git/gitlab` - GitLab Webhook 接收器

**配置示例**：
```python
git_service.register_repository(
    repo_id="vehicle-core",
    name="vehicle-core",
    full_name="company/vehicle-core",  # owner/repo
    provider=GitProvider.GITHUB,
    api_token="ghp_xxx",  # GitHub Personal Access Token
    webhook_secret="webhook_secret",
)
```

---

### 2.2 变更-用例匹配（8.2）✅

**新增文件**：`app/services/change_awareness_service.py`

**核心类**：`ChangeCaseMatcher`

**匹配算法**：
1. 从变更文件路径提取模块（基于关键词 + 正则）
2. 使用 `ModuleMapping` 匹配文件 → 模块 → 用例
3. 支持多模块匹配

**模块关键词映射**：
| 模块 | 关键词 |
|------|--------|
| ICC-AEB | aeb, emergency_braking, 紧急制动 |
| ICC-LCC | lcc, lane_centering, 车道居中 |
| ICC-ACC | acc, adaptive_cruise, 自适应巡航 |
| ICC-FCW | fcw, forward_collision, 前向碰撞预警 |
| ICC-LDW | ldw, lane_departure, 车道偏离 |
| ICC-APA | apa, parking_assist, 泊车辅助 |
| ADAS | adas, advanced_driver |
| PERCEPTION | perception, 感知, radar, lidar, camera |
| PLANNING | planning, 规划, path |
| CONTROL | control, 控制, vehicle |

**模块映射注册**：
```python
git_service.register_module_mapping(
    module_name="ICC-AEB",
    file_patterns=[r".*aeb.*", r".*emergency_braking.*"],
    related_requirements=["REQ-AEB-001"],
    test_case_ids=["TC-AEB-001", "TC-AEB-002"],
)
```

**API端点**：
- `POST /webhook/change/match` - 手动触发变更-用例匹配

---

### 2.3 回归建议生成（8.3）✅

**核心类**：`RegressionSuggestionGenerator`

**功能**：
- 根据MR信息生成回归建议
- 优先级自动判定（P0/P1/P2）
- 格式化为Feishu友好的Markdown
- 推送至测试工程师

**优先级判定规则**：
| 条件 | 优先级 |
|------|--------|
| 标题含 [P0] | P0 |
| 标题含 [P1] | P1 |
| 标题含 hotfix / critical | P0 |
| 标题含 bugfix / fix | P1 |
| 其他 | P2 |

**推送集成**：
- P0 → `PushService.enqueue_p0_alert()` （无限制）
- P1/P2 → `PushService.enqueue_p1_notification()` （批量）

**Markdown格式**：
```markdown
## 📋 回归测试建议

**MR**: [title](url)
**优先级**: 🔴 P0
**原因**: MR modifies N files in M modules

### 🔧 变更模块
- ICC-AEB
- ICC-LCC

### ✅ 建议执行的用例（共 N 个）
| 用例ID | 用例名称 | 模块 | 优先级 | 状态 |
|------|---------|------|--------|------|
| TC-001 | AEB测试 | ICC-AEB | P1 | 待执行 |
```

**API端点**：
- `POST /webhook/regression/push` - 推送回归建议
- `GET /webhook/regression/suggestions` - 列出回归建议

---

### 2.4 OTA变更感知（8.4）✅

**核心类**：`OTAChangeAnalyzer`

**功能**：
- 解析OTA变更描述
- 提取变更类型（feature/bugfix/improvement/security）
- 提取关键词和受影响模块
- 匹配相关测试用例
- 推送通知

**变更类型关键词**：
| 类型 | 关键词 |
|------|--------|
| feature | 新增, 新功能, 添加, 功能升级 |
| bugfix | 修复, bugfix, fix, 问题修复 |
| improvement | 优化, 改进, enhance, improve |
| security | 安全, security, 加密, 认证 |

**API端点**：
- `POST /webhook/ota` - 接收OTA变更通知
- `GET /webhook/ota/analyze` - 手动分析OTA变更

**Webhook Payload 示例**：
```json
{
  "version": "v2.1.0",
  "title": "AEB功能升级",
  "description": "新增紧急制动功能，修复感知模块bug",
  "change_type": "feature",
  "released_at": "2026-04-02T10:00:00Z"
}
```

---

### 2.5 用例失败→DR关联（8.5）✅

**核心类**：`TestCaseDRAssociator`

**功能**：
- 记录测试用例失败
- 查询最近24小时内的DR行程
- 关联失败与相关DR数据
- 推送关联报告

**注意**：DR行程查询依赖DR CLI格式确认，当前为框架占位实现。

**数据结构**：
```python
@dataclass
class TestCaseFailure:
    case_id: str
    case_name: str
    module: str
    failure_time: str
    failure_reason: Optional[str]
    executor: Optional[str]
    related_dr_trips: List[str]  # DR trip IDs

@dataclass  
class FailureDRAssociation:
    failure: TestCaseFailure
    dr_trips: List[DRTripInfo]
    associated_at: str
    confidence: float  # 0.0-0.9
```

**API端点**：
- `POST /webhook/testcase/failure` - 接收用例失败通知
- `POST /failure/associate-dr` - 手动关联失败与DR数据
- `GET /failure/associate-dr` - 查询失败-DR关联

---

## 3. 代码变更摘要

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `app/models/schemas.py` | 修改 | 新增M6相关Schema |
| `app/services/git_service.py` | 新增 | Git仓库集成服务（600+行） |
| `app/services/change_awareness_service.py` | 新增 | 变更感知核心服务（800+行） |
| `app/api/change_awareness.py` | 新增 | M6 API端点（500+行） |
| `app/services/feishu_sheet_client.py` | 修改 | 新增query_cases_by_module方法 |
| `main.py` | 修改 | 注册change_awareness路由 |
| `tests/test_m6_change_awareness.py` | 新增 | M6单元测试（24个） |

---

## 4. 新增API端点

### Git Webhook（8.1）
| 端点 | 方法 | 功能 |
|------|------|------|
| `/webhook/git/github` | POST | GitHub Webhook接收 |
| `/webhook/git/gitlab` | POST | GitLab Webhook接收 |

### 变更匹配（8.2）
| 端点 | 方法 | 功能 |
|------|------|------|
| `/webhook/change/match` | POST | 手动触发变更-用例匹配 |

### 回归建议（8.3）
| 端点 | 方法 | 功能 |
|------|------|------|
| `/webhook/regression/push` | POST | 推送回归建议 |
| `/webhook/regression/suggestions` | GET | 列出回归建议 |

### OTA感知（8.4）
| 端点 | 方法 | 功能 |
|------|------|------|
| `/webhook/ota` | POST | 接收OTA变更通知 |
| `/webhook/ota/analyze` | GET | 分析OTA变更 |

### DR关联（8.5）
| 端点 | 方法 | 功能 |
|------|------|------|
| `/webhook/testcase/failure` | POST | 接收用例失败 |
| `/failure/associate-dr` | GET/POST | 关联失败与DR |

---

## 5. 单元测试结果

```
24 passed in 0.32s
```

**测试覆盖**：
- Git服务：6个测试
- 模块映射：4个测试
- 变更匹配：2个测试
- OTA分析：5个测试
- 回归建议：3个测试
- DR关联：2个测试
- 意图识别：2个测试

---

## 6. 架构约束遵守情况

| 约束 | 实现 | 状态 |
|------|------|------|
| Git变更→匹配用例→推荐回归→推送 | Webhook → Matcher → Generator → PushService | ✅ |
| 用例-模块映射表 | ModuleMapping + Feishu Sheet | ✅ |
| P0快速通道 | PushService.enqueue_p0_alert() | ✅ |
| P1批量推送 | PushService.enqueue_p1_notification() | ✅ |
| DR CLI框架 | TestCaseDRAssociator（占位） | ✅ |

---

## 7. 验收标准达成情况

### M6准出标准

| 标准 | 实现 | 状态 |
|------|------|------|
| GitHub/GitLab Webhook接收 | `/webhook/git/github`, `/webhook/git/gitlab` | ✅ |
| MR变更→受影响用例匹配 | `ChangeCaseMatcher.match_mr_to_test_cases()` | ✅ |
| 回归测试建议生成 | `RegressionSuggestionGenerator` | ✅ |
| OTA变更解析+用例匹配 | `OTAChangeAnalyzer.analyze_ota_change()` | ✅ |
| 用例失败→DR关联框架 | `TestCaseDRAssociator`（框架就绪） | ✅ |
| 推送测试工程师 | `push_suggestion_to_engineer()` | ✅ |
| 单元测试覆盖 | 24个测试 | ✅ |

---

## 8. 下一步

### M6完成后的工作
- [ ] Hale 验收 M6 功能
- [ ] Echo 进行集成测试
- [ ] **DR平台确认CLI返回格式**（8.5细节实现）
- [ ] 配置GitLab/GitHub Webhook指向机器人服务
- [ ] 配置cron job定期检查新MR（如果不用webhook）

### Webhook配置示例

**GitHub**：
1. Settings → Webhooks → Add webhook
2. Payload URL: `https://your-domain.com/webhook/git/github`
3. Content type: `application/json`
4. Secret: `your-webhook-secret`
5. Events: Pull requests

**GitLab**：
1. Settings → Webhooks
2. URL: `https://your-domain.com/webhook/git/gitlab`
3. Secret token: `your-webhook-secret`
4. Triggers: Merge request events

### 环境变量配置
```bash
# Git Integration
GITHUB_TOKEN=ghp_xxx  # GitHub Personal Access Token
GITLAB_TOKEN=glpat_xxx  # GitLab Personal Access Token

# Webhook secrets (same as configured in GitHub/GitLab)
GITHUB_WEBHOOK_SECRET=xxx
GITLAB_WEBHOOK_SECRET=xxx
```

---

## 9. 技术亮点

1. **智能文件路径解析**：基于关键词和正则匹配变更文件到模块
2. **多源MR数据整合**：GitHub/GitLab API统一抽象
3. **优先级自动判定**：基于MR标题关键词自动推断回归优先级
4. **Feishu原生Markdown**：生成的报告直接适配飞书展示
5. **OTA变更关键词提取**：支持feature/bugfix/improvement/security分类
6. **DR关联框架**：预留DR平台对接，格式确认后可快速实现

---

## 10. 待优化项

1. **DR CLI集成**：等待DR平台确认CLI返回格式
2. **变更影响范围估算**：根据代码行数/文件数估算影响范围
3. **历史MR学习**：根据历史回归结果优化用例匹配
4. **用例执行历史**：关联用例历史通过率到建议优先级
5. **测试工程师分配**：根据模块自动分配负责的测试工程师

---

## 11. 依赖项

| 依赖 | 版本 | 说明 |
|------|------|------|
| fastapi | ≥0.100 | Web框架 |
| httpx | ≥0.24 | 异步HTTP客户端 |
| pydantic | ≥2.0 | 数据验证 |
| redis | ≥4.5 | 推送队列（可选） |

---

*报告更新时间: 2026-04-02*
