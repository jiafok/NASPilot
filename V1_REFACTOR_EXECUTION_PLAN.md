# V1 Refactor Execution Plan

角色: Chief Product Architect

范围约束:
- 禁止新增功能
- 禁止新增页面
- 禁止新增插件
- 仅做信息架构、导航、可观测与界面层重构

最终产品方向:
- NAS Monitoring + NAS Automation + NAS Operations Platform
- 一级业务域: Dashboard, Automation Center, Application Center, Container Manager, File Manager, Log Center, System Settings, AI Assistant
- Plugin Center 退出一级导航，进入 System Settings -> Plugin Management
- Dashboard 升级为 Operations Center，聚焦状态、结果、风险、待处理事项

实施原则:
- 先结构后视觉，先可观测后美化
- 每一阶段均可独立回滚
- 每一阶段均保持业务可用，不中断任务调度与插件执行

## Phase 1: 导航重构

目标:
- 将产品主导航重构为业务域导向，而非工具堆叠导向
- 统一一级入口为: Dashboard, Automation Center, Application Center, Container Manager, File Manager, Log Center, System Settings, AI Assistant
- 移除 Plugin Center 的一级导航位置，仅保留在 System Settings 内的 Plugin Management 入口

影响页面:
- frontend/src/layouts/MainLayout.tsx
- frontend/src/App.tsx
- frontend/src/pages/plugins/PluginList.tsx
- frontend/src/pages/system/SystemSettings.tsx

影响组件:
- 主侧边菜单与移动端菜单
- 顶部导航状态高亮逻辑
- 一级到二级页面的路由映射

风险:
- 菜单路径变化导致用户短期迷失
- 历史书签路径命中旧入口
- Plugin 配置入口被误判为下线

验证方案:
- 验证 8 个一级业务域均可单击到达
- 验证旧路径到新路径存在兼容跳转
- 验证 Plugin Management 可在 System Settings 内可发现
- 验证移动端抽屉菜单与桌面菜单一致

回滚方案:
- 恢复 MainLayout 与 App 的旧菜单结构与路由映射
- 保留已变更文案但恢复旧入口层级
- 通过版本标签快速回退到 Phase 1 前快照

## Phase 2: Dashboard Operations Center

目标:
- 将 Dashboard 从功能入口升级为 Operations Center 状态中心
- 用户 10 秒内确认:
  - NAS 状态
  - Docker 状态
  - Task 状态
  - File 状态
  - 最近 24 小时发生了什么

影响页面:
- frontend/src/pages/Dashboard.tsx
- frontend/src/components/ResourceMonitor.tsx
- frontend/src/pages/system/LogCenter.tsx

影响组件:
- 状态卡片组件
- Recent Activity 时间线组件
- Recent Failures 风险清单组件
- Quick Actions 快捷动作区

风险:
- 首页信息量增加导致认知拥堵
- 统计口径不一致导致信任下降
- 高刷新频率造成前端性能波动

验证方案:
- 10 秒可用性测试: 新用户能否在 10 秒回答四大状态是否正常
- 24 小时回放测试: 新增、删除、上传、Skip、失败是否能被看见
- 指标一致性测试: Dashboard 汇总值与 Task/Plugin/Container 明细对齐
- 性能测试: 首页加载与刷新不影响主要交互

回滚方案:
- 保留 Operations Center 数据层，回退至旧 Dashboard 排版
- 暂时隐藏时间线与待处理区，仅保留核心状态区
- 逐步恢复旧组件组合以快速止血

## Phase 3: Application Center 重构

目标:
- 将集成工具统一归并为 Application Center
- 范围固定为:
  - PT RSS
  - AList Upload
  - Docker Backup
  - Cloudflare DDNS
  - Cloudflare Pages
  - Log Cleanup
- 明确 Application 的职责边界: 配置、运行、状态、执行结果

影响页面:
- frontend/src/pages/PT_RSS.tsx
- frontend/src/pages/AlistUpload.tsx
- frontend/src/pages/DockerBackup.tsx
- frontend/src/pages/CloudflareDDNSPage.tsx
- frontend/src/pages/CloudflarePages.tsx
- frontend/src/pages/plugins/LogCleanup.tsx
- frontend/src/pages/plugins/PluginList.tsx

影响组件:
- frontend/src/components/PluginConfigForm.tsx
- frontend/src/components/LogViewer.tsx
- Application 卡片与状态标识

风险:
- 应用配置入口变动造成操作路径改变
- Plugin 与 Application 关系认知冲突
- 执行结果字段差异造成展示不一致

验证方案:
- 六个应用均可完成 配置 -> 运行 -> 查看结果 闭环
- 结果页必须展示成功、失败、Skip 及原因
- Application 列表状态与各应用详情状态一致
- Plugin 作为后台支撑层可被访问但不抢占主入口

回滚方案:
- 保留 Application Center 信息架构，恢复原 Tool 分组展示
- 保留 PluginList 现有入口用于临时兜底
- 对单个应用支持分项回滚，不影响其他应用

## Phase 4: Observability 落地

目标:
- 落实 OBSERVABILITY_ARCHITECTURE 中的统一执行结果语义
- 建立跨 Task、Application、Container 的统一事件和结果口径
- 让用户稳定看到:
  - 新增了什么
  - 删除了什么
  - 上传了什么
  - Skip 了什么以及原因
  - 失败了什么
  - 哪些容器异常
  - 哪些任务失败
  - 下一步需要处理什么

影响页面:
- frontend/src/pages/Dashboard.tsx
- frontend/src/pages/tasks/TaskList.tsx
- frontend/src/pages/ContainerManager.tsx
- frontend/src/pages/system/LogCenter.tsx
- frontend/src/pages/system/LogFullPage.tsx

影响组件:
- 统一结果摘要组件
- Recent Activity Timeline 组件
- Recent Failures 组件
- 执行证据跳转组件

风险:
- 多源数据融合带来字段对不齐
- 旧插件返回格式不统一导致结果解释偏差
- 失败归因错误会误导用户处置

验证方案:
- 九类执行对象抽样验证:
  - PT RSS
  - qBittorrent
  - AList Upload
  - Docker Backup
  - Log Cleanup
  - Cloudflare DDNS
  - Cloudflare Pages
  - Task Scheduler
  - Container Manager
- 每类对象执行后均可回答 结果 + 原因 + 风险 + 下一步
- 最近 24 小时时间线可追溯到日志证据

回滚方案:
- 回退为各域独立统计，不做统一口径聚合
- 暂时隐藏统一时间线，仅保留域内结果列表
- 保留失败清单最小可用视图，优先保障排障路径

## Phase 5: UI Modernization

目标:
- 在不新增功能前提下，完成信息密度、可读性、视觉一致性现代化
- 让状态、风险、待处理事项优先于配置项呈现

影响页面:
- frontend/src/pages/Dashboard.tsx
- frontend/src/layouts/MainLayout.tsx
- frontend/src/pages/tasks/TaskList.tsx
- frontend/src/pages/ContainerManager.tsx
- frontend/src/pages/system/LogCenter.tsx
- frontend/src/pages/system/SystemSettings.tsx

影响组件:
- 全局主题令牌
- 状态卡片、风险卡片、时间线、数据表格样式
- 移动端布局与桌面端布局适配

风险:
- 样式升级引入视觉回归
- 紧凑型页面改版导致操作路径变化
- 深色区域和浅色区域风格不统一

验证方案:
- 关键路径可用性回归:
  - 登录后查看状态
  - 运行任务
  - 查看失败
  - 进入日志
- 视觉一致性检查: 字体、色阶、间距、状态色语义一致
- 多分辨率验证: 桌面和移动端关键模块不折损

回滚方案:
- 主题与样式分层回滚，先回滚皮肤后回滚结构
- 保留信息架构新结构，回退视觉到上一稳定版本
- 单页面样式异常可按页面快速回退，不阻断全站

## 里程碑与交付门禁

Phase 1 门禁:
- 导航结构完成
- Plugin 入口迁移完成

Phase 2 门禁:
- Dashboard 完成 Operations Center 化
- 10 秒状态判断通过

Phase 3 门禁:
- Application Center 六应用闭环打通

Phase 4 门禁:
- 统一可观测口径上线
- 最近 24 小时活动与失败调查链路可用

Phase 5 门禁:
- 视觉与交互现代化完成
- 全链路回归通过

## 总体验收标准

用户打开 NASPilot 后应能立即回答:
- NAS 是否正常
- Docker 是否正常
- Task 是否正常
- File 是否正常
- 最近 24 小时发生了什么
- 哪些任务失败
- 哪些容器异常
- 哪些文件上传成功
- 哪些文件上传失败
- 下一步需要处理什么

若以上问题无法在 30 秒内回答，视为本次 V1 重构未达标。
