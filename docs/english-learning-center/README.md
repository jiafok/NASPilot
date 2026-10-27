# English Learning Center（NASPilot 内部模块）

英语学习能力的**唯一服务端实现**。EnglishFlow（Flutter）只是客户端，
不拥有独立后端 / 数据库 / AI 服务。

## 评审文档（Review Gate）

| # | 门 | 文件 |
| --- | --- | --- |
| 1 | PRD Review | [PRD.md](PRD.md) |
| 2 | Architecture Review | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 3 | Database Review | [DATABASE.md](DATABASE.md) |
| 4 | API Review + Resource Import Specification | [API.md](API.md) |
| 5 | UI Review | [UI.md](UI.md) |

## 模块位置

```
NASPilot
├── System / Docker / File Manager / AI Center / Media Download Center
└── English Learning Center
    ├── Resource Scanner      services/english/scanner.py
    ├── Metadata Engine       services/english/metadata.py
    ├── Transcript Engine     services/english/subtitles.py
    ├── Vocabulary Engine     services/english/vocabulary.py
    ├── Learning Progress     services/english/query.py
    ├── AI Tutor              services/ai_service.py（共用）
    ├── Media Stream Service  services/english/stream.py
    └── Learning Open API     api/v1/english.py
```

## 快速开始

1. 不需要单独建目录——**直接复用媒体库**（`data/media`，容器内 `/app/data/media`）。媒体中心下载完成的文件
   已经在 `/Media/Video`、`/Media/Audio` 下，扫描即成为课时：

```
data/media/                              ← 媒体根目录
├── Video/                               ← 媒体中心输出（provider/channel/…）
│   └── youtube/Mr English Channel/
│       ├── Lesson 001.mp4
│       └── Lesson 001.srt               ← 同名自动绑定字幕
└── Audio/
    └── TED/How to speak.mp3
```

也可以自己按 `<频道>/<课程>/<课时>.mp4` 放进去：

```
/Media/
└── Mr English Channel/          ← Channel
    └── Business English/        ← Course
        ├── 001 - Greetings.mp4  ← Lesson
        └── 002 - Negotiating.mp3
```

2. 网页「英语学习 → 扫描媒体库」
3. 课程卡片 → 课时 → 播放（字幕可点击跳转）

可选：课程目录放 `course.json` 覆盖标题/分类/难度/标签。

## 客户端接口

Flutter 侧按 `docs/english-learning-center/API.md` Part B 调用，
基址 `/api/english/*`，媒体走 `/api/media/{lessonId}`（支持 Range）。

**禁止**客户端出现：下载 / 媒体库 / 文件 / 目录 / 资源管理。
