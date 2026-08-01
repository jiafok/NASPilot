import os, time, json, requests, feedparser, yaml, hmac, hashlib, base64, re, socket, fcntl, random, shutil
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs
from typing import Dict, List, Optional, Tuple, Set, Any
import subprocess

# ======================================================
# 常量定义
# ======================================================
SCRIPT_NAME = "pt_rss_auto"
#LOCK_FILE = f"/tmp/{SCRIPT_NAME}.lock"
MAX_RETRIES = 3
RETRY_DELAY = 5
print(f"[{SCRIPT_NAME}] 启动（v2.1-final）", flush=True)
# ======================================================
# 路径
# ======================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE_DIR, "config_pt_rss_auto.yaml")

STATE_DIR = os.environ.get("PT_RSS_STATE_DIR", "/logs")
PROCESSED_FILE = os.path.join(STATE_DIR, "processed.json")
DAILY_FILE = os.path.join(STATE_DIR, "daily_report.json")


# ======================================================
# 时间工具（统一使用北京时间，UTC+8）
# ======================================================
LOCAL_TZ = timezone(timedelta(hours=8))

def utc_now() -> datetime:
    """返回带时区的当前北京时间"""
    return datetime.now(LOCAL_TZ)

def utc_now_iso() -> str:
    """返回 ISO 格式的 UTC 时间字符串"""
    return utc_now().isoformat()

def days_since(timestamp: float) -> float:
    """计算距离 timestamp 的天数"""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return (utc_now() - dt).total_seconds() / 86400

def hours_since_iso(ts_iso: str) -> float:
    """计算距离 ISO 时间字符串的小时数"""
    dt = datetime.fromisoformat(ts_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return (utc_now() - dt).total_seconds() / 3600


# ======================================================
# 文件操作
# ======================================================
def load_json(p: str) -> dict:
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[ERROR] Failed to load {p}: {e}", flush=True)
            return {}
    return {}

def save_json(p: str, o: dict, atomic: bool = True):
    """原子保存 JSON 文件"""
    if atomic:
        tmp_path = f"{p}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(o, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, p)
        except Exception as e:
            print(f"[ERROR] Failed to save {p}: {e}", flush=True)
            raise
    else:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(o, f, indent=2, ensure_ascii=False)

def extract_tid_from_tags(tags_str: str) -> Optional[str]:
    """从标签字符串中提取 tid"""
    if not tags_str:
        return None
    for tag in tags_str.split(','):
        tag = tag.strip()
        if tag.startswith("rss_tid:"):
            return tag.replace("rss_tid:", "")
    return None


# ======================================================
# 状态常量
# ======================================================
STATUS_PENDING_FREE = "pending_free"
STATUS_EXPIRED_FREE = "expired_free"
STATUS_ADDED = "added"
STATUS_COMPLETED = "completed"
STATUS_EVICTED = "evicted"

OLD_STATUS_TO_EVICTED = {
    "evicted_by_rss": STATUS_EVICTED,
    "deleted_space_seed": STATUS_EVICTED,
    "deleted_space_stuck": STATUS_EVICTED,
    "deleted_stuck": STATUS_EVICTED,
    "deleted_rss_missing": STATUS_EVICTED,
}

def is_final_status(status: str) -> bool:
    """判断是否为终态（不再主动处理，但可能被强制清理）"""
    if not status:
        return False
    if status in OLD_STATUS_TO_EVICTED:
        return True
    return status in [STATUS_COMPLETED, STATUS_EXPIRED_FREE, STATUS_EVICTED]

def is_hard_final_status(status: str) -> bool:
    """真正不可再处理的硬终态（永远跳过）"""
    if not status:
        return False
    if status in OLD_STATUS_TO_EVICTED:
        return True
    return status in [STATUS_EVICTED, STATUS_EXPIRED_FREE]

def migrate_old_status(rec: dict, processed: dict) -> bool:
    """迁移旧状态到新状态"""
    old_status = rec.get("status", "")
    if old_status in OLD_STATUS_TO_EVICTED:
        print(f"[MIGRATE] converting {old_status} -> {STATUS_EVICTED}")
        rec["status"] = STATUS_EVICTED
        if "evicted_reason" not in rec:
            if "rss" in old_status:
                rec["evicted_reason"] = "rss"
            elif "space" in old_status:
                rec["evicted_reason"] = "space_seed"
            elif "stuck" in old_status:
                rec["evicted_reason"] = "stuck"
            else:
                rec["evicted_reason"] = old_status
        if "evicted_time" not in rec and "deleted_time" in rec:
            rec["evicted_time"] = rec["deleted_time"]
        return True
    return False


# ======================================================
# 配置
# ======================================================
with open(CFG_FILE, "r", encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

DEBUG = bool(CFG.get("debug", True))
MAX_ACTIVE = int(CFG.get("max_active_downloads", 0))
FREE_TTL_HOURS = float(CFG.get("free_ttl_hours", 24))

def dbg(msg: str):
    if DEBUG:
        if not msg.startswith("[DEBUG]"):
            print(f"[DEBUG] {msg}", flush=True)
        else:
            print(msg, flush=True)


# ======================================================
# 状态 & 统计
# ======================================================
processed = load_json(PROCESSED_FILE)
daily_state = load_json(DAILY_FILE)

# 迁移旧数据
migrated_count = 0
for tid, rec in processed.items():
    if migrate_old_status(rec, processed):
        migrated_count += 1
    rec.setdefault("rss_missing_count", 0)

if migrated_count > 0:
    save_json(PROCESSED_FILE, processed)
    dbg(f"[MIGRATE] migrated {migrated_count} records")

DEFAULT_STATS = {
    "added": 0,
    "expired_free": 0,
    "deleted_stuck": 0,
    "deleted_seed": 0,
    "deleted_rss_missing": 0,
    "deleted_space": 0,
}

DEFAULT_DETAILS = {
    "added_items": [],
    "deleted_items": [],
}

if "stats" not in daily_state:
    daily_state["stats"] = DEFAULT_STATS.copy()
else:
    for k, v in DEFAULT_STATS.items():
        daily_state["stats"].setdefault(k, v)

if "details" not in daily_state:
    daily_state["details"] = DEFAULT_DETAILS.copy()
else:
    daily_state["details"].setdefault("added_items", [])
    daily_state["details"].setdefault("deleted_items", [])

def append_daily_detail(detail_type: str, item: dict):
    """追加每日明细并限制列表长度，避免 JSON 过大。"""
    details = daily_state.setdefault("details", DEFAULT_DETAILS.copy())
    details.setdefault("added_items", [])
    details.setdefault("deleted_items", [])

    max_keep = int(CFG.get("audit", {}).get("max_items", 50))
    if detail_type == "added":
        details["added_items"].append(item)
        if len(details["added_items"]) > max_keep:
            details["added_items"] = details["added_items"][-max_keep:]
    elif detail_type == "deleted":
        details["deleted_items"].append(item)
        if len(details["deleted_items"]) > max_keep:
            details["deleted_items"] = details["deleted_items"][-max_keep:]

dbg(f"processed loaded: {len(processed)} items")

# 通知缓冲
notify_added = []
notify_evicted = []
notify_skip_eviction = []
notify_add_failed = []

# 本次运行中已估算释放但磁盘尚未刷新的累计空间（GB）
_session_freed_gb: float = 0.0


# ======================================================
# 飞书通知
# ======================================================
def _fs_sign(ts: str, secret: str) -> str:
    return base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    ).decode()

def feishu_send(title: str, text: str, color: str = "blue") -> bool:
    if not CFG.get("feishu_enable"):
        dbg("[FEISHU] disabled by config")
        return False

    body = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": [
                {"tag": "markdown", "content": text[:4000]}
            ],
        },
    }

    secret = CFG.get("feishu_secret")
    if secret:
        ts = str(int(time.time()))
        body.update({"timestamp": ts, "sign": _fs_sign(ts, secret)})

    webhook = CFG.get("feishu_webhook", "")
    if not webhook:
        dbg("[FEISHU] missing feishu_webhook")
        return False

    retry_cfg = CFG.get("feishu_retry", {})
    max_retries = int(retry_cfg.get("max_retries", 3))
    base_delay = float(retry_cfg.get("base_delay_sec", 1.5))
    timeout = float(retry_cfg.get("timeout_sec", 10))

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(
                webhook,
                headers={"Content-Type": "application/json"},
                json=body,
                timeout=(3, timeout),
            )

            # HTTP 非 2xx：通常是网关/服务端问题，可重试
            if not (200 <= r.status_code < 300):
                raise RuntimeError(f"http={r.status_code} body={r.text[:300]}")

            # 飞书机器人常见返回: {"code":0,"msg":"success"}
            try:
                payload = r.json() if r.text else {}
            except ValueError:
                payload = {}

            if isinstance(payload, dict) and payload.get("code", 0) != 0:
                raise RuntimeError(
                    f"code={payload.get('code')} msg={payload.get('msg', '')}"
                )

            return True

        except Exception as e:
            if attempt >= max_retries:
                dbg(f"[FEISHU] failed after {max_retries} attempts: {e}")
                return False

            # 轻微抖动，降低多消息并发触发频控的概率
            sleep_sec = base_delay * attempt + random.uniform(0, 0.8)
            dbg(f"[FEISHU] attempt {attempt}/{max_retries} failed: {e}; retry in {sleep_sec:.1f}s")
            time.sleep(sleep_sec)

    return False


# ======================================================
# qBittorrent 客户端
# ======================================================
class QBError(Exception):
    pass

class QBClient:
    def __init__(self, cfg: dict, timeout: int = 10,
                 fallback_download_dir: Optional[str] = None,
                 fallback_local_dir: Optional[str] = None):
        self.base = cfg["url"].rstrip("/")
        self.timeout = timeout
        self.s = requests.Session()
        self._last_free_space: Optional[float] = None
        self._space_reliable = False
        self._last_space_source = "unknown"
        self.fallback_paths = [fallback_local_dir, fallback_download_dir]
        
        # 重试登录
        for attempt in range(MAX_RETRIES):
            try:
                r = self.s.post(
                    f"{self.base}/api/v2/auth/login",
                    data={
                        "username": cfg.get("username"),
                        "password": cfg.get("password"),
                    },
                    timeout=self.timeout,
                )
                if r.status_code in (200, 204):
                    return
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"Login failed after {MAX_RETRIES} attempts: {r.status_code} {r.text}")
                time.sleep(RETRY_DELAY)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"Login request failed: {e}")
                time.sleep(RETRY_DELAY)
    
    def _get(self, path: str):
        for attempt in range(MAX_RETRIES):
            try:
                r = self.s.get(f"{self.base}{path}", timeout=self.timeout)
                if r.status_code == 200:
                    return r
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"GET {path} status={r.status_code}")
                time.sleep(RETRY_DELAY)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"GET {path} failed: {e}")
                time.sleep(RETRY_DELAY)
    
    def _post(self, path: str, data: dict, ok_status: Tuple[int, ...] = (200,)):
        for attempt in range(MAX_RETRIES):
            try:
                r = self.s.post(f"{self.base}{path}", data=data, timeout=self.timeout)
                if r.status_code in ok_status:
                    return r
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"POST {path} status={r.status_code}")
                time.sleep(RETRY_DELAY)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES - 1:
                    raise QBError(f"POST {path} failed: {e}")
                time.sleep(RETRY_DELAY)
    
    def torrents(self) -> List[dict]:
        return self._get("/api/v2/torrents/info").json()
    
    def add(self, url: str, savepath: str, tags: Optional[str] = None):
        data = {"urls": url, "savepath": savepath}
        if tags:
            data["tags"] = tags
        r = self._post("/api/v2/torrents/add", data, ok_status=(200, 202))
        body = (r.text or "").strip().lower()
        if "fails" in body or "error" in body:
            raise QBError(f"add rejected by qB: {r.text[:200]}")
        return True
    
    def add_file(self, torrent_url: str, savepath: str, tags: Optional[str] = None) -> bool:
        """Python 端下载 .torrent 文件再上传给 qB（单次尝试，不重试）。

        M-Team 的 Cloudflare CDN 要求 HTTP/2，Python requests 只支持 HTTP/1.1，
        会被 TLS 层卡死。因此用 subprocess 调 curl 下载（容器内置，支持 HTTP/2）。
        """
        dl_timeout = int(CFG.get("download_timeout", 120))
        dbg(f"[ADD_FILE] downloading from {torrent_url[:200]}... (timeout={dl_timeout}s)")

        # 用 curl 下载 .torrent（HTTP/2，绕过 Cloudflare TLS 指纹阻断）
        try:
            torrent_data = _curl_download(torrent_url, dl_timeout)

            if not torrent_data or len(torrent_data) < 50:
                raise QBError(f"torrent file too small: {len(torrent_data)} bytes")
            if not torrent_data.startswith(b'd'):
                raise QBError(f"response not a torrent (starts with {torrent_data[:10]!r})")

            dbg(f"[ADD_FILE] downloaded {len(torrent_data)} bytes")
        except QBError:
            raise
        except Exception as e:
            raise QBError(f"download error: {e}")

        # 上传到 qBittorrent
        try:
            files = {"torrents": ("seed.torrent", torrent_data, "application/x-bittorrent")}
            form_data = {"savepath": savepath}
            if tags:
                form_data["tags"] = tags

            r = self.s.post(
                f"{self.base}/api/v2/torrents/add",
                files=files,
                data=form_data,
                timeout=self.timeout,
            )

            if r.status_code not in (200, 202):
                raise QBError(f"upload status={r.status_code}")

            body = (r.text or "").strip().lower()
            if "fails" in body or "error" in body:
                raise QBError(f"qB rejected: {r.text[:200]}")

            dbg(f"[ADD_FILE] uploaded successfully")
            return True
        except requests.RequestException as e:
            raise QBError(f"upload failed: {e}")

        return False

    def delete(self, torrent_hash: str, delete_files: bool = True):
        self._post("/api/v2/torrents/delete", {
            "hashes": torrent_hash,
            "deleteFiles": "true" if delete_files else "false",
        })

    def add_tags(self, torrent_hash: str, tags: str):
        if not tags:
            return
        self._post("/api/v2/torrents/addTags", {
            "hashes": torrent_hash,
            "tags": tags,
        })
    
    def _local_free_space_gb(self) -> Optional[float]:
        """当 qB API 异常时，回退到本地可访问路径的磁盘剩余空间。"""
        seen: Set[str] = set()
        for p in self.fallback_paths:
            if not p:
                continue
            if p in seen:
                continue
            seen.add(p)

            if not os.path.exists(p):
                dbg(f"[SPACE_FALLBACK] path not found: {p}")
                continue

            try:
                usage = shutil.disk_usage(p)
                free_gb = usage.free / 1024 / 1024 / 1024
                dbg(f"[SPACE_FALLBACK] using local path {p}, free={free_gb:.1f}GB")
                return free_gb
            except Exception as e:
                dbg(f"[SPACE_FALLBACK] disk_usage failed for {p}: {e}")

        return None

    def space_reliable(self) -> bool:
        return self._space_reliable

    def last_space_source(self) -> str:
        return self._last_space_source

    def free_space_gb(self) -> float:
        md = self._get("/api/v2/sync/maindata").json()

        # 详细诊断日志
        server_state = md.get("server_state", {})
        raw_bytes = server_state.get("free_space_on_disk", None)

        def _use_fallback(reason: str) -> float:
            fallback = self._local_free_space_gb()
            if fallback is not None and fallback > 0:
                print(f"[WARNING] qB free space invalid ({reason}); using local disk free {fallback:.1f}GB", flush=True)
                self._last_free_space = fallback
                self._space_reliable = True
                self._last_space_source = "local_fallback"
                return fallback

            if self._last_free_space is not None and self._last_free_space > 0:
                print(f"[WARNING] qB free space invalid ({reason}); using cached value {self._last_free_space:.1f}GB", flush=True)
                self._space_reliable = True
                self._last_space_source = "cached"
                return self._last_free_space

            print(
                f"[ERROR] qB free space invalid ({reason}); no usable fallback path. "
                f"Space checks are now UNRELIABLE and safety mode will block space-based actions. "
                f"Set config space_fallback_dir to a local mounted path.",
                flush=True,
            )
            self._space_reliable = False
            self._last_space_source = "unreliable"
            return 0.0

        # 如果字段不存在或异常，打印响应关键信息并回退
        if raw_bytes is None:
            print(f"[ERROR] qB API: free_space_on_disk missing, server_state keys: {list(server_state.keys())}", flush=True)
            return _use_fallback("missing field")

        # 尝试转换为数字
        try:
            raw_bytes = float(raw_bytes)
        except (ValueError, TypeError) as e:
            print(f"[ERROR] qB API: free_space_on_disk invalid type/value: {raw_bytes}, err={e}", flush=True)
            return _use_fallback("non-numeric")

        space = raw_bytes / 1024 / 1024 / 1024

        # 检查异常值（-1 是 qB 无法读取磁盘的常见特殊值）
        if space <= 0:
            print(f"[WARNING] qB returned suspicious free_space_on_disk: raw_bytes={raw_bytes}, computed_gb={space:.2f}GB", flush=True)
            return _use_fallback("<=0 from API")

        # 更新缓存
        self._last_free_space = space
        self._space_reliable = True
        self._last_space_source = "qb_api"
        return space


# ======================================================
# qB 初始化
# ======================================================
try:
    qb = QBClient(
        CFG["qbittorrent"],
        timeout=10,
        fallback_download_dir=CFG.get("download_dir"),
        fallback_local_dir=CFG.get("space_fallback_dir"),
    )
    print("[DEBUG] qB login OK", flush=True)
except QBError as e:
    print(f"[ERROR] qB unreachable: {e}", flush=True)
    exit(2)


# ======================================================
# RSS 解析（带重试）
# ======================================================
def parse_rss_with_retry(url: str, timeout: int = 30) -> Any:
    """带重试的 RSS 解析（M-Team 慢，连接超时 15s，读取超时 30s）"""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(
                url,
                timeout=(15, timeout),
                headers={
                    "User-Agent": "curl/7.81.0",
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
                allow_redirects=True,
            )
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            if feed.entries:  # 检查是否有条目
                return feed
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError("RSS parsed but no entries")
            time.sleep(RETRY_DELAY)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(f"RSS fetch failed after {MAX_RETRIES} attempts: {e}")
            time.sleep(RETRY_DELAY)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY)
    raise RuntimeError("RSS parse failed")

def get_download_url(item) -> str:
    return item.enclosures[0].href if hasattr(item, "enclosures") and item.enclosures else item.link

def extract_tid(url: str) -> Optional[str]:
    return parse_qs(urlparse(url).query).get("tid", [None])[0]

def extract_id_from_url(url: str) -> Optional[str]:
    """从 M-Team 下载链接中提取种子 id（兼容 /download.php?id=xxx 和 /rss/download.php?id=xxx）"""
    params = parse_qs(urlparse(url).query)
    return params.get("id", [None])[0]

def build_passkey_download_url(tid: str) -> Optional[str]:
    """用 passkey 构造不会过期的种子下载链接。
    
    M-Team 的 RSS 种子下载链接（dl=1 时）包含有时效的 sign/t 参数，过期后返回：
    - HTML 页面提示"链接已过期"
    - 或直接返回非 .torrent 内容
    
    本函数使用 passkey 构造稳定的下载链接，格式和网站直接复制的一样。
    """
    passkey = CFG.get("mt_passkey", "").strip()
    if not passkey:
        return None
    domain = CFG.get("site_domain", "m-team.cc").strip().rstrip("/")
    return f"https://{domain}/download.php?id={tid}&passkey={passkey}"

def is_mteam_rss_download_url(url: str) -> bool:
    """判断是否为 M-Team RSS 生成的有时效下载链接"""
    return "/rss/download.php" in url or (
        "m-team" in url and "dl=1" not in url and "passkey=" not in url and "sign=" in url
    )

def detect_expired_response(content: bytes, status_code: int = 200) -> bool:
    """根据响应内容判断是否为「链接已过期」"""
    text = ""
    try:
        text = content.decode("utf-8", errors="replace")[:500]
    except Exception:
        pass
    expired_keywords = ["已过期", "expired", "链接失效", "link expired", "签名错误", "sign error"]
    return any(kw in text.lower() for kw in expired_keywords)

def get_rss_sources() -> List[str]:
    """读取 RSS 源列表，兼容旧版单个 rss_url 配置。"""
    sources: List[str] = []

    raw_list = CFG.get("rss_urls")
    if isinstance(raw_list, list):
        for x in raw_list:
            if isinstance(x, str) and x.strip():
                sources.append(x.strip())

    legacy_url = CFG.get("rss_url")
    if isinstance(legacy_url, str) and legacy_url.strip():
        sources.append(legacy_url.strip())

    # 去重并保持顺序
    deduped: List[str] = []
    seen: Set[str] = set()
    for s in sources:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)

    return deduped


# ======================================================
# 任务查询
# ======================================================
def get_incomplete_torrents() -> List[dict]:
    return [t for t in qb.torrents() if t["progress"] < 1]

def get_stuck_torrents(threshold_days: int) -> List[dict]:
    return [t for t in qb.torrents() if t["progress"] < 1 and days_since(t["added_on"]) >= threshold_days]

def get_seed_torrents_over_days(threshold_days: int) -> List[dict]:
    done = [t for t in qb.torrents() if t["progress"] == 1 and (t.get("seeding_time", 0) / 86400) >= threshold_days]
    return sorted(done, key=lambda t: t.get("seeding_time", 0), reverse=True)

def has_tag(torrent: dict, tag: str) -> bool:
    raw = torrent.get("tags", "")
    if isinstance(raw, list):
        tags = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        tags = [x.strip() for x in raw.split(",") if x.strip()]
    else:
        tags = []
    return tag in tags

def normalize_title(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()

def find_torrent_by_tag(tag: str) -> Tuple[Optional[dict], str]:
    """按 tag 查找唯一任务，返回 (任务, 状态)。状态: one/none/multi"""
    candidates = [t for t in qb.torrents() if has_tag(t, tag)]
    if not candidates:
        return None, "none"
    if len(candidates) > 1:
        return None, "multi"
    return candidates[0], "one"

def find_torrent_by_title(item_title: str) -> Tuple[Optional[dict], str]:
    """按标题匹配唯一任务，返回 (任务, 状态)。状态: one/none/multi"""
    target = normalize_title(item_title)
    if not target:
        return None, "none"

    torrents = qb.torrents()
    exact = [t for t in torrents if normalize_title(t.get("name", "")) == target]
    if len(exact) == 1:
        return exact[0], "one"
    if len(exact) > 1:
        return None, "multi"

    # 兜底：包含关系 + 限制候选规模，降低误判
    fuzzy = []
    for t in torrents:
        name_norm = normalize_title(t.get("name", ""))
        if not name_norm:
            continue
        if target in name_norm or name_norm in target:
            fuzzy.append(t)
            if len(fuzzy) > 3:
                break

    if len(fuzzy) == 1:
        return fuzzy[0], "one"
    if len(fuzzy) > 1:
        return None, "multi"
    return None, "none"

def find_existing_torrent(tag: str, item_title: str) -> Tuple[Optional[dict], str, str]:
    """优先按 tag，其次按标题查找。返回 (任务, 状态, 来源)。"""
    by_tag, state_tag = find_torrent_by_tag(tag)
    if state_tag == "one" and by_tag is not None:
        return by_tag, "one", "tag"
    if state_tag == "multi":
        return None, "multi", "tag"

    by_title, state_title = find_torrent_by_title(item_title)
    if state_title == "one" and by_title is not None:
        return by_title, "one", "title"
    if state_title == "multi":
        return None, "multi", "title"

    return None, "none", "none"

def sync_status_from_existing_torrent(rec: dict, tid: str, item_title: str, tag: str, t: dict) -> str:
    """当 qB 已存在任务时，将 processed 状态与 qB 同步。返回 added/completed。"""
    now_iso = utc_now_iso()
    rec["tag"] = tag
    rec["title"] = item_title
    rec["rss_missing_count"] = 0

    # 如果任务历史上没有 tag，补齐 tag，后续驱逐逻辑才能稳定识别
    try:
        if not has_tag(t, tag):
            qb.add_tags(t.get("hash", ""), tag)
    except Exception as e:
        dbg(f"[SYNC] add tag failed for tid={tid}: {e}")

    if t.get("progress", 0) >= 1:
        rec["status"] = STATUS_COMPLETED
        rec.setdefault("completed_time", now_iso)
        return STATUS_COMPLETED

    rec["status"] = STATUS_ADDED
    rec.setdefault("added_time", now_iso)
    return STATUS_ADDED

def mark_add_failed(rec: dict, tid: str, title: str, reason: str):
    """记录新增失败原因并发送失败通知。"""
    rec["last_add_error"] = reason
    rec["last_add_error_time"] = utc_now_iso()
    rec["add_fail_count"] = int(rec.get("add_fail_count", 0)) + 1
    save_json(PROCESSED_FILE, processed)
    notify_add_failed.append(f"{tid} | {title[:40]} | {reason[:180]}")

def wait_torrent_added_by_tag(tag: str, retries: int = 30, interval_sec: float = 2.0) -> bool:
    """添加后按 tag 轮询确认任务已进入 qB（M-Team 慢，等 60s）。"""
    for _ in range(retries):
        try:
            if any(has_tag(t, tag) for t in qb.torrents()):
                return True
        except Exception as e:
            dbg(f"[ADD_CONFIRM] list torrents failed: {e}")
        time.sleep(interval_sec)
    return False


def wait_torrent_added_by_titleorhash(title: str, retries: int = 30, interval_sec: float = 2.0) -> bool:
    """添加后按标题模糊匹配确认任务（兜底，tag 可能延迟写入）。"""
    target = normalize_title(title)
    if not target:
        return False
    for _ in range(retries):
        try:
            for t in qb.torrents():
                if target in normalize_title(t.get("name", "")):
                    return True
        except Exception:
            pass
        time.sleep(interval_sec)
    return False

def sync_pending_from_qb():
    """本轮结束前回查 qB，修正 pending_free 到 added/completed（处理延迟入队场景）。"""
    changed = 0
    for tid, rec in processed.items():
        if rec.get("status") != STATUS_PENDING_FREE:
            continue

        title = rec.get("title", "")
        tag = rec.get("tag") or f"rss_tid:{tid}"
        t, state, src = find_existing_torrent(tag, title)
        if state != "one" or t is None:
            continue

        synced = sync_status_from_existing_torrent(rec, tid, title, tag, t)
        if synced == STATUS_COMPLETED:
            notify_added.append(f"♻️ 延迟确认已完成：{title[:50]}")
        else:
            notify_added.append(f"♻️ 延迟确认已在下载：{title[:50]}")
        dbg(f"[SYNC_PENDING] tid={tid}, status={synced}, by={src}")
        changed += 1

    if changed:
        save_json(PROCESSED_FILE, processed)
        dbg(f"[SYNC_PENDING] updated {changed} pending records")


# ======================================================
# 核心删除函数
# ======================================================
def delete_torrent_and_mark(torrent: dict, reason: str, reason_detail: str = "", delete_files: bool = True, tid: Optional[str] = None) -> bool:
    """统一删除任务并标记"""
    tags = torrent.get("tags", "")
    # 优先使用传入的 tid，其次尝试从标签中提取
    if not tid:
        tid = extract_tid_from_tags(tags)
    torrent_name = torrent.get("name", "unknown")
    torrent_hash = torrent["hash"]
    save_path = torrent.get("save_path", "unknown")
    content_path = torrent.get("content_path", "")
    
    if not content_path:
        content_path = os.path.join(save_path, torrent_name)
    
    total_size = torrent.get("total_size", 0) / (1024**3)
    
    dbg(f"[DELETE] tid={tid}, name={torrent_name}, reason={reason}, size={total_size:.2f}GB")
    
    # 执行删除
    try:
        qb.delete(torrent_hash, delete_files=delete_files)
        dbg(f"[DELETE] qB deleted: {torrent_name}")
        
        if delete_files and content_path and os.path.exists(content_path):
            time.sleep(0.5)
            if os.path.isdir(content_path) and not os.listdir(content_path):
                os.rmdir(content_path)
                dbg(f"[DELETE] Removed empty folder: {content_path}")
    except Exception as e:
        dbg(f"[DELETE] Failed: {torrent_name}, error={e}")
        if tid and tid in processed:
            processed[tid]["delete_failed"] = True
            processed[tid]["delete_error"] = str(e)
        return False

    # 删除成功后再更新状态与统计，避免失败也被计数
    if tid and tid in processed:
        rec = processed[tid]
        rec["status"] = STATUS_EVICTED
        rec["evicted_time"] = utc_now_iso()
        rec["evicted_reason"] = reason
        rec["evicted_detail"] = reason_detail if reason_detail else reason
        rec["evicted_content_path"] = content_path
        rec["evicted_size_gb"] = round(total_size, 2)
        rec["rss_missing_count"] = 0
        rec["rss_finalized"] = True
        # 删除成功后立即落盘，避免后续流程异常导致状态未写入
        save_json(PROCESSED_FILE, processed)

    # 统计按“成功删除事件”计数，不依赖 tid 是否存在于 processed
    if reason in ["stuck", "space_stuck"]:
        daily_state["stats"]["deleted_stuck"] += 1
    elif reason in ["space_seed", "seed"]:
        daily_state["stats"]["deleted_seed"] += 1
    elif reason == "rss":
        daily_state["stats"]["deleted_rss_missing"] += 1

    if reason in ["space_stuck", "space_seed"]:
        daily_state["stats"]["deleted_space"] += 1

    if not (tid and tid in processed):
        dbg(f"[DELETE] stats counted without processed record: reason={reason}, name={torrent_name}")

    append_daily_detail("deleted", {
        "time": utc_now_iso(),
        "tid": tid,
        "name": torrent_name,
        "reason": reason,
        "detail": reason_detail if reason_detail else reason,
        "size_gb": round(total_size, 2),
        "delete_files": delete_files,
    })
    
    file_status = "删除文件" if delete_files else "保留文件"
    notify_evicted.append(f"{tid} | {torrent_name[:50]} | {reason} | {file_status} ({total_size:.1f}GB)")
    return True


# ======================================================
# 空间清理
# ======================================================
def cleanup_for_new_task(target_free_gb: float) -> dict:
    """添加新任务前的空间清理"""
    global _session_freed_gb
    STUCK_DAYS = CFG["cleanup"]["stuck_download_days"]
    SEED_DAYS = CFG["cleanup"]["seed_days"]
    
    start_space = qb.free_space_gb()
    if not qb.space_reliable():
        dbg("[CLEANUP_NEW_TASK] free space source is unreliable, skip space gate and allow add")
        return {"ok": True, "deleted": [], "start_space": start_space, "end_space": start_space}

    # 加上本次运行中已估算释放但磁盘尚未刷新的累计量
    effective_space = start_space + _session_freed_gb
    deleted = []
    
    if effective_space >= target_free_gb:
        dbg(f"[CLEANUP_NEW_TASK] effective={effective_space:.1f}GB (disk={start_space:.1f}GB + session_freed={_session_freed_gb:.1f}GB) >= {target_free_gb}GB, skip cleanup")
        return {"ok": True, "deleted": [], "start_space": start_space, "end_space": effective_space}
    
    dbg(f"[CLEANUP_NEW_TASK] Need {target_free_gb}GB, effective={effective_space:.1f}GB (disk={start_space:.1f}GB + session_freed={_session_freed_gb:.1f}GB)")
    
    needed = max(0, target_free_gb - effective_space)
    freed = 0.0
    
    # 第一轮：卡死任务（使用硬终态检查）
    for t in get_stuck_torrents(STUCK_DAYS):
        tid = extract_tid_from_tags(t.get("tags", ""))
        status = processed.get(tid, {}).get("status", "")
        if is_hard_final_status(status):
            continue
        
        if delete_torrent_and_mark(t, "space_stuck", f"卡死>{STUCK_DAYS}天", delete_files=True, tid=tid):
            size_gb = t.get("total_size", 0) / (1024**3)
            freed += size_gb
            _session_freed_gb += size_gb
            deleted.append({"name": t.get("name", "unknown")[:60], "size": size_gb, "reason": "卡死下载"})
            if freed >= needed:
                break
    
    # 第二轮：做种超期（使用硬终态检查）
    if freed < needed:
        for t in get_seed_torrents_over_days(SEED_DAYS):
            if t.get("progress") != 1:
                continue
            tid = extract_tid_from_tags(t.get("tags", ""))
            status = processed.get(tid, {}).get("status", "")
            if is_hard_final_status(status):
                continue
            
            if delete_torrent_and_mark(t, "space_seed", f"做种>{SEED_DAYS}天", delete_files=True, tid=tid):
                size_gb = t.get("total_size", 0) / (1024**3)
                freed += size_gb
                _session_freed_gb += size_gb
                deleted.append({"name": t.get("name", "unknown")[:60], "size": size_gb, "reason": "做种超时"})
                if freed >= needed:
                    break
    
    time.sleep(3)
    end_space = qb.free_space_gb()

    # 综合磁盘实际读数和本次运行累计估算，两者任一满足即通过
    estimated_space = start_space + _session_freed_gb
    ok = end_space >= target_free_gb or estimated_space >= target_free_gb
    if ok and end_space < target_free_gb:
        dbg(f"[CLEANUP_NEW_TASK] disk not yet updated ({end_space:.1f}GB), "
            f"estimated={estimated_space:.1f}GB >= {target_free_gb}GB, allow add")

    return {
        "ok": ok,
        "deleted": deleted,
        "start_space": start_space,
        "end_space": max(end_space, estimated_space),
    }

def cleanup_low_space_with_incomplete() -> dict:
    """紧急空间清理（只清理到安全线，不过度删除）"""
    EMERGENCY_THRESHOLD = 20
    CLEANUP_TARGET = 30  # 紧急模式清理到 30GB 即可，不必到 50GB
    
    STUCK_DAYS = CFG["cleanup"]["stuck_download_days"]
    SEED_DAYS = CFG["cleanup"]["seed_days"]
    
    start_space = qb.free_space_gb()
    if not qb.space_reliable():
        dbg("[EMERGENCY] skip: free space source is unreliable, do not perform space-based deletions")
        return {"ok": False, "deleted": [], "start_space": start_space, "end_space": start_space}

    current_free = start_space
    deleted = []
    
    incomplete = get_incomplete_torrents()
    if not incomplete or current_free >= EMERGENCY_THRESHOLD:
        return {"ok": True, "deleted": [], "start_space": start_space, "end_space": current_free}
    
    dbg(f"[EMERGENCY] triggered: incomplete={len(incomplete)}, space={current_free:.1f}GB")
    
    # 目标：清理到至少 CLEANUP_TARGET GB
    target_space = max(EMERGENCY_THRESHOLD + 5, CLEANUP_TARGET)
    needed = max(0, target_space - start_space)
    freed = 0.0
    
    # 清理卡死任务（使用硬终态检查）
    for t in get_stuck_torrents(STUCK_DAYS):
        tid = extract_tid_from_tags(t.get("tags", ""))
        if is_hard_final_status(processed.get(tid, {}).get("status", "")):
            continue
        
        if delete_torrent_and_mark(t, "space_stuck", f"紧急清理-卡死>{STUCK_DAYS}天", delete_files=True, tid=tid):
            size_gb = t.get("total_size", 0) / (1024**3)
            freed += size_gb
            deleted.append({"name": t.get("name", "unknown")[:60], "size": size_gb, "reason": "卡死下载"})
            if freed >= needed:
                break
    
    # 清理做种任务（使用硬终态检查）
    if freed < needed:
        for t in get_seed_torrents_over_days(SEED_DAYS):
            if t.get("progress") != 1:
                continue
            tid = extract_tid_from_tags(t.get("tags", ""))
            if is_hard_final_status(processed.get(tid, {}).get("status", "")):
                continue
            
            if delete_torrent_and_mark(t, "space_seed", f"紧急清理-做种>{SEED_DAYS}天", delete_files=True, tid=tid):
                size_gb = t.get("total_size", 0) / (1024**3)
                freed += size_gb
                deleted.append({"name": t.get("name", "unknown")[:60], "size": size_gb, "reason": "做种超时"})
                if freed >= needed:
                    break
    
    time.sleep(3)
    end_space = qb.free_space_gb()

    estimated_space = start_space + freed
    ok = end_space >= EMERGENCY_THRESHOLD + 5 or estimated_space >= EMERGENCY_THRESHOLD + 5

    return {
        "ok": ok,
        "deleted": deleted,
        "start_space": start_space,
        "end_space": max(end_space, estimated_space),
    }

def cleanup_stuck_periodic():
    """定期清理卡死任务（✅ 修复：使用 is_hard_final_status）"""
    STUCK_DAYS = CFG["cleanup"]["stuck_download_days"]
    for t in get_stuck_torrents(STUCK_DAYS):
        tid = extract_tid_from_tags(t.get("tags", ""))
        # ✅ 必须修复：使用硬终态检查，completed 的任务仍然可以被清理
        if tid and tid in processed and is_hard_final_status(processed[tid].get("status", "")):
            dbg(f"  skip hard final status tid={tid}")
            continue
        delete_torrent_and_mark(t, "stuck", f"定期清理-卡死>{STUCK_DAYS}天", delete_files=True, tid=tid)


# ======================================================
# RSS 驱逐
# ======================================================
def evict_tid_download(tid: str):
    """RSS 缺席时删除未完成的任务"""
    rec = processed.get(tid)
    if not rec:
        return
    
    current_status = rec.get("status", "")
    
    # ✅ is_final_status 已经包含 STATUS_COMPLETED，无需重复判断
    if is_final_status(current_status):
        dbg(f"  tid={tid} already final status, skip eviction")
        return
    
    # 保护期（10分钟）
    added_time = rec.get("added_time")
    if added_time:
        added_dt = datetime.fromisoformat(added_time)
        if added_dt.tzinfo is None:
            added_dt = added_dt.replace(tzinfo=LOCAL_TZ)
        if (utc_now() - added_dt).total_seconds() < 600:
            dbg(f"  tid={tid} within grace period, skip")
            notify_skip_eviction.append(f"{tid} | 保护期内")
            return
    
    tag = rec.get("tag")
    if not tag:
        notify_skip_eviction.append(f"{tid} | 缺少 tag")
        return
    
    candidates = [t for t in qb.torrents() if has_tag(t, tag)]
    
    if not candidates:
        notify_skip_eviction.append(f"{tid} | 未找到任务 (tag={tag})")
        return
    
    if len(candidates) > 1:
        notify_skip_eviction.append(f"{tid} | 找到多个任务 ({len(candidates)}个)")
        return
    
    t = candidates[0]
    
    # 已完成的不删
    if t["progress"] >= 1:
        rec["status"] = STATUS_COMPLETED
        rec["completed_time"] = utc_now_iso()
        rec["rss_missing_count"] = 0
        rec["rss_finalized"] = True
        save_json(PROCESSED_FILE, processed)
        return
    
    delete_torrent_and_mark(t, "rss", "RSS缺席", delete_files=True, tid=tid)

def rss_delayed_eviction_check(rss_tid_set: Set[str], failed_sources: Set[str]):
    """RSS 缺席检查（方案A：源故障不计为 miss；方案B：阈值=2 兜底）"""
    if not CFG.get("enable_rss_eviction", True):
        dbg("RSS eviction disabled")
        return
    if not rss_tid_set:
        if failed_sources:
            dbg("[RSS] skip eviction: all RSS sources failed (not true miss)")
        else:
            dbg("[RSS] skip eviction: no RSS data")
        return
    
    dbg(f"[RSS] Eviction check, RSS has {len(rss_tid_set)} tids, {len(failed_sources)} source(s) failed")
    
    for tid, rec in processed.items():
        current_status = rec.get("status", "")
        missing_count = rec.get("rss_missing_count", 0)
        
        if is_final_status(current_status):
            continue
        
        if current_status != STATUS_ADDED:
            continue
        
        # RSS 中存在 -> 重置计数
        if tid in rss_tid_set:
            if missing_count != 0:
                rec["rss_missing_count"] = 0
            continue
        
        # ⭐ 方案A：该 tid 的 RSS 源故障，不是真正的 miss
        tid_source = rec.get("rss_source", "")
        if not tid_source:
            # 旧记录缺少 rss_source，无法判定归属；有源故障时保守跳过
            if failed_sources:
                dbg(f"  tid={tid} no rss_source, skip count ({len(failed_sources)} source(s) failed)")
                continue
        elif tid_source in failed_sources:
            dbg(f"  tid={tid} source failed (not true miss), skip count")
            continue
        
        # RSS 缺失 -> 计数+1
        new_count = missing_count + 1
        rec["rss_missing_count"] = new_count
        threshold = CFG.get("rss_missing_threshold", 2)
        dbg(f"  tid={tid} missing, count={new_count}/{threshold}")
        
        if new_count >= threshold:
            dbg(f"  -> RSS eviction triggered")
            evict_tid_download(tid)


# ======================================================
# RSS 主逻辑
# ======================================================
def handle_rss(emergency_result: dict) -> Tuple[Set[str], Set[str]]:
    """处理 RSS 源，返回 (rss_tid_set, failed_sources)"""

    rss_sources = get_rss_sources()
    failed_sources: Set[str] = set()
    if not rss_sources:
        print("[ERROR] RSS source is empty (set rss_urls or rss_url)", flush=True)
        return set(), failed_sources

    feeds_data: List[Tuple[Any, str, str, str]] = []
    parsed_ok = 0
    seen_tid: Set[str] = set()

    rss_tid_set: Set[str] = set()
    added = 0

    for src in rss_sources:
        try:
            feed = parse_rss_with_retry(src, timeout=10)
            parsed_ok += 1
            dbg(f"RSS source ok: {src[:80]}..., entries={len(feed.entries)}")
        except Exception as e:
            dbg(f"[RSS] source failed: {src[:80]}..., err={e}")
            failed_sources.add(src)
            continue

        for item in feed.entries:
            url = get_download_url(item)
            tid = extract_tid(url)
            if not tid:
                dbg(f"  skip: cannot extract tid from url={url[:100]}")
                continue

            rss_tid_set.add(tid)
            if tid in seen_tid:
                continue

            seen_tid.add(tid)
            feeds_data.append((item, url, tid, src))

    if parsed_ok == 0:
        print("[ERROR] all RSS sources failed", flush=True)
        return set(), failed_sources

    dbg(f"RSS sources={len(rss_sources)}, parsed={parsed_ok}, unique_tids={len(feeds_data)}")

    for item, url, tid, src in feeds_data:
        if MAX_ACTIVE and added >= MAX_ACTIVE:
            break

        now_iso = utc_now_iso()

        if tid not in processed:
            processed[tid] = {
                "title": item.title,
                "first_seen": now_iso,
                "status": STATUS_PENDING_FREE,
                "rss_missing_count": 0,
                "rss_source": src,
            }

        rec = processed[tid]

        # 终态直接跳过
        if is_final_status(rec.get("status", "")):
            dbg(f"  skip: tid={tid}, status={rec.get('status')} (final)")
            continue

        # Free TTL
        age_hours = hours_since_iso(rec["first_seen"])
        if age_hours > FREE_TTL_HOURS and rec["status"] == STATUS_PENDING_FREE:
            rec["status"] = STATUS_EXPIRED_FREE
            rec["rss_missing_count"] = 0
            daily_state["stats"]["expired_free"] += 1
            dbg(f"  free expired tid={tid}")
            continue

        if rec["status"] != STATUS_PENDING_FREE:
            dbg(f"  skip: tid={tid}, status={rec['status']}")
            continue

        # ===== 添加任务 =====
        tag = f"rss_tid:{tid}"

        # 若任务已存在于 qB，直接同步状态
        existing, existing_state, existing_src = find_existing_torrent(tag, item.title)
        if existing_state == "one" and existing is not None:
            synced = sync_status_from_existing_torrent(rec, tid, item.title, tag, existing)
            save_json(PROCESSED_FILE, processed)
            if synced == STATUS_COMPLETED:
                notify_added.append(f"♻️ 已存在并完成：{item.title[:50]}")
            else:
                notify_added.append(f"♻️ 已存在并在下载：{item.title[:50]}")
            dbg(f"  sync existing ({existing_src}), tid={tid}, status={synced}")
            continue
        if existing_state == "multi":
            reason = f"existing match is multi by {existing_src}: {tag}"
            mark_add_failed(rec, tid, item.title, reason)
            dbg(f"  failed to add {tid}: {reason}")
            continue

        # ===== 空间检查 =====
        cleanup_result = cleanup_for_new_task(CFG["min_free_gb"])

        # 紧急清理后 retry 一次
        if not cleanup_result["ok"] and emergency_result.get("deleted"):
            dbg("  retry space check after emergency cleanup")
            time.sleep(2)
            space2 = qb.free_space_gb()
            if space2 >= CFG["min_free_gb"]:
                dbg(f"  space updated to {space2:.1f}GB, allow add")
                cleanup_result["ok"] = True
                cleanup_result["end_space"] = space2

        if not cleanup_result["ok"]:
            dbg(f"  skip {tid}, space insufficient after cleanup")
            continue

        # ===== 添加任务（策略：add_file 优先 → add(url) 兜底 → passkey 重试）──
        # 核心思路：add(url) 让 qB 自己去下载 .torrent，M-Team 慢会导致 qB 内部超时，
        # 种子根本不出现在列表中。所以 add_file（Python 下载再上传）优先，更可靠。
        added_ok = False
        last_error = ""

        # RSS 每次抓取都生成全新 sign+t（当前时间戳），下载链接是新鲜的不会过期
        # passkey 链接仅做兜底
        passkey_url = build_passkey_download_url(tid)
        urls_to_try: list[str] = [url]
        if passkey_url:
            urls_to_try.append(passkey_url)

        for try_url in urls_to_try:
            label = "passkey" if try_url == passkey_url else "rss"
            # add_file: Python 下载 .torrent → 上传 qB（单次 60s 超时，不重试）
            try:
                qb.add_file(try_url, savepath=CFG["download_dir"], tags=tag)
                added_ok = True
                break
            except QBError as e:
                last_error = str(e)

            # add(url): qB 直接下载（兜底）
            try:
                qb.add(try_url, savepath=CFG["download_dir"], tags=tag)
                added_ok = True
                break
            except QBError as e:
                last_error = str(e)

        if not added_ok:
            existing2, existing_state2, existing_src2 = find_existing_torrent(tag, item.title)
            if existing_state2 == "one" and existing2 is not None:
                synced = sync_status_from_existing_torrent(rec, tid, item.title, tag, existing2)
                save_json(PROCESSED_FILE, processed)
                if synced == STATUS_COMPLETED:
                    notify_added.append(f"♻️ 添加失败但任务已完成：{item.title[:50]}")
                else:
                    notify_added.append(f"♻️ 添加失败但任务已在下载：{item.title[:50]}")
                dbg(f"  add failed but existing found, tid={tid}, status={synced}")
                continue
            mark_add_failed(rec, tid, item.title, last_error)
            dbg(f"  ❌ all add methods failed for tid={tid}: {last_error}")
            continue

        # ── 确认任务已进入 qB（add_file 上传真实数据，应秒入列）──
        confirmed = wait_torrent_added_by_tag(tag, retries=10, interval_sec=1.0)
        if not confirmed:
            confirmed = wait_torrent_added_by_titleorhash(item.title, retries=10, interval_sec=1.0)

        if not confirmed:
            dbg(f"  ⚠️ tid={tid} not in qB after 20s, keeping pending_free")
            continue

        # 确保 tag 写入
        confirmed_t, confirmed_state, _ = find_existing_torrent(tag, item.title)
        if confirmed_state == "one" and confirmed_t is not None:
            try:
                qb.add_tags(confirmed_t.get("hash", ""), tag)
            except Exception:
                pass

        # ===== 状态更新 =====
        daily_state["stats"]["added"] += 1
        rec.update({
            "status": STATUS_ADDED,
            "added_time": utc_now_iso(),
            "tag": tag,
            "title": item.title,
        })
        append_daily_detail("added", {
            "time": rec["added_time"],
            "tid": tid,
            "title": item.title,
            "tag": tag,
        })
        save_json(PROCESSED_FILE, processed)
        save_json(DAILY_FILE, daily_state)

        msg = f"✅ 新增下载：{item.title[:50]}"
        if cleanup_result.get("deleted"):
            lines = [
                f"- {d['name']}（{d['size']:.1f}GB，{d['reason']}）"
                for d in cleanup_result["deleted"]
            ]
            msg += (
                "\n\n**⚠️ 添加前空​​间清理：**\n"
                + "\n".join(lines)
                + f"\n空间变化：{cleanup_result['start_space']:.1f}GB → "
                  f"{cleanup_result['end_space']:.1f}GB"
            )
        notify_added.append(msg)
        added += 1
        dbg(f"  ✅ added {item.title[:60]}")

        if added % 10 == 0:
            save_json(PROCESSED_FILE, processed)
            save_json(DAILY_FILE, daily_state)

    save_json(PROCESSED_FILE, processed)
    save_json(DAILY_FILE, daily_state)
    return rss_tid_set, failed_sources


# ======================================================
# 每日汇总
# ======================================================
def daily_summary():
    """发送每日汇总（仅在成功发送后才标记 date）"""
    notify = CFG.get("feishu_notify", {})
    if not notify.get("on_daily_summary"):
        dbg("[DAILY] daily summary disabled by config")
        return

    now_local = utc_now()
    today = now_local.strftime("%Y-%m-%d")
    local_hour = now_local.hour
    target_hour = int(notify.get("daily_summary_hour", 22))

    if local_hour != target_hour:
        return
    if daily_state.get("date") == today:
        return

    s = daily_state.get("stats", {})
    details = daily_state.get("details", {})
    added_items = details.get("added_items", [])
    deleted_items = details.get("deleted_items", [])

    lines = [
        f"📅 {today} NASPilot 每日汇总",
        "",
        f"📥 新增下载：{s.get('added', 0)}",
        f"🗑️ 删除任务：{len(deleted_items)}",
        f"⏳ Free过期：{s.get('expired_free', 0)}",
    ]

    if added_items:
        lines.append("\n**📥 新增：**")
        for a in added_items[-10:]:
            lines.append(f"- {a.get('title', 'unknown')[:50]}")

    if deleted_items:
        lines.append("\n**🗑️ 删除：**")
        for d in deleted_items[-10:]:
            lines.append(f"- {d.get('name', 'unknown')[:50]} | {d.get('reason', '')}")

    if feishu_send("📊 每日汇总", "\n".join(lines), color="blue"):
        daily_state["date"] = today
        daily_state["stats"] = DEFAULT_STATS.copy()
        daily_state["details"] = DEFAULT_DETAILS.copy()
        save_json(DAILY_FILE, daily_state)
        dbg("[DAILY] sent and reset")


# ======================================================
# processed 垃圾回收
# ======================================================
def gc_processed():
    """清理 processed 中的终态旧记录"""
    gc_cfg = CFG.get("gc", {})
    evicted_days = float(gc_cfg.get("evicted_days", 5))
    expired_days = float(gc_cfg.get("expired_days", 5))
    purged = 0

    for tid in list(processed.keys()):
        rec = processed.get(tid, {})
        status = rec.get("status", "")
        if status == STATUS_EVICTED:
            ts = rec.get("evicted_time", rec.get("deleted_time", ""))
            if ts and hours_since_iso(ts) > evicted_days * 24:
                del processed[tid]
                purged += 1
        elif status == STATUS_EXPIRED_FREE:
            ts = rec.get("first_seen", "")
            if ts and hours_since_iso(ts) > expired_days * 24:
                del processed[tid]
                purged += 1

    if purged:
        save_json(PROCESSED_FILE, processed)
        dbg(f"[GC] purged {purged} records from processed")


# ======================================================
# 主循环
# ======================================================
if __name__ == "__main__":
    if not get_rss_sources():
        print("[ERROR] No RSS sources configured", flush=True)
        exit(1)

    # 紧急空间清理
    emergency_result = cleanup_low_space_with_incomplete()

    # RSS 处理
    rss_tid_set, failed_sources = handle_rss(emergency_result)

    # RSS 驱逐检查
    rss_delayed_eviction_check(rss_tid_set, failed_sources)

    # 定期清理
    cleanup_stuck_periodic()

    # 延迟同步
    sync_pending_from_qb()

    # 垃圾回收
    gc_processed()

    save_json(DAILY_FILE, daily_state)

    # 飞书通知
    if CFG.get("feishu_notify", {}).get("on_add_task") and notify_added:
        feishu_send("✅ 新增下载", "\n".join(notify_added), color="green")

    if CFG.get("feishu_notify", {}).get("on_delete_task") and notify_evicted:
        feishu_send("🗑️ 任务清理", "\n".join(notify_evicted), color="red")

    if notify_add_failed:
        feishu_send("❌ 添加失败", "\n".join(notify_add_failed), color="red")

    if notify_skip_eviction:
        dbg(f"[RSS] Skipped eviction: {len(notify_skip_eviction)} items")

    # 每日汇总
    daily_summary()

    dbg(f"[DONE] Run complete")