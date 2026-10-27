# English Learning Center — 架构评审

状态：**Review Gate 2（Architecture Review）**

---

## 1. 在 NASPilot 中的位置

```
NASPilot (唯一服务端)
│
├── System Module          ← 复用：用户 / 设置 / 日志
├── Docker Module          ← 复用：容器管理
├── File Manager           ← 复用：目录浏览白名单
├── AI Center              ← 复用：统一 AI 客户端（本次抽离）
├── Media Download Center  ← 上游：把资源下载到 <DATA_DIR>/media
│
└── English Learning Center        ← 本次新增
    ├── Resource Scanner
    ├── Metadata Engine
    ├── Transcript Engine
    ├── Vocabulary Engine
    ├── Learning Progress
    ├── AI Tutor
    ├── Recommendation Engine (预留)
    ├── Media Stream Service
    └── Learning Open API
```

## 2. 代码结构

```
backend/app/
├── models/english.py                8 张表
├── services/
│   ├── ai_service.py                ★ 统一 AI 客户端（被 /ai 与 English 共用）
│   └── english/
│       ├── scanner.py               目录 → 课程/课时（幂等）
│       ├── metadata.py              分类 / 难度 / 时长 / 封面推导
│       ├── subtitles.py             srt / vtt 解析
│       ├── vocabulary.py            生词 / 短语提取与统计
│       ├── stream.py                HTTP Range 流式响应
│       └── query.py                 读侧聚合（供 API 使用）
└── api/v1/english.py                开放 API + 管理 API
frontend/src/pages/english/EnglishCenter.tsx   Web 管理/浏览页
```

## 3. 数据流

```
Media Download Center ──▶ <DATA_DIR>/media/<Video|Audio>/<Channel>/…/<Lesson>.mp4|.mp3
                                        │
                                   Resource Scanner（手动/定时）
                                        ▼
                    courses / lessons / transcripts / vocabulary
                                        │
                          Learning Open API (/api/english/*)
                                        ▼
                              EnglishFlow (Flutter)
```

扫描是**幂等**的：以文件绝对路径为稳定键（`lessons.file_path` 唯一索引），
重复扫描只更新元数据，不新增行。

## 4. 关键设计决策

| 决策 | 理由 |
| --- | --- |
| 扫描结果落到**关系表**而非运行时读目录 | 客户端查询需要分页/筛选/排序，实时遍历 NAS 目录无法支撑 |
| 字幕句子以 JSON 存在 `transcripts.segments` | 避免为每个句子建表导致行数爆炸；MVP 检索用 `search_text LIKE` |
| 媒体流由 NASPilot 代理，不让客户端访问文件 | 客户端无 NAS 凭据，且需鉴权；统一走 HTTP + Range |
| AI 调用集中在 `ai_service.py` | 现 `/ai/ask` 内联 httpx，设置页的 `AI_API_KEY` 根本没被读取；抽离后两处共用 |
| 目录层级 = Channel / Course / Lesson | 与下载模块的 `<provider>/<channel>/<playlist>` 命名习惯一致，用户无需额外解释 |
| 不做转码 | 下载模块已按需选清晰度；转码属独立能力，避免耦合 |

## 5. 与 Media Download Center 的衔接

下载模块的根目录可配置（`MEDIA_ROOT`，默认 `/Media`）。
English 模块默认直接扫描媒体库根目录 `MEDIA_ROOT`（`ENGLISH_ROOT` 留空即复用，
文件不复制、不占第二份空间），
两者互不干扰；用户也可以把 English 根指向 `/Media/...` 的某个子目录。

## 6. 安全

| 点 | 措施 |
| --- | --- |
| 路径穿越 | 扫描/播放前一律 `realpath` 校验必须位于库根目录之内 |
| 未授权访问 | 所有接口 `CurrentUser` 依赖；客户端用 NASPilot 登录 token |
| 文件泄露 | 流式接口只允许访问 `lessons` 表里登记的路径，不接受任意路径参数 |
| 资源消耗 | 扫描在后台任务中执行，API 侧只读结果 |

## 7. 性能预估

| 规模 | 表现 |
| --- | --- |
| 1 万课时 | 扫描约数十秒（纯文件 stat + 少量解析），API 分页查询走索引为毫秒级 |
| 字幕解析 | srt/vtt 纯文本，单文件毫秒级 |
| 词汇提取 | 纯本地正则 + 词频，无需 AI；AI 仅用于释义 |

## 8. 失败模式

| 失败 | 处理 |
| --- | --- |
| 媒体根目录不存在 | 扫描返回明确提示，不创建空课程 |
| 字幕编码非 UTF-8 | 依次尝试 utf-8 / utf-8-sig / gbk，最终忽略非法字符 |
| 文件名重复 | 以绝对路径去重；同目录同名不同扩展名视为同一课时的不同资产 |
| 扫描中断 | 单文件异常只记录并跳过，不中断整轮扫描 |
| 扫描遇到写锁争用 | `rollback` 并提前返回（已入库的部分保留，下一轮继续），不会把整轮标记为失败 |
| AI 未配置 | 返回 400 + 指引，学习类接口不受影响 |

## 9. 自动扫描与数据库并发

自动扫描把"扫描"从用户动作变成了后台常态，因此这一节的约束必须维持。

### 9.1 后台调度

`services/english/watcher.py`：

- `start_watcher(interval)` 起一个轮询协程，每 `ENGLISH_AUTO_SCAN_SECONDS` 秒
  比较 `scanner.fingerprint()`，有变化才扫描。
- `request_scan()` 供下载完成等事件"立即扫一次"，永不抛异常、永不阻塞调用方；
  扫描进行中时只置一个"待重跑"标记，结束后自动补跑一轮。
- 全进程同时只允许一次扫描（`scanner._SCAN_LOCK`），手动 API 与后台共用同一把锁。

### 9.2 SQLite 写锁（踩过的坑，务必保留）

| 措施 | 原因 |
| --- | --- |
| `journal_mode=WAL` | 读不阻塞写，写不阻塞读 |
| `busy_timeout=15000` | 抢不到锁时等待而不是立刻失败 |
| **每条事务 `BEGIN IMMEDIATE`** | **关键**。先读后写的事务在 WAL 下需要"升级快照"；只要期间别的连接提交过，SQLite 会**立刻**返回 `database is locked` 且**完全不理会 `busy_timeout`**。先拿写锁就没有升级，争用退化为有界等待 |
| 一个课时一个短事务 | 解析字幕 / ffprobe / 分类都在第一条 SQL 之前完成，写锁只握毫秒级 |
| 聚合阶段按课程提交 | 难度聚合、词表重算不会跨课程占锁 |

实测（398 课时，Windows bind mount）：全量重扫期间并行 80 次登录 —— 0 失败、
0 次 `database is locked`；空闲扫描 ~10s（其中大部分是 bind mount 的 `stat` 延迟）。

### 9.3 不要挡住事件循环

词表打分（`vocabulary.extract_new_words`）在 398 课时语料上要 ~55s 纯 Python，
而且**在事务内**执行会同时占住写锁。因此：

- 用 `asyncio.to_thread` 抛到工作线程；
- 计算前先 `await db.commit()` 释放写锁；
- 只对本次有变化的课程重算。

教训：曾经它直接在事件循环里跑，导致整进程冻结 51 秒，任何 HTTP 请求（包括登录）
都只能等到超时 —— 表现为"500 / 请求超时"，很容易被误判成数据库锁问题。
诊断方法：用 20ms 间隔采样 `asyncio.sleep` 的实际延迟，p95 正常而 max 达数十秒，
即说明有同步 CPU 工作堵在循环上。
