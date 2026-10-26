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
├── Media Download Center  ← 上游：把资源下载到 /English
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
Media Download Center ──▶ /English/<Channel>/<Course>/<Lesson>.mp4|.mp3
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
English 模块根目录独立配置 `ENGLISH_ROOT`（默认 `/English`），
两者互不干扰；用户也可以把 English 根指向 `/Media/...` 的某个子目录。

## 6. 安全

| 点 | 措施 |
| --- | --- |
| 路径穿越 | 扫描/播放前一律 `realpath` 校验必须位于 `ENGLISH_ROOT` 之内 |
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
| `/English` 不存在 | 扫描返回明确提示，不创建空课程 |
| 字幕编码非 UTF-8 | 依次尝试 utf-8 / utf-8-sig / gbk，最终忽略非法字符 |
| 文件名重复 | 以绝对路径去重；同目录同名不同扩展名视为同一课时的不同资产 |
| 扫描中断 | 单文件异常只记录并跳过，不中断整轮扫描 |
| AI 未配置 | 返回 400 + 指引，学习类接口不受影响 |
