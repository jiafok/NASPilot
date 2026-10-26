# 媒体订阅中心 — YouTube 自动订阅下载

> NASPilot 内置的 TubeSync 轻量替代：输入频道 / 播放列表 URL，后台每 6 小时增量同步，
> 自动下载视频（MP4）、音频（MP3）或两者、英文字幕（SRT）与封面（JPG）。

## 1. 架构

```
APScheduler（每 6h / 手动触发）
        │
        ▼
YoutubeDownloadJob.sync_subscription()
        │  1. YouTubeProvider.list_items()      → yt-dlp --flat-playlist --dump-single-json
        │  2. 读取 youtube_download_history     → 已知 video_id 集合
        │  3. 过滤（严格按 YouTube Video ID，绝不看文件名）
        │  4. 逐个下载 + 实时进度              → youtube_download_tasks
        │  5. 写入历史                          → youtube_download_history
        │  6. 更新 last_check_time
        ▼
yt-dlp（subprocess）+ ffmpeg
```

统一订阅接口（`backend/app/services/media/`）：

| 类 | 说明 |
| --- | --- |
| `SubscriptionProvider` | 抽象基类：`list_items()` / `get_new_items()` / `download()` |
| `YouTubeProvider` | 频道、播放列表（含 `/playlists` 自动展开一层） |
| `BilibiliProvider` / `PodcastProvider` / `XimalayaProvider` / `RssProvider` | 预留实现，接入时只需补 `list_items` / `download` |

新增订阅源：实现接口后用 `@register_provider("type")` 注册，调度器、队列、历史表与 UI 无需改动。

## 2. 数据库

| 表 | 关键字段 |
| --- | --- |
| `youtube_subscriptions` | `name` `url` `type` `enabled` `media_mode` `video_height` `audio_format` `audio_only`(legacy) `download_path` `last_check_time` |
| `youtube_download_history` | `video_id`（唯一索引）`title` `channel` `playlist` `publish_date` `download_time` `file_path` `file_size` `status` |
| `youtube_download_tasks` | `video_id` `status` `progress` `speed` `downloaded_bytes` `total_bytes` `eta` `error_message` |

`video_id` 是唯一的去重依据；删除订阅时历史记录保留（`subscription_id` 置空）。

## 3. 下载规格

视频模式（默认）：

```
--write-auto-sub --sub-langs en.* --convert-subs srt
--write-thumbnail --convert-thumbnails jpg
-f "bestvideo[height<=480]+bestaudio/best[height<=480]"
--merge-output-format mp4
```

三种输出模式（`media_mode`，UI 为「下载格式」下拉）：

| 值 | 主文件 | yt-dlp 参数 | 目标根目录 |
| --- | --- | --- | --- |
| `video`（默认） | `.mp4` | `-f bestvideo[height<=480]+bestaudio/best[height<=480]/best` + `--merge-output-format mp4` | `/media/Youtube` |
| `audio` | `.mp3` | `-f bestaudio/best` + `-x --audio-format mp3 --audio-quality 0` | `/media/Audio` |
| `both` | `.mp4` + 同名 `.mp3` | 同 `video`，成功后再用 ffmpeg 就地转出 MP3 | `/media/Youtube` |

`both` 的实现细节：yt-dlp 无法在一次运行中同时保留 MP4 与 MP3（`-x` 会删掉源文件），
因此先下载 MP4，再由 `ytdlp.extract_mp3()` 调用
`ffmpeg -i x.mp4 -vn -acodec libmp3lame -q:a 0 x.mp3` 生成同名 MP3。
整个过程只请求一次网络，音频流与 MP4 完全一致，磁盘额外占用约为 MP4 的 1/10。

### 清晰度与音频格式

每个订阅可单独指定（留空/0 = 跟随全局环境变量）：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `video_height` | `0`=跟随全局(`YOUTUBE_MAX_HEIGHT`，默认 480) / `480` / `720` / `1080` / `1440` / `2160` | 格式选择器 `bestvideo[height<=H]+bestaudio/best[height<=H]/best`；0 表示不限高 `bestvideo+bestaudio/best` |
| `audio_format` | `mp3`（默认）/ `best` / `opus` / `m4a` / `flac` / `wav` | 见下表 |

**关于「无损」**：YouTube **从不提供无损音源**，最高只有 Opus ~160kbps 或 AAC ~128kbps，
因此真正的 lossless（CD/FLAC 级）无法获得。能达到的最高保真只有「零转码」：

| `audio_format` | 行为 | 音质 |
| --- | --- | --- |
| `mp3` | ffmpeg `libmp3lame -q:a 0`（VBR ≈245kbps） | 二次有损压缩（兼容性最好） |
| `best` | `-c:a copy`，按源编解码选容器（aac→`.m4a`、opus→`.opus`） | **与源逐字节一致，零损失** ✅ |
| `opus` / `m4a` | 源编码匹配时 `copy`，否则转码 | 通常零损失 |
| `flac` / `wav` | 一定转码（体积 ×5~10） | **并非真无损**，只是换了无损容器 |

yt-dlp 内部判定依据（`postprocessor/ffmpeg.py`）：目标格式 == 源 codec 时走
`-acodec copy`，否则才真正转码。本项目的 `both` 模式复用同一判定
（`ytdlp.extract_audio()` 先 ffprobe 源音频 codec 再决定 copy / 转码）。

真实行为实测（源分别为 AAC / Opus）：

```
aac-in-mp4     best -> .m4a  18,764B  copy     mp3 -> .mp3  20,087B  transcode
opus-in-webm   best -> .opus 18,259B  copy     mp3 -> .mp3  23,444B  transcode
```

兼容性：旧字段 `audio_only` 仍被接受并自动映射
（`true` → `media_mode=audio`，`false` → `video`），数据库中始终保持
`audio_only == (media_mode == "audio")`；升级时会把历史数据回填一次。

目录结构（`<下载根>/<播放列表或频道名>/<原始视频标题>.<ext>`，**不改名**）：

```
/media/Youtube/English Podcast/English Lesson 001 - Greetings.mp4
/media/Youtube/English Podcast/English Lesson 001 - Greetings.en.srt
/media/Youtube/English Podcast/English Lesson 001 - Greetings.jpg
/media/Audio/English Podcast/English Lesson 001 - Greetings.mp3
```

`both` 模式下 MP4 与 MP3 并列在同一目录：

```
/media/Youtube/English Podcast/English Lesson 001 - Greetings.mp4
（`audio_format=best` 时副产物为 `.m4a` / `.opus`，取决于 YouTube 提供的音轨）
/media/Youtube/English Podcast/English Lesson 001 - Greetings.en.srt
/media/Youtube/English Podcast/English Lesson 001 - Greetings.jpg
```

* 输出模板：`%(title)s.%(ext)s` —— 文件名就是 YouTube 原始标题。
* 字幕 / 封面 / 音频都沿用同一个基名（`--write-auto-sub`、`--write-thumbnail`、`-x`
  都以输出模板为前缀），因此三者永远与视频同名。
* 仅当标题超过 180 个字符时由 yt-dlp 截断（`--trim-filenames 180`），避免 NAS 文件名超长报错。

## 4. REST API

前缀：`/api/v1/youtube`（同时提供无版本别名 `/api/youtube`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/subscriptions` | 订阅列表（含 `download_count`、`status`） |
| POST | `/subscriptions` | 新增订阅 |
| PUT | `/subscriptions/{id}` | 编辑订阅 |
| DELETE | `/subscriptions/{id}` | 删除订阅（保留历史） |
| POST | `/subscriptions/{id}/toggle` | 启用 / 禁用 |
| POST | `/sync/{id}` | 立即同步（后台队列） |
| POST | `/sync-all` | 同步所有启用的订阅 |
| GET | `/tasks` | 下载任务（`status` / `active_only` / `limit`） |
| DELETE | `/tasks` | 清除已结束任务 |
| GET | `/history` | 下载历史（`search` / `status` / 分页） |
| DELETE | `/history/{id}` | 删除记录（`?delete_file=true` 同时删文件） |
| POST | `/history/{id}/redownload` | 重新下载 |
| GET | `/types` | 支持的订阅源 |
| GET | `/health` | yt-dlp / ffmpeg 状态 |
| GET | `/health/ytdlp`（根路径） | 同上的健康探针 |

示例：

```bash
curl -X POST http://nas:8082/api/v1/youtube/subscriptions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"Mr English Channel","url":"https://www.youtube.com/@Mr.EnglishChannel","type":"channel"}'

curl -X POST http://nas:8082/api/v1/youtube/sync/1 -H "Authorization: Bearer $TOKEN"
```

## 5. UI

一级菜单 **媒体中心 → YouTube 订阅**（`/media/youtube`）：

* **订阅列表**：名称 / URL / 类型 / 最近检查 / 下载总数 / 状态，支持新增、编辑、删除、启用禁用、立即同步。
* **下载任务**：等待中 / 下载中 / 处理中 / 已完成 / 失败，2 秒轮询显示进度、网速、已下载大小、剩余时间。
* **下载历史**：搜索、删除记录、重新下载、打开目录（跳转 `/files?path=...`）。

## 6. 依赖与 Docker

* 镜像构建时安装 `ffmpeg` 并 `pip install --upgrade yt-dlp`（yt-dlp 不锁版本，YouTube 提取器修复很频繁）。
* 应用启动时 `ensure_dependencies()` 再次检测；缺少 yt-dlp 时会尝试 `pip install yt-dlp`（容器内通常无需，因为镜像已装）。
* ffmpeg 用于合并音视频、字幕/封面转换与 MP3 提取，必须存在，否则 `/health/ytdlp` 返回 `degraded`。

媒体目录挂载（`docker-compose.yml`）：

```yaml
volumes:
  - ${NAS_MEDIA_VIDEO:-./data/media/Youtube}:/media/Youtube
  - ${NAS_MEDIA_AUDIO:-./data/media/Audio}:/media/Audio
```

Synology 示例：`- /volume1/media/Youtube:/media/Youtube`。

## 7. 常用环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `YOUTUBE_VIDEO_DIR` | `/media/Youtube` | 视频根目录 |
| `YOUTUBE_AUDIO_DIR` | `/media/Audio` | 音频根目录 |
| `YOUTUBE_SYNC_INTERVAL_HOURS` | `6` | 自动检查间隔 |
| `YOUTUBE_MAX_PER_SYNC` | `20` | 每次同步最多下载数（保护磁盘） |
| `YOUTUBE_SCAN_LIMIT` | `100` | 每次扫描条目上限 |
| `YOUTUBE_SUB_LANGS` | `en.*` | 字幕语言 |
| `YOUTUBE_MAX_HEIGHT` | `480` | 最高分辨率 |
| `YOUTUBE_COOKIES_FILE` | 空 | yt-dlp `--cookies` 文件 |
| `YOUTUBE_PROXY` | 空 | yt-dlp `--proxy` |
| `YOUTUBE_JS_RUNTIMES` | 空 | 例如 `node`（新版 YouTube 提取建议） |

## 8. 网络与代理

YouTube 的**视频 CDN（`googlevideo.com`）**与网页/API 是不同域名，很多网络环境
「能打开 YouTube 网页、能列出视频清单，但一下载就 `SSL: UNEXPECTED_EOF_WHILE_READING`」。

先自检：

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://www.youtube.com/          # API → 200 表示通
curl -s -o /dev/null -w '%{http_code}\n' https://redirector.googlevideo.com/  # CDN → 000 表示被阻断
```

或在 UI 中点击 **网络检测**（等价 `GET /api/v1/youtube/net-test`），返回示例：

```json
{"ok": false, "proxy": null,
 "checks": {"youtube_api": {"reachable": true, "http_status": 200},
            "youtube_cdn": {"reachable": false, "http_status": null}},
 "detail": "只能访问 YouTube API，无法访问视频 CDN（googlevideo.com）：..."}
```

### 配置代理

优先级：`YOUTUBE_PROXY` 环境变量 → `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`。
任一方式都会被 `yt-dlp --proxy` 采用（`ytdlp.proxy_url()`）。

`docker-compose.dev.yml`：

```yaml
environment:
  - YOUTUBE_PROXY=http://host.docker.internal:7890   # 宿主机上的代理
  # 或让所有工具都走代理：
  # - HTTPS_PROXY=http://host.docker.internal:7890
```

然后 `docker compose -f docker-compose.dev.yml up -d`，再点 **网络检测** 确认
`CDN: ✅`。

常见取值：

| 代理所在位置 | 填什么 |
| --- | --- |
| 本机 Clash/V2Ray/Surge（需开启「允许局域网连接」） | `http://host.docker.internal:<端口>` |
| 局域网另一台设备 / NAS / 软路由 | `http://192.168.x.x:<端口>` |
| 远程 VPS 上的 HTTP 代理 | `http://<公网IP>:<端口>` |
| SOCKS5 | `socks5://host.docker.internal:<端口>`（yt-dlp 支持） |

注意：容器若使用 `network_mode: host`，则应填 `http://127.0.0.1:<端口>`。

## 9. 排障

1. `GET /health/ytdlp` 查看版本与状态。
2. 同步无新内容：确认订阅 URL 可被 yt-dlp 访问（`/videos`、`/playlists`、`watch?v=...&list=...`）。
3. 需要登录 / 年龄限制：挂载 cookies 文件并设置 `YOUTUBE_COOKIES_FILE`。
4. 403 / 格式缺失：设置 `YOUTUBE_JS_RUNTIMES=node`（镜像自带 nodejs）或更新 yt-dlp。
5. 首次订阅会按“最新在前”顺序下载，单次上限 `YOUTUBE_MAX_PER_SYNC`，后续同步继续补齐更早内容。
