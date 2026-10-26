# English Learning Center — PRD

状态：**Review Gate 1（PRD Review）**
版本：1.0
归属：NASPilot 内部模块（非独立服务端）

---

## 1. 定位

NASPilot 是唯一服务端。英语学习能力以**模块**形式内置于 NASPilot，
EnglishFlow（Flutter）只是客户端。

```
EnglishFlow APP  ──HTTP──▶  NASPilot
    UI / 播放 / 缓存          资源 / 元数据 / 字幕 / 词汇 / 进度 / AI / 流媒体
```

## 2. 必须遵守的边界

| 允许 | 禁止 |
| --- | --- |
| 在 NASPilot 内新增 `services/english/` 模块 | 新建 EnglishFlow Backend |
| 复用 `users` / `settings` / `ai` / 任务系统 | 新建独立数据库 |
| 复用 Media Download Center 的下载结果 | 重新实现 OpenAI/Claude/Ollama 等 |
| 新增 `/api/english/*` 开放接口 | APP 直连 SMB / NFS / WebDAV / volume1 |
| 新增 `media stream` 接口 | APP 感知任何 NAS 概念 |

## 3. 用户故事

| # | 角色 | 故事 |
| --- | --- | --- |
| U1 | NAS 管理者 | 媒体中心下载好的音视频自动成为课时，点一次扫描就能在网页上看到课程列表 |
| U2 | 学习者 | 我在 APP 上看到首页推荐，能直接播放并看到同步字幕 |
| U3 | 学习者 | 我点字幕某一句可以直接跳到对应时间 |
| U4 | 学习者 | 我能收藏课程、给某一句打书签 |
| U5 | 学习者 | 我问 AI 老师「这句话为什么用现在完成时」，它结合字幕回答 |
| U6 | 学习者 | 我能看到自己的学习时长、完成课程数、生词本 |

## 4. 资源来源

资源**只能**来自 NASPilot 已有能力（Media Download Center）下载到
媒体库（`/Media`）的结果——含媒体中心下载的文件，以及用户手动放入该目录的文件。客户端不负责获取资源。

## 5. 功能范围（MVP = 阶段 1 + 部分阶段 2）

### 阶段 1（本次交付）
- 资源扫描：识别 mp4/mp3/srt/vtt/jpg/png/webp，构建 Channel → Course → Lesson
- 元数据引擎：分类、难度、时长、封面、标签
- 字幕引擎：解析 srt/vtt → 句子 + 时间戳 + 搜索文本
- 媒体流：MP4/MP3/字幕 HTTP 流（支持 Range）
- 开放 API：首页 / 课程列表 / 课程详情 / 课时详情 / 字幕 / 词汇
- Web 管理页：扫描、课程浏览、课时播放 + 字幕 + 生词

### 阶段 2（本次交付的部分）
- 学习进度、收藏、书签、学习统计
- AI 老师：课程总结 / 逐句讲解 / FAQ 问答 / 生词提取

### 阶段 3（不在本次范围，仅预留）
- 学习路径、推荐排序、发音评分、AI 跟读

## 6. 验收标准

| # | 场景 | 期望 |
| --- | --- | --- |
| A1 | 目录 `Mr English Channel/Business English/Lesson 001.mp4 + .srt` | 生成 1 channel / 1 course / 1 lesson，字幕可解析出句子 |
| A2 | 重复扫描同一目录 | 不产生重复 course/lesson（按文件路径去重） |
| A3 | 文件被删除后重新扫描 | 对应 lesson 标记为 `missing`，不再出现在课程列表 |
| A4 | 无字幕的课程 | 课程可用，字幕接口返回空数组而非报错 |
| A5 | 播放接口 | 支持 HTTP Range，Flutter 可拖动进度 |
| A6 | 客户端无 NAS 凭据 | 仅凭 NASPilot 登录态即可完成全部读取 |
| A7 | AI 未配置 | AI 接口返回 400 并给出明确提示，其他接口不受影响 |

## 7. 非目标

- 不做付费/会员体系
- 不做社交、排行榜
- 不做视频转码（沿用下载好的文件；如需其它码率交给 ffmpeg 单独任务）
- 不做词典订阅（生词释义由 AI 生成）
