# NASPilot Product Principles (Permanent)

## 1. Mission

NASPilot 的永久使命：

1. NAS Monitoring
2. NAS Automation
3. NAS Operations

所有功能必须直接服务至少一个使命维度；无法映射者禁止立项。

## 2. 核心业务域

NASPilot 永久核心域：

1. System
2. Container
3. Task
4. File

说明：
- AI Assistant 是能力增强层，不是替代核心域的独立产品。
- 新能力必须声明其服务的核心域，不允许域外漂移。

## 3. 核心产品目标（30 秒运维判断）

用户打开 NASPilot 后 30 秒内必须知道：

1. NAS 是否正常
2. Docker 是否正常
3. Task 是否正常
4. File 是否正常
5. 最近发生了什么
6. 哪些任务失败
7. 哪些容器异常
8. 下一步需要处理什么

判定标准：
- 以上 8 个问题必须在 Dashboard + 相关域页形成闭环答案。
- 任何重构不得降低该能力。

## 4. 强制原则

## Principle 1: Status First
优先展示状态。
- 每个域首页必须先给出当前状态（正常/警告/异常/不可用）。
- 状态展示必须可追溯到具体证据（日志、时间戳、来源）。

## Principle 2: Result First
优先展示执行结果。
- 自动化与任务相关能力必须先展示执行结果而非配置表单。
- 结果至少包含成功/失败/跳过/超时等核心状态与计数。

## Principle 3: Risk First
优先展示失败和异常。
- 异常必须高于常态信息展示。
- 必须提供“失败原因 + 处置建议 + 关联入口”。

## Principle 4: File Is Core Domain
File 属于核心业务域。
- 文件上传、清理、归档、变更必须是一等公民能力。
- 禁止将 File 降级为附属工具入口。

## Principle 5: Container Is Core Domain
Container 属于核心业务域。
- 容器状态、异常、生命周期动作必须被持续优化。
- 禁止将 Container 仅作为系统页面子功能隐藏。

## Principle 6: Observability Required
任何新功能必须具备：
1. 状态
2. 结果
3. 日志
4. 失败原因

否则禁止上线。

可执行标准：
- 状态：当前健康状态可见。
- 结果：最近执行结果可见。
- 日志：可追溯运行证据。
- 失败原因：可定位主因与影响范围。

## Principle 7: No Duplicate Entry
禁止重复入口。
- 同一能力只能有一个主入口。
- 若需跨域引用，使用跳转/关联，不新增平行主入口。

## Principle 8: Dashboard Is Operations Center
Dashboard 不是菜单页。
Dashboard 是运维中心。

强制要求：
- 首屏优先显示跨域状态与风险。
- 必须提供“最近事件、失败任务、异常容器、待处理动作”。
- 禁止将 Dashboard 退化为仅展示导航卡片。

## 5. 设计与开发约束

1. 所有需求必须声明：
- 服务哪个使命
- 归属哪个核心域
- 如何提升 30 秒判断能力

2. 所有设计必须声明：
- 状态模型
- 结果模型
- 风险展示策略
- 可观测证据链

3. 所有实现必须提交：
- 完成报告
- 验证步骤
- 回滚方案

## 6. 治理执行机制

1. 立项门槛
- 不满足 Mission/Domain/Principles 任一项，直接拒绝。

2. 评审门槛
- PO 负责方向与优先级。
- Architect 负责结构与边界。
- Reviewer 负责合规与质量结论。

3. 上线门槛
- 结论必须为 PASS，或 REWORK 项全部关闭后 PASS。
- REJECT 项不得上线。

## 7. 违规处理

1. 轻度违规
- 文档缺失、标注不全：限时补齐。

2. 中度违规
- 重复入口、可观测不完整：必须 REWORK。

3. 重度违规
- 定位漂移、越权新增页面/导航、破坏核心原则：REJECT + 回滚。

## 8. 原则版本管理

1. 本文档为永久治理基线。
2. 任何改动必须由 PO 与 Architect 联合审批。
3. 原则改动后需同步影响评估并通知全员执行。
