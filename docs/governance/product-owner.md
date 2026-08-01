# NASPilot Product Owner Governance

## 1. 角色目标

Product Owner (PO) 负责确保 NASPilot 在持续迭代中不偏离产品定位：
- NAS Monitoring
- NAS Automation
- NAS Operations

PO 对“做什么、为什么做、何时做、是否进入开发”负责最终裁决。

## 2. 职责

1. 产品定位管理
- 维护 NASPilot 的 Mission、核心业务域、核心用户场景。
- 维护边界：NAS 运维与自动化平台，不扩展为泛社交、泛内容、泛协作产品。

2. 产品路线图管理
- 维护 3 个层级路线图：年度方向、季度目标、Phase 计划。
- 将当前重点固定到以下战略轴：
  - Dashboard 重构
  - Observability 建设
  - 任务中心 建设
  - 集成工具 建设
  - Container Management 优化
  - File Management 优化
  - AI Assistant 建设

3. 优先级管理
- 统一使用 WSJF + 风险因子双维度排序。
- 高风险稳定性项可覆盖低价值新功能项。

4. 功能准入评审
- 评审每个功能是否符合 Mission、核心域、产品原则。
- 决定功能进入：Backlog / Discovery / Implementation / Rejected。

5. Phase 审批
- 每个 Phase 必须由 PO 书面批准后才能进入开发。
- 未审批事项禁止开发、禁止合并。

## 3. 权限

1. 有权批准或否决任何功能进入开发。
2. 有权冻结或暂停任何偏离路线图的开发项。
3. 有权要求拆分需求、收敛范围、延后非关键能力。
4. 有权定义阶段退出标准（Definition of Done for Phase）。
5. 有权发起产品方向纠偏。

## 4. 输入

1. 业务输入
- 用户反馈
- 运维事件与故障复盘
- 功能使用数据与留存数据

2. 技术输入
- Architect 设计方案
- Reviewer 审查报告
- 开发完成报告与质量指标

3. 治理输入
- NASPilot Product Principles
- 当前路线图与 Phase 目标
- 现有信息架构与导航规范

## 5. 输出

1. 产品定位基线（版本化）。
2. 季度路线图与 Phase 清单。
3. 功能准入决策记录（Approve / Hold / Reject）。
4. 开发授权单（Phase Approval Record）。
5. 变更通告（涉及范围、风险、上线门槛）。

## 6. 允许开发什么

满足以下全部条件的功能允许进入开发：
1. 明确归属核心业务域：System / Container / Task / File，或为 AI Assistant（且服务于上述域）。
2. 能提升用户在 30 秒内完成状态判断与处置能力。
3. 可观测性完整：至少定义状态、执行结果、日志、失败原因。
4. 不新增重复入口，不破坏既有信息架构。
5. 有明确验收标准与回滚方案。

## 7. 禁止开发什么

1. 与 Mission 无关的功能（如社交、内容流、泛知识库）。
2. 无可观测闭环的功能（仅有页面、无状态与结果）。
3. 未定义业务域归属的“孤立工具页”。
4. 造成导航重复入口、入口冲突、命名冲突的改动。
5. 未经过 Phase 审批的临时需求。
6. 以技术兴趣驱动、无用户价值证据的扩展开发。

## 8. 如何批准进入开发阶段

采用五闸门（5 Gates）流程。

Gate 1: Problem Fit
- 必答：解决哪个真实运维问题？
- 验收：问题描述、目标用户、触发场景完整。

Gate 2: Domain Fit
- 必答：归属哪个核心业务域？是否跨域？
- 验收：域归属明确，跨域边界明确。

Gate 3: Principle Fit
- 必答：是否满足 Status First / Result First / Risk First？
- 验收：8 条产品原则逐条打勾。

Gate 4: Architecture & Delivery Fit
- 必答：架构是否评审通过？数据与接口是否可落地？
- 验收：Architect 通过，开发任务可拆分。

Gate 5: Release Fit
- 必答：验证步骤、回滚方案、风险预案是否完备？
- 验收：Reviewer 可执行审查脚本，PO 签字。

审批结论：
- Approved: 进入开发
- Conditional Approved: 补齐条件后进入开发
- Rejected: 不进入当前周期

## 9. 审查清单（PO Checklist）

1. 该需求是否直接支持 NAS Monitoring / Automation / Operations？
2. 是否明确对应 System/Container/Task/File/AI Assistant 中至少一个域？
3. 是否提升 30 秒决策能力（状态、异常、下一步动作）？
4. 是否存在与现有功能重复入口或重复能力？
5. 是否要求新增页面？若是，是否已有 IA 与导航审批？
6. 是否定义了关键指标（成功率、失败率、异常发现时效）？
7. 是否有可执行验收标准（而非口头标准）？
8. 是否包含测试要求、回滚方案、变更说明？
9. 是否在当前 Phase 范围内？
10. 是否已获得 Architect 与 Reviewer 前置意见？

## 10. Phase 审批规则

1. Phase 启动条件
- 范围文档冻结
- 架构评审通过
- 任务拆分完成
- 风险清单确认

2. Phase 执行中变更
- 任何新增功能必须发起 Change Request (CR)
- CR 未批准，开发不得开始

3. Phase 结束条件
- 完成报告齐全
- 审查结论为 PASS 或 REWORK 已闭环
- PO 发布阶段结案记录
