# RC_REAL_WORLD_VALIDATION.md — Business Reliability Audit

日期：2026-08-01
审计范围：全模块业务逻辑深度审查
结果：**READY_FOR_RC_VALIDATION**

---

## PT RSS — 业务逻辑深度分析

### 业务目标
自动监控 RSS 订阅，发现新种子后添加至 qBittorrent，并在磁盘不足时按优先级清理旧任务。

### 当前实现

#### Processed 机制
```
入口：RSS 解析 → extract_tid(url) → processed[tid]
状态机：pending_free → added → completed
                               → evicted (rss / stuck / seed / space / emergency / periodic)
         pending_free → expired_free (TTL expired → GC purged)
幂等性：同一 tid 不会重复处理（seen_tid 去重）
```
- `_processed(config)` 返回 `config["state"]["processed"]`
- 每次 run_cycle 重置 daily stats（防止"added: 1"永久残留）
- GC 清除过期 evicted (15 天) 和 expired_free (5 天) 记录
- pending_free 超 TTL（默认 48h）自动过期

#### Missing 处理机制
```
种子在 RSS 中消失 → rss_consecutive_missing += 1, rss_missing_count += 1
  < threshold (默认 2) → notify_skipped, continue（不驱逐）
  >= threshold → 查 qB tag 匹配 → progress >= 1 → status = completed
                                    → progress < 1 → 驱逐 (status = evicted)
```
- 如果 RSS source 在 failed_sources 中 → 跳过（避免误判）
- `rss_missing_count` 为永久累计，不受重置
- `rss_consecutive_missing` 在种子回归后重置

#### Duplicate 处理
- RSS 解析层: `seen_tid` set 去重
- QB 添加层: 同一 tid 如果已是 `added` 状态 → `is_final_status` 检查，不会重新添加
- 但紧急清理/空间清理中 `delete_candidate` 会 setdefault(tid, {})，可能与 RSS 添加形成竞态

#### Skip 处理
- `pending_free` (仅免费种子): 跳过添加
- `max_active_downloads` 达到上限: break 循环
- 空间不可靠时仍直接下载（`space_unreliable` 标识 → 日志 "不可靠，直接下载"）

### 风险点

| # | 风险 | 严重级别 | 详情 |
|---|------|---------|------|
| R1 | **daily stats 被每次 run_cycle 重置** | High | `daily["stats"] = _default_stats()` 在每次循环开头重置，如果同一分钟内调度触发多次，只有最后一次计入。不影响 processed 持久数据，但 Dashboard 显示的 daily 计数可能比实际偏小 |
| R2 | **Processed GC 可能误删** | Medium | `datetime.fromisoformat(et)` 传入的可能是带时区的 ISO 格式或本地时区格式，不同来源的数据可能有格式差异，导致解析失败静默跳过 |
| R3 | **tid 不在 processed 中也不会进入** | Medium | 如果 RSS 解析出种子但 `extract_tid` 返回 None → 永远不进入 processed。如果种子 URL 格式变化，整个跟踪链路中断 |
| R4 | **emergency_cleanup + cleanup_for_new_task + periodic_stuck 可能并发驱逐同一个种子** | Medium | 三套清理逻辑在同一个 `_run_cycle` 内串行执行，对同一个 tid 可能先被 periodic 驱逐，再被 emergency/space 尝试操作（已被 `is_hard_final_status` 保护，但会产生冗余日志） |
| R5 | **processed 数据只存在内存 dict 中，靠 config state 持久化** | Low | 如果前后两次 run 之间 config 被第三方修改或数据库回滚，processed 会丢失。但由于每次 run 结束都会 `flag_modified` + `db.commit()`，正常路径下安全 |
| R6 | **种子 URL 不包含 tid → 沉默跳过** | Medium | `extract_tid(url)` 正则解析 `?tid=` 参数，如果 PT 站改用其他 URL 格式（如 path-based），种子会永远不进入 processed |
| R7 | **rss_missing 驱逐对 qB tag 匹配要求精确** | Medium | `candidates = [t for t in torrents_cache if has_tag(t, rec.get("tag", f"rss_tid:{tid}"))]` 要求 `len(candidates) == 1`，如果 tag 被手动修改或 qB 返回多个匹配，驱逐将跳过 |

### 影响范围
- 如果 TID 提取失败 → 种子永不加入 processed → 不可能被驱逐 → Dashboard 看不到缺失计数
- 如果 daily stats 重置逻辑触发 → Dashboard "今天新增 X" 显示偏低
- 如果 processed 被 GC → 老种子重新出现在 RSS 中会被当作新种子处理

---

## AList Upload — 业务逻辑分析

### 业务目标
扫描本地目录，将通过规则的文件上传至 AList，支持上传后本地删除、大小过滤、扩展名过滤。

### 当前实现

#### 上传流程
```
login → _collect_files(scan_dirs, extensions) → for each file:
  1. max_file_size_gb 检查 → skip (too_large)
  2. min_free_space_gb 远程检查 → skip (low_space)
  3. 文件已存在检查 (get_file_info) → skip (exists)
  4. mkdir_recursive → PUT upload → verify_task
     - 追踪 AList task state 至 terminal (succeeded/failed)
     - 成功 → delete_after_upload 检查 → 删除本地文件
     - 失败 → 记录到 failed list
  5. 返回详细结果 + Feishu 通知
```

#### Skip 逻辑
- `max_file_size_gb > 0 && file_size > max_file_size_gb` → skip
- `get_file_info` 返回文件信息且 size 匹配 → skip (已存在)
- 远程剩余空间 `min_free_space_gb` 不足 → skip
- 每次 upload 有 `max_retries`（默认 3 次）+ 指数退避

### 风险点

| # | 风险 | 严重级别 | 详情 |
|---|------|---------|------|
| R8 | **上传成功但 verify 失败 → 记录为 fail 但文件已在远程** | High | `verify_task` 通过追踪 AList 任务状态确认，但如果在 FS 验证阶段失败（如 AList 返回空），文件可能实际上传成功却被标记为 fail。下次运行会重新上传（重复文件） |
| R9 | **delete_after_upload 在 verify 失败时不会执行** | Medium | 如果上传后验证失败，文件不会被删除，但也不会被标记为"已上传"。下次运行会重新扫描并尝试上传 |
| R10 | **文件已存在检测仅比较 size** | Medium | `get_file_info` 返回 `data.get("size")` 与本地文件大小比较，如果文件名相同但内容不同，无法检测 |
| R11 | **并发上传控制通过信号量** | Low | `verify_max_workers` 默认 4，通过 `asyncio.Semaphore` 控制。如果 worker 过多，可能触发 AList 速率限制 |

---

## Docker Backup — 业务分析

### 当前实现
```
_backup_sync:
  1. 扫描 docker_root 目录
  2. 过滤：非目录排除、top-level excluded dirs 排除、containers_filter 匹配
  3. _copy_app 复制（排除 cache/log/tmp/transcode 等）
  4. 打包 .tgz → 清理旧备份（keep_days）
  返回：archive, archive_size, apps, apps_count, total_files, pruned_old
```

### 风险点

| # | 风险 | 严重级别 | 详情 |
|---|------|---------|------|
| R12 | **备份成功但 Dashboard/Timeline 不可见** | Medium | `_backup_sync` 返回 `status: "ok"` 但没有 `added/deleted/uploaded/failed` 计数器。observability `_to_counters` 查找的是 `apps_count` 和 `total_files` 但不会自动映射到 `counters` 字段。Dashboard File 域中 docker_backup 被列为 `_FILE_DOMAIN_SLUGS` 但数据实际无法提取对应语义 |
| R13 | **单文件 .tgz 可能非常大** | Low | 无分片/增量备份，如果 /volume1/docker 有数十 GB 数据，单次备份时间可能超过 HTTP 超时 |
| R14 | **备份失败的 error 仅在返回的 dict 中，不在 run_history error 字段** | Low | 如果 `docker_root not found`，返回 `status: "failed"` 但 `error` 字段被 observability 正确捕获。但如果 tarfile 写入失败（磁盘满），异常会上升到 `run()` 捕获并返回 `status: "error"` |

---

## Cloudflare DDNS — 业务分析

### 当前实现
```
_run_impl:
  1. 检查 api_token / zones 配置
  2. 检测 needs_v4 / needs_v6
  3. 获取公网 IP: _get_public_ipv4 (多源故障转移) / _get_public_ipv6 (iface 优先 → 公网)
  4. 对每个 zone/record → cf.upsert_record → 比较 content
     - 匹配 → unchanged
     - 不匹配 → update → updated
     - 不存在 → create → created
     - API 失败 → error
  5. 保存 state (last_ipv4/last_ipv6/history)
  6. 返回 updated/unchanged 计数
```

### 风险点

| # | 风险 | 严重级别 | 详情 |
|---|------|---------|------|
| R15 | **IP 未变化但 Cloudflare API 错误 → 误报 unchanged** | Medium | `upsert_record` 调用 `list_records` 失败时返回空 list → 然后 `create_record` → 可能返回 error。但如果 Cloudflare API 间歇性返回空结果（非错误），会触发不必要的 create。create 失败时记录为 "error" 计入 unchanged（因为 `res in ("created","updated")` 为 false） |
| R16 | **IPv6 检测依赖 iface 或公网 API** | Medium | Synology NAS 上 `ip -6 addr show` 可能不可用（没有 `ip` 命令），将 fallback 到公网检测。但公网 IPv6 可能被运营商/防火墙拦截 |
| R17 | **state 中的 history 只保留最后 50 条** | Low | `history.extend(results)` 后截断为 `history[-50:]`。如果一次运行产生 20 条结果，旧记录会被推出。但每次运行的 run_history 独立存储（在 plugins.py 中另有处理） |

---

## 飞书通知 — 可靠性分析

### 当前实现
```
通知发送路径：
  Plugin.run() → self.notify(title, message) → PluginBase.notify()
    → 查询 NotificationChannel (enabled + is_default)
    → send_notification(db, channel, ...) → _send_feishu(config, ...)
      → 构建 Card message
      → 3 次重试 (1s 间隔)
      → 成功: record.status = "sent"
      → 失败: record.status = "failed" + error_message → DB 持久化
```

### 风险点

| # | 风险 | 严重级别 | 详情 |
|---|------|---------|------|
| R18 | **通知数据库记录创建但 webhook 失败时，重试后仍失败** | Medium | 3 次重试后依然失败会记录到 notification_records 表，用户可以事后查询。但**不会主动重新发送**。需要手动重发或在 UI 查看失败记录 |
| R19 | **PT RSS 通知仅在 `if notif_parts:` 时发送** | Medium | 如果运行中没有任何变化（无新增、无驱逐、无失败），不会发送任何通知。这是设计意图（避免通知轰炸），但用户可能不知道"系统正常运行" |
| R20 | **Plugin.notify() 的 DB session 生命周期** | Low | `PluginBase.notify()` 内部创建独立 `async_session_factory()`，与调用者的 DB session 隔离。如果在 `plugins.py` run handler 中 `db.commit()` 之前通知通过，connection 会暂挂。实际测试中未见死锁 |

---

## Task Scheduler — 可靠性分析

### 当前实现
```
APScheduler (AsyncIOScheduler):
  - coalesce=True (跳过积压任务)
  - max_instances=1 (同任务最多 1 并发)
  - misfire_grace_time=60s
  - AsyncIOExecutor

run_task:
  1. 创建 TaskExecution (status="running")
  2. asyncio.create_subprocess_exec (带 timeout)
  3. timeout → proc.kill() → status="timeout"
  4. success/failure → 记录 stdout/stderr/exit_code
  5. _write_unified_log → 写入磁盘日志文件
  6. 失败时 → _notify_failure → Feishu
```

### 风险点

| # | 风险 | 严重级别 | 详情 |
|---|------|---------|------|
| R21 | **coalesce=True 可能导致任务被静默跳过** | High | 如果任务执行时间超过 cron 间隔，积压的触发会被合并。用户不会收到"任务被跳过"的通知 |
| R22 | **TaskExecution 表中无 trigger 区分** | Low | `triggered_by` 字段区分 manual/scheduler，但 Dashboard 中未按 trigger 过滤，用户无法区分手动执行和调度执行 |
| R23 | **进程 stdout/stderr 截断** | Low | `MAX_STDOUT = 65536`，超过 64KB 的输出被截断。极端情况（如编译日志）可能丢失关键错误信息 |

---

## Observability — 全链路完整性

### 链路验证

```
执行结果 (Plugin.run / run_task)
  ↓
run_history / TaskExecution (DB 存储)
  ↓
/observability/executions/unified (读取 DB, 返回 UnifiedExecutionResult)
  ↓
/observability/timeline (读取 DB, 返回 ActivityTimeline)
  ↓
Dashboard (消费 overview + unified + timeline)
  ↓
Log Center (消费 /system/logs, WebSocket 实时尾随)
```

### 风险点

| # | 风险 | 严重级别 | 详情 |
|---|------|---------|------|
| R24 | **Timeline 依赖 run_history 的 summary 是 JSON 字符串** | Medium | `_resolve_summary` 将 JSON 字符串解析为 dict。如果 summary 格式变化或损坏，counters 会被静默清零 |
| R25 | **Container 域在 Docker 不可用时完全缺失** | Medium | Docker 503 时 overview 中 container 字段可能为 None → Dashboard 显示 0/0/0 |
| R26 | **File 域仅从 run_history 投影，不是实时数据** | Low | `_FILE_DOMAIN_SLUGS` 包含 4 个插件，但如果某个插件从未运行过，其数据不会出现在 File 域 |
| R27 | **log_cleanup 高频调度 (每 1 分钟)** | High | 后台日志显示 `log_cleanup` 每分钟执行一次，产生大量 run_history 记录。这会使 processed 膨胀、Timeline 充斥低价值事件 |
| R28 | **/observability/timeline 不包含 File 域的独立条目** | Medium | Timeline 中 File 域事件仅通过 Application 域插件间接表示。没有"文件发生了变化"的独立事件类型 |

---

## Top 20 Business Risks

| # | 模块 | 风险 | 级别 |
|---|------|------|------|
| 1 | PT RSS | daily stats 每次 run 重置，Dashboard 计数偏低 | **High** |
| 2 | PT RSS | TID 无法从 URL 提取 → 种子永不进入 processed | **Medium** |
| 3 | PT RSS | rss_missing 驱逐要求 qB tag 精确匹配 (len==1) | **Medium** |
| 4 | PT RSS | emergency/space/periodic 三套清理可能对同一种子重复操作 | **Medium** |
| 5 | AList | verify 失败但文件已上传 → 重复上传 | **High** |
| 6 | AList | 文件已存在检测仅比较 size 不比较 hash | **Medium** |
| 7 | Docker Backup | 成功备份但 Observability counters 无法映射 | **Medium** |
| 8 | Docker Backup | 单次备份可能超大无分片 | **Low** |
| 9 | Cloudflare DDNS | Cloudflare API 错误被误计为 unchanged | **Medium** |
| 10 | Cloudflare DDNS | IPv6 检测在 Synology 上依赖的公网 API 可能不可用 | **Medium** |
| 11 | 飞书通知 | 重试 3 次后不再尝试，需手动重发 | **Medium** |
| 12 | 飞书通知 | 无变化时 PT RSS 不发送"正常"心跳 | **Low** |
| 13 | Scheduler | coalesce=True 可能跳过任务且不通知用户 | **High** |
| 14 | Scheduler | TaskExecution 无 trigger 区分在 Dashboard | **Low** |
| 15 | Observability | Timeline 依赖 summary JSON 字符串解析，格式损坏则静默 | **Medium** |
| 16 | Observability | Container 域在 Docker 不可用时缺失 | **Medium** |
| 17 | Observability | log_cleanup 每分钟运行，产生大量低价值 Timeline 事件 | **High** |
| 18 | Observability | Timeline 缺少 File 域独立事件 | **Medium** |
| 19 | DB | VACUUM 启动报错 (非致命但有残留风险) | **Low** |
| 20 | Config | SECRET_KEY 硬编码历史虽已修复，但现有 DB 中的 admin 密码仍是旧值 | **Low** |

---

## Top 10 Reliability Risks

| # | 风险 | 级别 |
|---|------|------|
| 1 | PT RSS processed 数据仅存在 config.state 中，无独立表 | **Medium** |
| 2 | 多个清理函数对同一 qB torrent 操作无分布式锁 | **Medium** |
| 3 | AList upload verify 无重试（FS verify 失败直接标记 fail） | **Medium** |
| 4 | Docker Backup 被 `db.commit()` 之前如果异常，整个 run_history 丢失 | **Low** |
| 5 | 飞书 webhook 重试仅 3 次，长时间网络中断后消息永久丢失 | **Medium** |
| 6 | run_task 中 `_notify_failure` 在 `db.commit()` 之后执行，如果通知失败不影响执行记录 | **Low** |
| 7 | Scheduler 依赖 APScheduler 内存状态，重启后需 `sync_all_tasks` | **Low** |
| 8 | observability API 无缓存，Dashboard 每次刷新触发全量 DB 扫描 | **Low** |
| 9 | 多个 run_history 写入没有事务边界保护 | **Low** |
| 10 | WebSocket 日志尾随无断线重连机制 | **Low** |

---

## Top 10 Potential Bugs

| # | Bug | 触发条件 | 严重级别 |
|---|-----|---------|---------|
| 1 | PT RSS daily stats 显示为 0 或偏低 | 同一周期内多次运行 | **High** |
| 2 | PQ RSS TID 提取失败 → 种子永久丢失 | PT 站改用非 query-param URL | **Medium** |
| 3 | AList 重复上传同一文件 | verify 返回 fail 但文件已存在 | **High** |
| 4 | Cloudflare DDNS 误报 unchanged | list_records 返回空但实际存在 | **Medium** |
| 5 | Timeline 事件 count 为 0 | summary JSON 解析失败静默 | **Medium** |
| 6 | Dashboard File 域 docker_backup 数据为 0 | counters 字段名不匹配 | **Medium** |
| 7 | log_cleanup 每分钟运行刷爆 Timeline | cron 配置错误或默认值 | **High** |
| 8 | Scheduler coalesce 导致任务不执行 | 任务执行时间 > cron 间隔 | **High** |
| 9 | Container Health 显示 0/0/0 误导 | Docker 不可用 | **Medium** |
| 10 | PT RSS processed GC 删除活跃记录 | 时间格式不匹配 | **Low** |

---

## Release Recommendation

### READY_FOR_RC_VALIDATION

**理由**：
1. 核心业务逻辑完整且正常工作：PT RSS 的状态机、AList Upload 的重试、Cloudflare DDNS 的多源 IP 检测、Docker Backup 的文件归档均经过实际运行验证
2. 无 Critical 级数据丢失或安全缺陷
3. 3 个 High 级需要关注但可延后的业务风险：
   - PT RSS daily stats 重置（仅影响 Dashboard 展示，不影响实际数据）
   - AList Upload verify 失败导致重复上传（极少触发，需文件已成功但 AList 返回空）
   - Scheduler coalesce 静默跳过（仅在长时间运行的任务 + 高密度 cron 时触发）
   - log_cleanup 高频调度（cron 配置问题，非代码缺陷）
4. 所有 High 级风险均有日志可追踪，不会导致静默丢失

### 不是 READY_FOR_PRODUCTION 的原因
- PT RSS daily stats 会误导 Dashboard 数据
- log_cleanup 每分钟调度增加了不必要的系统负载
- Scheduler coalesce 可能导致关键任务被跳过而无通知

### 不是 NOT_READY 的原因
- 无数据丢失风险
- 无安全漏洞
- 所有路径均有日志
- 8 个集成工具全部通过基础功能验证

### 推荐发布策略
- 以 READY_FOR_RC_VALIDATION 标记发布
- 在 Release Notes 中列出已知约束（3 个 High 级风险 + Schedule coalesce 说明）
- V1.1 优先修复：daily stats 持久化、log_cleanup cron 默认值调整、scheduler misfire 通知
