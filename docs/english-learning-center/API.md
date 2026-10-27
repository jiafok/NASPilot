# English Learning Center — API 评审 + Resource Import Specification

状态：**Review Gate 4（API Review）**

本文件替代并合并原先分散的
`NASPilot Integration Contract` / `Course API Contract` /
`Lesson Sync Contract` / `Playback Ticket Contract` —— 以上四份**作废**。
客户端只需理解：一个导入规范 + 一组 REST 接口。

---

# Part A — Resource Import Specification

## A.1 根目录

```
ENGLISH_ROOT（默认 = 媒体库根目录 <DATA_DIR>/media，即直接复用媒体中心下载的文件）
├── Video/            ← 媒体中心视频输出（<provider>/<channel>/<playlist>/）
├── Audio/            ← 媒体中心音频输出
├── Business English/
├── Daily English/
├── Podcast/
├── TED/
├── BBC/
└── Mr English Channel/
```

> 英语学习中心**不要求单独的目录**：媒体中心下载完成的文件会直接出现在课程
> 列表里。顶层若存在 `Video/` 或 `Audio/`，这一层只表示「视频/音频输出」，
> 不参与频道/课程命名。

## A.2 层级推导规则（Resource Scanner）

以 `ENGLISH_ROOT` 为基准（先剥掉顶层的 `Video/`、`Audio/`），按相对深度推导：

| 相对深度 | 目录 → | 文件 → |
| --- | --- | --- |
| 1 级目录 | **Channel** | 直接位于根下的媒体 → 课程「未分类」，频道「默认」 |
| 2 级目录 | **Course** | 媒体 → 该 Course 的 Lesson |
| 3 级及以上 | 归入最近的 2 级 Course，更深目录名并入 Lesson 标题前缀 |

`videos` / `streams` / `shorts` / `playlist` 这类容器目录不计入标题前缀。

示例：

```
data/media/Video/youtube/Mr English Channel/Lesson 001.mp4
  → channel = "youtube"          （媒体中心布局：provider = 频道）
  → course  = "Mr English Channel"
  → lesson  = "Lesson 001"

data/media/Mr English Channel/Business English/Lesson 001.mp4
  → channel = "Mr English Channel"
  → course  = "Business English"
  → lesson  = "Lesson 001"

data/media/TED/How to speak.mp3
  → channel = "TED"
  → course  = "TED"          （1 级目录时 course 取同名）
  → lesson  = "How to speak"

data/media/loose.mp4
  → channel = "默认"
  → course  = "未分类"
```

## A.3 识别的文件类型

| 类型 | 扩展名 | 处理 |
| --- | --- | --- |
| 视频 | `.mp4` `.mkv` `.webm` `.mov` `.m4v` | 作为一个 Lesson（`media_type=video`） |
| 音频 | `.mp3` `.m4a` `.aac` `.flac` `.wav` `.ogg` `.opus` | 作为一个 Lesson（`media_type=audio`） |
| 字幕 | `.srt` `.vtt` | 按同名基名绑定；支持 yt-dlp 的语言后缀 `<name>.en.srt` / `<name>.zh-Hans.vtt`，英文优先、`.srt` 优先 |
| 说话人 | `>>` 标记 | 自动字幕用它表示「换人说话」，解析时切分为 A / B 两个轮次 |
| 图片 | `.jpg` `.jpeg` `.png` `.webp` | 同名绑定为 Lesson 封面；目录内第一张作为 Course 封面 |
| 忽略 | 其它 | 跳过 |

## A.3.1 时长

有字幕时取字幕最后一句的时间；没有字幕时用镜像内已有的 `ffprobe` 读取
容器时长并缓存到 `lessons.duration`，文件未变动就不会重复探测。

## A.3.2 字幕归一化

自动字幕（YouTube / Podcast）是**滚动式**的：几乎每个 cue 都重复上一句的尾部，还夹杂
10 毫秒的过渡空帧和 `[music]` 噪音。直接存进库会出现「同一句显示两三次」和「两个人说的话
黏成一段」。解析时因此做四步归一化：

| 步骤 | 作用 |
| --- | --- |
| 丢帧 | 丢弃 `end - start < 120ms` 的空帧与纯音效标签 |
| 去前缀 | 去掉每条 cue 从上一句重复过来的词 |
| 切人 | 在 `>>` 处断开，保证**同一个人的连续台词留在同一块**，不同人分开 |
| 合句 | 合并到真正的句子结束（排除 `Mr.` 等缩写），过长时按上限截断 |
| 去重 | 与上一块完全相同的块直接丢弃 |

输出结构：

```json
{ "i": 0, "start": 19880, "end": 34870, "text": "Hello Emily and hello to all ...", "spk": "A" }
```

`spk` 只表示「轮换」，因为自动字幕只告诉我们「换人了」，并不知道是谁；
无 `>>` 标记的材料（独白）`spk` 为 `null`。

## A.4 同名绑定

同一目录中基名相同的多个文件视为**同一课时**：

```
Lesson 001.mp4
Lesson 001.srt
Lesson 001.jpg
```

→ 1 个 lesson（`file_path` 取媒体文件），`subtitle_path` / `cover_path` 分别绑定。
若同时存在 `.srt` 与 `.vtt`，优先 `.srt`。

## A.5 可选元数据覆盖

课程目录下放 `course.json` 可覆盖自动推导：

```json
{
  "title": "Business English 进阶",
  "category": "Business",
  "difficulty": "intermediate",
  "description": "面向职场的商务英语",
  "tags": ["business", "meeting"],
  "cover": "cover.jpg"
}
```

`course.json` 缺失或字段非法时全部回退到自动推导，不影响扫描。

## A.6 幂等与删除

- 唯一键：媒体文件绝对路径。
- 重复扫描：更新 title/duration/size/mtime，不新增行。
- 文件消失：`lessons.status = "missing"`，默认不出现在课程列表
  （可用 `include_missing=1` 查看）。
- 不自动删除行，避免误删用户进度。

## A.7 触发扫描

| 方式 | 说明 |
| --- | --- |
| Web 页「扫描」按钮 | `POST /api/v1/english/admin/scan` |
| API | 同上 |
| 定时 | 沿用 NASPilot 调度器，可选开启（默认关闭） |

---

# Part B — Learning Open API

基址：`/api/v1/english`，同时以 `/api/english` 暴露（客户端契约以此为准）。

所有接口需要 NASPilot 登录 token：`Authorization: Bearer <token>`。

## B.1 读接口

### `GET /api/english/home`
首页聚合。

```json
{
  "greeting": "晚上好",
  "stats": {"courses": 6, "lessons": 128, "completed": 12, "minutes_today": 25},
  "continue_learning": [{"lesson_id": 9, "lesson_title": "…", "course_title": "…", "position": 132.5, "progress": 0.42}],
  "recent": [{"course_id": 1, "title": "…", "channel": "…", "cover": "…"}],
  "recommended": [ … ]
}
```

### `GET /api/english/courses`
查询参数：`q`、`channel`、`category`、`difficulty`、`favorite`(0/1)、
`sort`(`recent|title|lessons`)、`page`、`page_size`。

```json
{"items": [{"id":1,"title":"…","channel":"…","category":"Business","difficulty":"intermediate","lesson_count":12,"total_seconds":3600,"cover":"/api/stream/cover/1"}], "total": 6, "page": 1, "page_size": 20}
```

### `GET /api/english/course/{id}`
课程详情 + 课时列表。

```json
{"id":1,"title":"…","channel":"…","category":"…","difficulty":"…","description":"…","tags":[],"lesson_count":12,"total_seconds":3600,"is_favorite":true,"lessons":[{"id":9,"title":"…","position":1,"duration":312,"media_type":"video","has_subtitle":true,"progress":0.42,"completed":false}]}
```

### `GET /api/english/lesson/{id}`
课时详情：播放地址、字幕地址、词汇。

```json
{"id":9,"course_id":1,"title":"…","media_type":"video","duration":312,"stream_url":"/api/media/9","subtitle_url":"/api/subtitle/9","has_subtitle":true,"vocabulary":[{"id":3,"word":"negotiate","kind":"word","frequency":4}],"bookmarks":[{"position":12,"at_seconds":61.2,"note":"…"}],"progress":{"position":12.0,"completed":false}}
```

### `GET /api/english/transcript/{id}`
`{id}` 为 **lesson id**。返回字幕句子。

```json
{"lesson_id":9,"language":"en","segment_count":88,"segments":[{"i":0,"start":0,"end":2400,"text":"Hello everyone."}]}
```

`start/end` 单位：**毫秒**。

### `GET /api/english/vocabulary/{id}`
`{id}` 为 **course id**。返回课程生词表（按频率降序）。

```json
{"course_id":1,"items":[{"id":3,"word":"negotiate","kind":"word","frequency":7,"difficulty":"intermediate","meaning":null}]}
```

## B.2 写接口

### `POST /api/english/progress`
```json
{"lesson_id": 9, "position": 132.5, "listen_seconds": 30, "completed": false}
```
→ `{"lesson_id":9,"position":132.5,"completed":false}`

### `POST /api/english/bookmark`
```json
{"lesson_id": 9, "position": 12, "at_seconds": 61.2, "note": "重要", "text": "…"}
```
→ 书签对象。同 `(lesson_id, position)` 视为更新。

### `POST /api/english/favorite`
```json
{"course_id": 1, "favorite": true}
```
→ `{"course_id":1,"favorite":true}`

## B.3 AI 接口（复用 NASPilot AI Center）

### `POST /api/english/ai/chat`
```json
{"lesson_id": 9, "question": "第 12 句为什么用现在完成时？"}
```
→ `{"answer":"…","context_used":["transcript"]}`

### `POST /api/english/ai/explain`
```json
{"lesson_id": 9, "position": 12}
```
→ `{"answer":"…","sentence":"…"}` 逐句语法/用法讲解

### `POST /api/english/ai/summary`
```json
{"lesson_id": 9, "language": "zh"}
```
→ `{"summary":"…"}` 课程/课时总结

AI 未配置时统一返回：
```json
{"detail": "AI 未配置。请在「系统设置 → AI」填写 API Key。"}   // HTTP 400
```

## B.4 管理接口（Web 后台用）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/english/admin/scan` | 触发扫描，返回统计 |
| GET | `/api/v1/english/admin/stats` | 目录存在性、课程/课时/字幕计数 |
| POST | `/api/v1/english/admin/vocabulary/{course_id}` | 重新提取生词 |
| GET | `/api/v1/english/directories` | 当前 ENGLISH_ROOT 与来源 |

## B.5 媒体流

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/media/{lesson_id}` | 播放媒体文件，支持 `Range`（客户端契约） |
| GET | `/api/subtitle/{lesson_id}` | 字幕原文（`text/plain; charset=utf-8`） |
| GET | `/api/stream/cover/{course_id}` | 课程封面图 |

**命名冲突处理（已验证）**

既有的 Media Download Center 已占用 `/api/media/*`（`/api/media/subscriptions`、
`/api/media/providers`、`/api/media/detect` …）。因此：

1. 流式路由的路径参数声明为 `int`，且**在媒体中心路由之后**注册 ——
   字面量路由先匹配，数字才会落到流式路由。
2. 提供 `/api/stream/media/{id}`、`/api/stream/subtitle/{id}`、
   `/api/stream/cover/{id}` 作为无歧义别名，内部实现完全一致。
3. 封面 URL 一律使用 `/api/stream/cover/{id}`，避免 `cover` 被当成
   非数字 lesson id。

## B.6 错误约定

| 状态码 | 含义 |
| --- | --- |
| 400 | 参数错误 / AI 未配置 |
| 401 | 未登录 |
| 403 | 路径越界 |
| 404 | 课程/课时不存在 |
| 416 | Range 超出文件范围 |
