# BUGFIX_VACUUM_TRANSACTION.md — SQLite VACUUM Transaction Fix

日期：2026-08-01
严重级别：Low（非致命但每次启动打印异常堆栈）
状态：✅ 已修复

---

## 1. 问题根因

SQLite **禁止在事务内执行 `VACUUM`**。原代码把 `DELETE` 和 `VACUUM` 放在了同一个 `async with engine.begin()` 块内：

```python
async with engine.begin() as conn:   # ← BEGIN TRANSACTION
    await conn.execute(sa.text("DELETE FROM log_entries"))  # ✓ 允许
    await conn.execute(sa.text("VACUUM"))                   # ✗ 禁止！
    # engine.begin().__aexit__ → COMMIT
```

`engine.begin()` 创建了一个显式事务（`BEGIN TRANSACTION`），`DELETE` 在事务内执行没有问题。但 `VACUUM` 要求**当前没有任何活跃事务**，SQLite 直接拒绝并抛出：

```
sqlite3.OperationalError: cannot VACUUM from within a transaction
```

## 2. 修改文件

- `backend/app/core/database.py` — `_cleanup_log_spam()`

## 3. 修复方案

**两步执行**：DELETE 在事务内提交 → VACUUM 在独立连接上通过 `exec_driver_sql` 执行。

`exec_driver_sql()` 绕过 SQLAlchemy 的隐式事务管理，直接将 SQL 发送到 DBAPI 驱动层，确保 VACUUM 在没有活跃事务的连接上执行。

## 4. 修复前执行路径

```
engine.begin() → BEGIN TRANSACTION
  DELETE FROM log_entries → 219 rows deleted
  VACUUM → ❌ OperationalError
  (事务回滚/VACUUM 失败)
```

## 5. 修复后执行路径

```
engine.begin() → BEGIN TRANSACTION
  DELETE FROM log_entries → 219 rows deleted
__aexit__ → COMMIT

engine.connect() → 新连接（无事务）
  exec_driver_sql("VACUUM") → ✅ 成功
```

## 6. 为什么 SQLite 报错

SQLite 的 VACUUM 命令是对**整个数据库文件**的重建操作。如果在事务内执行，其他连接可能持有未提交的写操作，导致 VACUUM 读取到不一致的数据库状态。SQLite 强制在事务外执行以确保数据完整性。

## 7. 为什么修复有效

| 步骤 | 方式 | 事务状态 |
|------|------|---------|
| DELETE | `engine.begin()` | 有事务 → 提交后释放 |
| VACUUM | `engine.connect()` + `exec_driver_sql()` | 无事务 → SQLite 允许 |

`exec_driver_sql` 直接绕过 SQLAlchemy 层到达 aiosqlite 驱动，不会触发隐式 `BEGIN`。

## 8. 风险评估

- **风险等级**：Low
- **影响范围**：仅启动时的一次性清理操作
- **兼容性**：仅针对 SQLite；非 SQLite 数据库不受影响（函数第一步就 return）
- **副作用**：无。DELETE 和 VACUUM 分别使用独立连接，互不影响
- **回滚方案**：如果 `exec_driver_sql("VACUUM")` 在特定驱动下不可用，可回退为 `await conn.execute(sa.text("VACUUM"))` + `await conn.commit()`

## 9. 验证结果

```
2026-08-01 19:13:26 [INFO] naspilot.db — Deleted 219 legacy DB log entries (logs now file-only)
2026-08-01 19:13:26 [INFO] naspilot.db — Database vacuum completed
```

- ✅ DELETE 成功（219 条删除）
- ✅ VACUUM 成功（无异常堆栈）
- ✅ 启动日志干净
