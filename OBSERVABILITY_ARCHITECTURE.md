# OBSERVABILITY ARCHITECTURE

角色视角: Product Owner / Product Architect

范围约束:
- 不新增功能
- 不新增页面
- 不新增插件
- 本文为现有代码基础上的可观测层产品架构设计

核心目标:

用户打开 NASPilot 后 30 秒内必须知道:
- 新增了什么
- 删除了什么
- 上传了什么
- Skip 了什么
- 为什么 Skip
- 失败了什么
- 哪些容器异常
- 哪些任务失败

## 1. Operations Center 定义

Operations Center 是 NASPilot 的运维可观测层，不是新页面名称，而是 Dashboard + Task + Plugin + Container + Log 的统一信息视图。

它回答 4 个问题:
- 当前系统是否健康
- 过去 24 小时发生了什么变化
- 当前有哪些风险与失败
- 下一步要先处理什么

它输出 3 类结果:
- 状态: healthy / warning / critical
- 结果: added / deleted / uploaded / skipped / failed
- 待办: now / today / can_wait

## 2. Dashboard 数据模型

Dashboard 采用四层数据模型。

### Layer A: Health Snapshot (10 秒判断)
- nas_health: cpu, memory, disk, network
- docker_health: running_count, stopped_count, error_count
- task_health: success_24h, failed_24h, pending_count
- file_health: storage_usage, recent_changes_count, transfer_activity

### Layer B: Execution Summary (24 小时结果)
- totals:
  - added_total
  - deleted_total
  - uploaded_total
  - skipped_total
  - failed_total
- by_domain:
  - pt_rss
  - alist_upload
  - docker_backup
  - log_cleanup
  - cloudflare_ddns
  - cloudflare_pages
  - scheduler
  - container

### Layer C: Risk Queue (待处理事项)
- failed_tasks_top
- failed_plugins_top
- abnormal_containers_top
- repeated_skip_items_top

### Layer D: Action Queue (下一步动作)
- open_failed_task_log
- open_failed_plugin_log
- open_container_error_log
- rerun_last_failed

## 3. Execution Center 设计

Execution Center 是执行结果语义层，统一 Task、Plugin、Container 三类执行记录。

### 3.1 统一执行实体
- execution_id
- domain: task/plugin/container
- source_slug: pt_rss, docker_backup, task_name, container_name
- trigger: manual/scheduled/system
- start_time
- end_time
- duration_ms
- final_status

### 3.2 统一结果维度
- added_count
- deleted_count
- uploaded_count
- skipped_count
- failed_count
- unchanged_count
- pending_count

### 3.3 统一诊断维度
- skip_reasons[]
- failure_reasons[]
- evidence_refs[]: log path, execution id, container id

## 4. Event Model

事件采用统一事件名和语义，不按页面划分。

### 4.1 事件类型
- health_event
- execution_event
- result_event
- failure_event
- action_event

### 4.2 核心事件字典
- execution_started
- execution_succeeded
- execution_failed
- execution_timeout
- item_added
- item_deleted
- item_uploaded
- item_skipped
- item_failed
- container_abnormal
- task_failed
- plugin_failed

### 4.3 严重级别
- info: 状态正常与普通结果
- warn: skip 激增、部分失败、容器非运行
- critical: 关键任务失败、关键插件失败、容器核心服务异常

## 5. Unified Execution Result Schema

统一执行结果 Schema 定义如下。

- execution_id: string
- domain: task | plugin | container
- source_slug: string
- source_name: string
- trigger: manual | scheduled | system
- status: ok | warning | failed | timeout | skipped
- started_at: datetime
- ended_at: datetime
- duration_ms: number
- summary:
  - added: number
  - deleted: number
  - uploaded: number
  - skipped: number
  - failed: number
  - unchanged: number
  - pending: number
- details:
  - added_items[]
  - deleted_items[]
  - uploaded_items[]
  - skipped_items[]
  - failed_items[]
- reasons:
  - skip_reasons[]
  - failure_reasons[]
- diagnosis:
  - probable_root_cause
  - recommended_next_action
  - evidence_refs[]

## 6. Plugin Result Standard

目标: 所有插件执行后，用户都能看到结果 + 风险 + 下一步。

### 6.1 通用标准
- status: ok/failed/skipped/error
- counters: added/deleted/uploaded/skipped/failed/unchanged
- reason lists: skip_reasons, failure_reasons
- evidence: log source, plugin instance, last run summary

### 6.2 按插件的用户必知项

1. PT RSS
- 新增下载了多少 (added)
- 清理了多少 (deleted_messages)
- 跳过了多少 (skipped_messages)
- 为什么跳过: 空间不足/状态非待处理/已存在
- 失败了哪些条目 (failed_messages)

2. qBittorrent (由 PT RSS 关联观测)
- 是否可连接
- 新增任务是否实际进入 qB
- 已存在还是新添加
- 因空间或接口失败导致的添加失败

3. AList Upload
- 扫描文件数 (scanned)
- 成功上传数 (uploaded)
- 跳过数 (skipped)
- 失败数 (failed)
- 本地删除数 (deleted)
- 跳过原因: 文件过大、已存在、策略限制

4. Docker Backup
- 备份状态
- 备份应用数量 (apps_count)
- 总文件数 (total_files)
- 归档产物与大小 (archive, archive_size)
- 失败原因: 路径不存在/复制失败/打包失败

5. Log Cleanup
- 删除日志文件数 (deleted)
- 截断日志文件数 (truncated)
- 跳过原因: 路径不存在或无匹配文件

6. Cloudflare DDNS
- 当前 IPv4/IPv6
- 更新记录数 (updated)
- 未变更数 (unchanged)
- 每条记录结果 created/updated/unchanged
- 失败原因: token 未配置、zone 配置错误、请求失败

7. Cloudflare Pages
- 是否部署成功 (deployed)
- 是否 skip (ipv6_unchanged)
- skip 原因 (reason)
- 项目名、服务数量、部署 URL
- 失败原因: wrangler 不可用、部署命令失败、鉴权缺失

## 7. Task Result Standard

目标: 调度与手动任务在同一标准下可比较、可排序、可追踪。

### 7.1 任务执行状态标准
- running
- success
- failed
- timeout

### 7.2 必备结果字段
- task_id / task_name
- triggered_by (manual/scheduler)
- start_time / end_time / duration_ms
- exit_code
- stdout_tail
- stderr_tail
- error_message

### 7.3 Dashboard 衍生指标
- success_24h
- failed_24h
- timeout_24h
- pending_count

pending 定义:
- enabled=true 且存在 cron_expr 且 next_run_at 在未来且最近无成功执行

### 7.4 用户执行后必知
- 本次是否成功
- 失败/超时原因
- 是否需要立即重跑
- 日志证据位置

## 8. Container Status Standard

目标: 用户 10 秒内判断 Docker 面是否稳定。

### 8.1 容器状态分层
- running: 正常运行
- stopped: 非运行
- error: 重启异常、不可操作、关键容器不在 running

### 8.2 容器基础字段
- id, name, image
- status, state, running
- created_at
- stack, ownership
- ip_addresses, ports

### 8.3 容器健康指标
- cpu_percent
- memory_percent
- net_rx / net_tx
- blk_read / blk_write
- pids

### 8.4 用户必知
- 当前运行中数量
- 停止数量
- 异常数量
- 异常容器名称与首要原因

## 9. Recent Activity Timeline

Timeline 是 24 小时运维事实流，按时间倒序。

### 9.1 时间线事件项结构
- time
- domain: task/plugin/container/file
- source
- event_type
- result_summary
- severity
- link_to_evidence

### 9.2 时间线展示优先级

优先展示以下事件:
1. execution_failed
2. execution_timeout
3. container_abnormal
4. item_added / item_deleted / item_uploaded
5. item_skipped with reason

### 9.3 24 小时业务结果摘要
- 新增总数
- 删除总数
- 上传总数
- Skip 总数 + Top 原因
- 失败总数 + Top 原因

## 10. Failure Investigation Flow

故障排查路径必须固定为 5 步，避免用户迷路。

1. 发现异常
- 来自 Dashboard 的 Recent Failures 或容器异常卡片

2. 定位执行对象
- 确认是 Task / Plugin / Container 哪一类

3. 查看统一结果摘要
- 看 counters + reasons
- 判断是配置问题、环境问题还是外部依赖问题

4. 打开证据
- Task: execution + task log
- Plugin: run_history + plugin log
- Container: container logs + exec 诊断

5. 决策与处置
- 立即重试
- 修改配置后重试
- 升级为风险待办

---

## 附录 A: 九类执行对象的执行后必知清单

1. PT RSS
- 新增了什么
- 删除了什么
- Skip 了什么
- 为什么 Skip
- 失败了什么

2. qBittorrent
- 下载是否真正入列
- 哪些条目添加失败
- 失败是否因空间不足或接口错误

3. AList Upload
- 上传了什么
- 跳过了什么
- 为什么跳过
- 失败了什么
- 本地删除了什么

4. Docker Backup
- 备份了哪些应用
- 产出了什么归档
- 失败了什么

5. Log Cleanup
- 删除了哪些日志
- 截断了哪些日志

6. Cloudflare DDNS
- 更新了哪些 DNS 记录
- 哪些未变更
- 哪些失败

7. Cloudflare Pages
- 是否部署成功
- 如果 Skip，为什么 Skip
- 部署 URL 是什么

8. Task Scheduler
- 哪些任务成功
- 哪些任务失败/超时
- 哪些任务 pending

9. Container Manager
- 哪些容器异常
- 异常是 stopped 还是 error
- 是否影响核心服务

## 附录 B: Dashboard 的 10 秒认知承诺

Dashboard 不展示功能列表，必须优先展示:
- 状态: 是否正常
- 结果: 新增/删除/上传/Skip/失败
- 风险: 容器异常、任务失败、插件失败
- 待处理: 下一步动作入口

如果 10 秒内无法回答 系统是否健康 + 最近 24 小时发生了什么，则 Dashboard 设计不达标。
