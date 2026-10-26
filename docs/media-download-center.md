# 媒体下载中心（Media Download Center）

> NASPilot 内置的统一媒体订阅 / 下载 / 管理平台：一个订阅表、一套下载队列、
> 一份历史归档，覆盖 YouTube、Bilibili、Podcast RSS、SoundCloud、Vimeo、Twitch、X
> 以及任何 yt-dlp 支持的站点。

```
┌────────────── UI（媒体中心）────────────────────────────────┐
│  媒体订阅       下载任务（实时）        历史记录            │
└───────┬───────────────┬──────────────────┬─────────────────┘
        │               │                  │
   /api/media/*  （REST + /api/v1 别名）     │
        │               │                  │
┌───────▼───────────────▼──────────────────▼─────────────────┐
│ MediaSyncJob → 发现 → 去重(provider+media_id) → 入队        │
│ MediaQueueManager（并发 / 暂停 / 继续 / 取消 / 重试 / 限速）│
│ SubscriptionProvider 注册表（每源一个实现，共享下载引擎）    │
│ yt-dlp + ffmpeg（subprocess，无浏览器自动化）                │
└────────────────────────────────────────────────────────────┘
```

## 1. 支持的媒体源

| provider | 来源 | 支持类型 | 说明 |
| --- | --- | --- | --- |
| `youtube` | YouTube | 视频 / Shorts / 播放列表 / 频道 | 频道自动补 `/videos` |
| `bilibili` | Bilibili | 单视频 / UP 主 / 合集 / 收藏夹 | 合集与收藏夹按播放列表处理 |
| `podcast` | Podcast RSS | 任意标准 RSS 2.0 / iTunes | 原生实现（feedparser + httpx），带断点续传与封面 |
| `soundcloud` | SoundCloud | 单曲 / 专辑 / 用户主页 | yt-dlp |
| `vimeo` | Vimeo | 视频 / 频道 / showcase | yt-dlp |
| `twitch` | Twitch | 视频 / 频道 | yt-dlp |
| `x` | X (Twitter) | 推文视频 | 多数内容需要 cookies |
| `ximalaya` | 喜马拉雅 | — | **预留**（需要签名算法） |
| `xiaoyuzhou` | 小宇宙 | — | **预留**（本质是 Podcast，可先用 RSS） |
| `custom` | 任意站点 | 视频 / 播放列表 / 频道 | 兜底 Provider，约 1800+ 站点 |

新增来源只需实现 `SubscriptionProvider` 并用 `@register_provider` 注册，
调度、队列、历史、UI 下拉会自动出现，无需改动其它层。

```python
@register_provider
class MyProvider(YtDlpProvider):        # 复用 yt-dlp 实现
    provider = "mysite"
    display_name = "My Site"
    url_patterns = ("mysite.com",)
```

## 2. 数据模型

| 表 | 关键字段 |
| --- | --- |
| `media_subscriptions` | `provider` `url` `type` `enabled` `media_mode` `video_height` `audio_format` `schedule_type` `cron_expr` `network_mode` `proxy_*` `download_path` `number_prefix` `last_check_time` `last_error` |
| `media_download_history` | **`(provider, media_id)` 唯一索引**、标题、频道、播放列表、发布时间、路径、大小、下载时间、状态 |
| `media_download_tasks` | `status`（pending/downloading/paused/processing/completed/failed/cancelled）`progress` `speed` `downloaded_bytes` `total_bytes` `eta` `error_message` `attempts` |

> **去重铁律**：只使用 provider 的 media id（`(provider, media_id)` 唯一索引），
> 从不使用文件名。重命名/移动文件不会触发重复下载。

## 3. 下载模式与规格

| `media_mode` | 主文件 | yt-dlp 参数 |
| --- | --- | --- |
| `video`（默认） | `.mp4` | `-f bestvideo[height<=H]+bestaudio/best[height<=H]/best` + `--merge-output-format mp4` |
| `audio` | `.mp3`（或所选格式） | `-f bestaudio/best` + `-x --audio-format <fmt>` |
| `both` | `.mp4` + 同名音频 | 同 `video`，完成后 ffmpeg 就地转出音频（不二次下载） |

* 视频清晰度：`480 / 720 / 1080 / 0=原画`
* 音频格式：`mp3`（有损）/ `best`（**零转码**，保留原始 Opus/AAC）/ `m4a` / `opus` / `flac`
* 字幕：`--write-auto-sub --sub-langs en.* --convert-subs srt`
* 封面：`--write-thumbnail --convert-thumbnails jpg`
* 断点续传：`--continue`（暂停后「继续」会接着上次进度）

## 4. 目录规则

```
/Media/Video/<provider>/<channel>/<playlist>/<title>.mp4
/Media/Audio/<provider>/<channel>/<playlist>/<title>.mp3
```

* 文件名默认使用**原始标题**；字幕 / 封面沿用同名（`title.en.srt`、`title.jpg`）。
* 勾选「文件名加序号」则输出 `001-title.ext`（序号来自播放列表位置）。
* 订阅设置了 `download_path` 时只替换根目录，`provider/channel/playlist` 仍会追加，
  避免不同来源互相覆盖。

## 5. 网络策略（每个订阅独立）

| `network_mode` | 行为 |
| --- | --- |
| `system` | 继承容器代理环境变量或全局 `MEDIA_PROXY_URL` |
| `direct` | 强制直连（传 `--proxy ""` 覆盖继承的代理） |
| `http` | 使用 `proxy_url`（`http://host:port`），支持 `proxy_username/password` |
| `socks5` | 使用 `proxy_url`（`socks5://host:port`），支持认证 |
| `docker_vpn` | 流量已由 VPN sidecar 接管（compose 中 `network_mode: container:gluetun`），不再设置代理 |

代理认证会被注入到 URL（`scheme://user:pass@host:port`）。
`network_mode=docker_vpn` 只是记录与校验，真正的网络接管需在 compose 里完成：

```yaml
services:
  naspilot:
    network_mode: "container:gluetun"
```

## 6. 调度与下载控制

* 调度：`manual / hourly / every_6h / every_12h / daily / cron`（APScheduler，重启后自动重建）
* 立即同步：`POST /api/media/sync/{id}`（后台执行，不阻塞请求）
* 任务控制：`POST /api/media/tasks/{id}/{pause|resume|cancel|retry}`
  * **暂停**：终止当前进程并保留已下载分片，状态 `paused`
  * **继续**：重新下发，`--continue` 断点续传
  * **取消**：终止并标记 `cancelled`（不写入历史）
  * **重试**：重置进度重新下载
* 并发：`MEDIA_MAX_CONCURRENT`（默认 2 个同时下载）
* 限速：每次订阅可设 `rate_limit`（如 `5M`、`500K`），透传 `--limit-rate`

## 7. REST API

前缀 `/api/v1/media`（同时提供 `/api/media` 别名）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/subscriptions` | 订阅列表（含 `download_count`、`status`） |
| POST | `/subscriptions` | 新增（校验 URL / 预留 provider / 代理必填项） |
| GET/PUT/DELETE | `/subscriptions/{id}` | 查看 / 编辑 / 删除（历史保留） |
| POST | `/subscriptions/{id}/toggle` | 启用 / 禁用 |
| POST | `/sync/{id}` · `/sync-all` | 立即同步 |
| GET | `/tasks` | 任务列表（`status` / `active_only` / `provider` / `limit`） |
| POST | `/tasks/{id}/{action}` | `pause` / `resume` / `cancel` / `retry` |
| DELETE | `/tasks` | 清除已结束任务 |
| GET | `/tasks/stats` | 队列统计（运行中 / 并发上限） |
| GET | `/history` | 历史（`search` / `provider` / `status` / 分页） |
| DELETE | `/history/{id}` | 删除记录（`?delete_file=true` 同时删文件） |
| POST | `/history/{id}/redownload` | 重新下载 |
| GET | `/history/{id}/links` | 媒体服务器深链 |
| GET | `/providers` · `/providers/simple` | 可用 Provider 元数据 |
| GET | `/detect?url=` | 从 URL 推断 provider / 类型 |
| GET | `/mediaservers` · `/directories` | 媒体服务器与建议目录 |
| GET | `/network-test` | API / CDN 连通性（走订阅同款代理解析） |
| GET | `/health` | 媒体栈健康 |

顶层健康探针：`GET /health/media`

```json
{
  "status": "ok",
  "ytdlp_version": "2026.10.21",
  "ffmpeg_version": "7.1.5",
  "providers": [{"provider": "youtube", "implemented": true, "...": "..."}],
  "network": {"ok": false, "detail": "只能访问 YouTube API，无法访问视频 CDN…"},
  "directories": {"video": "/Media/Video", "audio": "/Media/Audio"},
  "active_downloads": 1,
  "queued_tasks": 3
}
```

## 8. UI

一级菜单 **媒体中心** → 三个二级菜单（同一页面的三个视图，可直接收藏 URL）：

| 菜单 | 路径 | 内容 |
| --- | --- | --- |
| 媒体订阅 | `/media/subscriptions` | 名称 / 来源 / URL / 类型规格 / 调度 / 网络 / 已下载 / 最后更新 / 状态；新增、编辑、删除、启停、立即同步 |
| 下载任务 | `/media/tasks` | 等待中 / 下载中 / 暂停 / 处理中 / 完成 / 失败；实时进度、速度、剩余时间；暂停/继续/取消/重试；2 秒轮询 |
| 历史记录 | `/media/history` | 标题 / 来源 / 大小 / 下载时间 / 路径；搜索、重新下载、删除记录、打开目录 |

新增订阅时粘贴 URL 会**自动识别来源与类型**（`/media/detect`）。
底部「媒体服务器集成」卡片展示 Emby / Jellyfin / Audiobookshelf 的建议媒体库
路径；配置了 `EMBY_URL` / `JELLYFIN_URL` / `AUDIOBOOKSHELF_URL` 后提供一键跳转。

## 9. 媒体服务器集成

| 服务器 | 建议媒体库目录 | 环境变量 |
| --- | --- | --- |
| Emby | `/Media/Video`（电影/电视节目）、`/Media/Audio`（音乐） | `EMBY_URL` |
| Jellyfin | `/Media/Video`、`/Media/Audio` | `JELLYFIN_URL` |
| Audiobookshelf | `/Media/Audio`（播客/有声书） | `AUDIOBOOKSHELF_URL` |

目录结构天然适配：`<根>/<来源>/<频道>/<播放列表>/<标题>.ext`，
频道名直接作为「剧集/专辑」分组，播放列表作为「季/子目录」。

## 10. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MEDIA_VIDEO_DIR` | `/Media/Video` | 视频根目录 |
| `MEDIA_AUDIO_DIR` | `/Media/Audio` | 音频根目录 |
| `MEDIA_DEFAULT_VIDEO_HEIGHT` | `480` | 默认清晰度上限（0=原画） |
| `MEDIA_AUDIO_QUALITY` | `0` | 有损编码质量（copy 模式忽略） |
| `MEDIA_SUB_LANGS` | `en.*` | 字幕语言 |
| `MEDIA_SCAN_LIMIT` | `100` | 每次扫描条目上限 |
| `MEDIA_MAX_PER_SYNC` | `20` | 每次同步最多新下载数 |
| `MEDIA_MAX_CONCURRENT` | `2` | 同时下载数 |
| `MEDIA_PROXY_URL` | 空 | `network_mode=system` 时的默认代理 |
| `MEDIA_RETENTION_DAYS` | `30` | 已结束任务清理天数 |

## 11. 与旧 YouTube 模块的关系

早期版本有一个 YouTube 专用模块（`/api/youtube/*`、页面 `/media/youtube`）。
它的订阅类型里错误地放入了 `bilibili` / `podcast` 等预留值，那些行永远无法下载，
日志会反复出现「尚未实现」——现在已修正：

| 项目 | 现在 |
| --- | --- |
| 旧页面 `/media/youtube` | **重定向**到 `/media/subscriptions` |
| 旧模块订阅类型 | 只允许 `channel` / `playlist` |
| 旧模块调度 | **不再注册**（调度统一由媒体下载中心负责） |
| 旧库中的历史数据 | 启动时**自动迁移**到统一中心（订阅 + 已下载归档，幂等），并禁用旧行 |
| 旧模块遇到非 YouTube 行 | 直接跳过（DEBUG 日志），不再报「尚未实现」 |

## 12. 排障


1. `GET /health/media` 或 UI「网络检测」：区分「API 通但 CDN 不通」（需代理）与依赖缺失。
   网络检测是**按来源（provider）**做的，失败信息会明确写清是哪个来源，不会把
   Bilibili 的故障报成 YouTube；订阅行上的 🌐 按钮会用该订阅自己的代理策略复测
   （`GET /api/media/network-test?subscription_id=<id>`）。
2. `SSL: UNEXPECTED_EOF_WHILE_READING` / `403` → 该源 CDN 被阻断，为该订阅设置
   `http`/`socks5` 代理，或让容器走 VPN sidecar。
3. 需要登录 / 年龄限制（YouTube、X）→ 挂载 cookies 并设置 `YOUTUBE_COOKIES_FILE`。
4. 任务停在 `paused` → 点「继续」，yt-dlp 会用 `--continue` 续传。
5. 磁盘被占满 → 降低 `MEDIA_MAX_PER_SYNC` / `MEDIA_MAX_CONCURRENT`，或设置 `rate_limit`。
