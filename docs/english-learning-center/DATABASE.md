# English Learning Center — 数据库评审

状态：**Review Gate 3（Database Review）**

复用 NASPilot 现有 SQLite（`DATABASE_URL`），**不新建数据库**。
所有新表通过 `Base.metadata.create_all` 在启动时创建，
不修改任何既有表。

---

## 1. 表清单（8 张，与需求一致）

| 表 | 作用 | 关键索引 |
| --- | --- | --- |
| `courses` | 课程 | `(channel, slug)` 唯一 |
| `lessons` | 课时 | `file_path` 唯一；`course_id`、`published_at` |
| `transcripts` | 字幕（按课时 1:1） | `lesson_id` 唯一；`search_text` |
| `vocabulary` | 生词/短语 | `(course_id, word)` 唯一 |
| `user_progress` | 学习进度（按用户×课时） | `(user_id, lesson_id)` 唯一 |
| `favorites` | 收藏 | `(user_id, course_id)` 唯一 |
| `bookmarks` | 句子书签 | `(user_id, lesson_id, position)` |
| `learning_statistics` | 按用户×日期聚合 | `(user_id, day)` 唯一 |

## 2. 字段定义

### courses
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| channel | str(191) | 频道名，来自目录一级 |
| title | str(255) | 课程名，来自目录二级（可被 `course.json` 覆盖） |
| slug | str(191) | `title` 归一化，用于 `(channel, slug)` 去重 |
| category | str(64) | 第一个话题（兼容旧客户端的单值字段） |
| difficulty | str(16) | beginner / intermediate / advanced（由 level 派生） |
| level | str(4) | CEFR 难度 A1..C1，**从字幕实测**（句长/生词比/语速） |
| topics | JSON | 多标签话题，如 `["日常","饮食"]` |
| skills | JSON | 技能侧重，如 `["听力","词汇"]` |
| format | str(24) | dialogue / interview / monologue / lecture / news / story / song |
| description | text | 来自 `course.json` 或留空 |
| cover_path | str(1024) | 封面图绝对路径 |
| lesson_count | int | 冗余计数，扫描时更新 |
| total_seconds | int | 总时长，扫描时更新 |
| source | str(64) | 来源 provider（youtube/podcast/…，可空） |
| tags | JSON | 标签数组 |
| created_at / updated_at | datetime | `TimestampMixin` |

### lessons
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| course_id | int FK → courses | `ON DELETE CASCADE` |
| title | str(255) | 文件名去扩展名 |
| file_path | str(1024) **unique** | 绝对路径，扫描幂等键 |
| media_type | str(16) | video / audio |
| media_ext | str(16) | mp4 / mp3 / … |
| subtitle_path | str(1024) | srt/vtt 绝对路径 |
| subtitle_format | str(8) | srt / vtt |
| cover_path | str(1024) | 同目录图片 |
| duration | int | 秒（来自字幕末尾或元数据） |
| size_bytes | bigint | |
| position | int | 课程内序号 |
| status | str(16) | ready / missing |
| published_at | datetime | mtime |
| created_at / updated_at | datetime | |

### transcripts
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| lesson_id | int FK → lessons **unique** | 1:1 |
| language | str(16) | en |
| segment_count | int | |
| segments | JSON | `[{i,start,end,text,spk}]`；`spk` 为说话人轮次（A/B），独白为 null |
| search_text | text | 所有句子拼接，供 LIKE 检索 |
| created_at / updated_at | datetime | |

### vocabulary
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| course_id | int FK → courses（可空） | |
| lesson_id | int FK → lessons（可空） | |
| word | str(64) | 单词或短语（短语含空格） |
| kind | str(16) | word / phrase |
| frequency | int | 出现次数 |
| meaning | text | AI 生成（可空） |
| example | text | 例句（可空） |
| difficulty | str(16) | 由词频/长度推断 |
| created_at / updated_at | datetime | |

### user_progress
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| user_id | int FK → users | |
| lesson_id | int FK → lessons | |
| position | float | 播放秒数 |
| completed | bool | |
| listen_seconds | int | 累计收听时长 |
| last_played_at | datetime | |
| created_at / updated_at | datetime | |
| **唯一** | `(user_id, lesson_id)` | |

### favorites
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| user_id | int FK → users | |
| course_id | int FK → courses | |
| **唯一** | `(user_id, course_id)` | |

### bookmarks
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| user_id | int FK → users | |
| lesson_id | int FK → lessons | |
| position | int | 句子序号 |
| at_seconds | float | 秒 |
| note | text | 用户备注 |
| text | text | 该句原文快照 |

### learning_statistics
| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | int PK | |
| user_id | int FK → users | |
| day | date(str 10) | `YYYY-MM-DD` |
| listen_seconds | int | 当日收听 |
| lessons_completed | int | 当日完成数 |
| words_learned | int | 当日生词 |
| **唯一** | `(user_id, day)` | |

## 3. 不变量

1. `lessons.file_path` 全局唯一 —— 扫描幂等的唯一依据。
2. 删除 `courses` 级联删除其 `lessons`，再级联 `transcripts`。
3. `user_progress` / `favorites` / `bookmarks` / `learning_statistics` 以
   `user_id` 隔离，所有查询必须带 `user_id` 条件。
4. `transcripts.segments` 的 `start/end` 单位统一为**毫秒整数**，避免浮点比较问题。
5. 所有业务表带 `created_at/updated_at`（`TimestampMixin`），便于排障。

## 4. 索引理由

| 索引 | 支撑的查询 |
| --- | --- |
| `courses(channel, slug)` unique | 扫描时的 upsert 去重 |
| `lessons(file_path)` unique | 扫描幂等 |
| `lessons(course_id, position)` | 课程详情按顺序取课时 |
| `transcripts(lesson_id)` unique | 播放页取字幕 |
| `vocabulary(course_id, word)` unique | 词汇 upsert |
| `user_progress(user_id, lesson_id)` unique | 进度上报 upsert |
| `learning_statistics(user_id, day)` unique | 统计聚合 upsert |

## 5. 迁移策略

- 全新表 → `create_all` 自动建，无需手写迁移。
- 未来若需给既有表加列，沿用仓库既有的
  `_add_column_if_missing()`（`core/database.py`）。
- **不触碰**任何既有表结构，保证旧数据库可直接升级。
