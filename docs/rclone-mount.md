# Rclone 挂载（容器内）

把 AList / WebDAV 远程挂载成容器里的一个目录，**宿主机不装 rclone**，配置、凭据、缓存全部
落在 `data` 卷里。因此备份整个 `data/` 目录 → 在另一台 NAS 上 `docker compose up -d`，
挂载会自动恢复，不需要在新机器上做任何 NAS 侧配置。

---

## 1. 它怎么工作

```
        ┌──────────────── 容器 naspilot ────────────────┐
        │                                               │
        │  rclone (镜像内置)                             │
        │      ↓ FUSE (需要 /dev/fuse + SYS_ADMIN)       │
        │  /app/data/alist   ← 挂载点，位于数据卷内      │
        │                                               │
        └───────────────┬───────────────────────────────┘
                        │ bind mount（必须 :rshared）
                        ↓
        宿主机 <你的 data 路径>/alist/  ← 宿主机直接看到文件
```

三个要点：

| 要点 | 为什么 |
| --- | --- |
| 挂载点必须在 `/app/data` 之下 | 只有数据卷会 bind 回宿主机。挂在容器其它路径，宿主机看不见，容器重建也丢。 |
| data 卷必须 `:rshared` | Docker 默认 `rprivate`，**容器内新建的挂载不会传播出去**，宿主机只会看到空目录。 |
| compose 要有 `devices: /dev/fuse` + `cap_add: SYS_ADMIN` | 容器不能自己创建设备节点；FUSE 挂载需要该能力。 |

---

## 2. compose 需要的东西

三个 compose 文件已经都加好了。如果你是从旧版本升级，把下面这些补进 `naspilot` 服务：

```yaml
    devices:
      - /dev/fuse:/dev/fuse
    cap_add:
      - SYS_ADMIN
    security_opt:
      - apparmor:unconfined

    volumes:
      - ./data:/app/data:rshared      # ← :rshared 是关键
```

**宿主机这一层也必须是 shared 挂载**，否则 Docker 会拒绝 `rshared`：

```
docker: Error response from daemon: path /volume1/docker/naspilot/data is mounted on /volume1
but it is not a shared or slave mount.
```

解决（宿主机执行一次）：

```bash
sudo mount --make-shared /volume1
```

重启后若丢失，可以写进群晖的「控制面板 → 任务计划 → 触发的任务（开机）」。

> 只想在**容器内**使用挂载（不需要宿主机访问）？把 `:rshared` 去掉，两份 `devices`/`cap_add`
> 也保留即可，其它不变。

---

## 3. 配置步骤

1. 打开 **集成工具 → Rclone 挂载**。
2. 填写：
   - **AList 地址**：`https://alist.example.com`（不要带 `/dav`，插件会自动补）
   - **用户名 / 密码**：AList 账号
   - **挂载点**：保持默认 `/app/data/alist` 即可
   - **文件属主 UID/GID**：群晖常见 `0`；想让普通用户读写可填 `1000`
3. 点 **保存配置**，再点 **挂载**。
4. 状态卡片会显示挂载点、挂载源、顶层条目数、缓存占用。

宿主机上就能直接 `ls ./data/alist/` 看到 AList 的文件了。

### 关于密码

密码由 `rclone obscure` 加密后写入 `rclone.conf`，以 stdin 传入（不出现在 `ps` 里）。
数据库里仍保留明文密码，因为需要它才能重新生成 `rclone.conf` —— 换句话说，
**`data/` 目录本身就是敏感数据**，备份时请自行加密或限制访问权限。

---

## 4. 动作说明

| 动作 | 行为 |
| --- | --- |
| `status` | 只探测：是否已挂载、挂载源、缓存占用、环境预检结果 |
| `mount` | 建立挂载；已挂载则直接返回 `already_mounted`（幂等） |
| `unmount` | `fusermount3 -uz`（lazy），忙时也能卸载 |
| `restart` | 先卸载再挂载 |
| `ensure` | **默认动作**：等同于 `mount`。定时任务用它做自愈 —— 网络闪断导致挂载消失时会被重新拉起 |

「立即执行」按钮和定时任务在没有显式 action 时都走 `ensure`。

---

## 5. 换机器 / 恢复

```bash
# 旧机器
tar czf naspilot-data.tgz -C /volume1/docker/naspilot data

# 新机器
tar xzf naspilot-data.tgz -C /volume1/docker/naspilot
cd /volume1/docker/naspilot && docker compose up -d
```

因为 `autostart` 默认为开，容器启动后由 `run_startup_hooks` 自动重建挂载，
日志里会看到：

```
Rclone 挂载：容器启动自动挂载成功 → /app/data/alist
```

新机器上**不需要**安装 rclone、不需要重建 remote、不需要改任何 NAS 配置。

---

## 5.1 挂载点不会进 Docker 备份

`docker-compose.yml` 里 data 卷指向 `/volume1/docker/naspilot/data`，
所以宿主机上 AList 的内容在：

```
/volume1/docker/naspilot/data/alist/
```

**「Docker 备份」插件默认排除这些路径**（配置项 `exclude_paths`，默认
`naspilot/data/alist` 和 `naspilot/data/media`）。这一点很重要：前者是 AList 的
**远程副本**，后者是可重新下载的媒体库 —— 打进备份包只会让归档膨胀到几十上百 GB。

插件有三层防护：

| 防护 | 作用 |
| --- | --- |
| `exclude_paths` | 按完整相对路径排除，默认 `naspilot/data/alist` + `naspilot/data/media` |
| `os.path.ismount()` | 自动跳过任何挂载点（FUSE/NFS/SMB） |
| `EXCLUDED_SUBDIR_NAMES` | 任意深度的 `cache/tmp/temp/log/transcode/...` 排除 |

> ★ **只排除 NASPilot 自己的路径，不会误伤其它容器。**
>
> 早期版本按目录名（`media`/`music`/`movies`）在任意深度排除，这是**错的** ——
> 别的容器（例如你自己的 `alist` 容器）里的 `data/media` 是它自己的真实数据，
> 被误排除后备份就是残缺的。
>
> 现在只认完整路径。实测结果：
>
> ```
> alist/data/media/clip.mp4     → 备份 ✓  （AList 自己的本地存储）
> alist/data/music/song.mp3     → 备份 ✓
> alist/data/storage/movie.mkv  → 备份 ✓
> alist/config/config.json      → 备份 ✓
> naspilot/data/alist/*         → 跳过   （rclone 远程视图）
> naspilot/data/media/*         → 跳过   （可重新下载的媒体库）
> ```
>
> 如果你还想额外排除某个容器的某个子目录，把它按完整相对路径加进
> `exclude_paths` 即可，例如 `jellyfin/metadata`。

备份结果里会列出被跳过的内容：

```json
{
  "skipped_mounts": [],
  "skipped_size": [],
  "apps": ["naspilot", "alist", "jellyfin"]
}
```

> 注意：容器的 `/volume1` 是 `:ro` 且默认 `rprivate`，宿主机上的 FUSE 挂载
> 通常**不会**传播进容器，因此 `ismount()` 在容器内多半看不到它 ——
> 这正是 `exclude_paths` 存在的原因，它不依赖传播行为。

---

## 6. 常见问题

**状态卡片里 `/dev/fuse` 显示「缺失」**
compose 少了 `devices: /dev/fuse:/dev/fuse`。改完要 `docker compose up -d --force-recreate`。

**挂载成功但宿主机目录是空的**
`data` 卷还是默认的 `rprivate`。改成 `:rshared`，并确认宿主机侧已 `mount --make-shared`。

**`rclone mount` 报 `fuse: device not found`**
同上，`/dev/fuse` 没透传，或宿主机内核没有 fuse 模块（群晖和威联通默认都有）。

**报 `fusermount3: failed to unmount: Device or resource busy`**
点「卸载」用的就是 lazy 卸载，通常能成功；仍失败时可 `docker restart naspilot`，
容器退出时内核会清掉命名空间内的挂载。

**挂载点不在数据卷内**
把 **挂载点** 改回 `/app/data/alist`。预检会直接拦下并提示。

**视频拖动进度条很慢**
确认 **VFS 缓存模式** 是 `full`，并适当加大 **缓存大小上限**（默认 `10G`）。

---

## 7. 与「AList 自动上传」的区别

| | Rclone 挂载 | AList 自动上传 |
| --- | --- | --- |
| 方式 | FUSE 文件系统 | 纯 HTTP API |
| 用途 | 直读远程文件（播放、扫描英语库） | 本地文件传到远程 |
| 依赖 | `/dev/fuse`、`SYS_ADMIN`、`:rshared` | 无 |
| 换机器 | 需 data 卷 + compose 三项 | 只需 data 卷 |

两者不冲突，可以同时开启。
