#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Cloudflare Pages：IPv6 变化触发部署（API Token 模式）
# ============================================================

########################################
#              配 置 区
########################################

# —— Cloudflare 凭据（必填）——
CLOUDFLARE_API_TOKEN="pkihXkGzj9Dc8TGc7I877XiD74avwYcr93vRL6ad"
CLOUDFLARE_ACCOUNT_ID="ed316079e1ac460eed5d755843b78e50"
CF_PAGES_PROJECT_NAME="nas"
AUTH_MODE="worker"
# —— Basic Auth（访问密码）——        
BASIC_AUTH_ENABLED=true                                                          
BASIC_AUTH_USER="jeffrey"
BASIC_AUTH_PASS="my2026"

# ---- 分阶段超时（秒） ----
TIMEOUT_LIST="${TIMEOUT_LIST:-120}"
TIMEOUT_CREATE="${TIMEOUT_CREATE:-180}"
TIMEOUT_DEPLOY="${TIMEOUT_DEPLOY:-600}"
RETRY_ON_TIMEOUT="${RETRY_ON_TIMEOUT:-1}"

# —— 指定网卡（可选）——
IFACE=""

# —— 日志/状态——
export LOG_BASE="${LOG_BASE:-/logs/cloudflarecfpages}"
export WRANGLER_HOME="${WRANGLER_HOME:-$LOG_BASE/wrangler}"
mkdir -p "$LOG_BASE" "$WRANGLER_HOME"
LAST_IP_FILE="${LAST_IP_FILE:-$LOG_BASE/last_ipv6.txt}"
LOG_FILE="${LOG_FILE:-$LOG_BASE/pages_deploy.log}"

# —— Wrangler 配置 —— 
WRANGLER_TIMEOUT="${WRANGLER_TIMEOUT:-120}"
NPM_REGISTRY="${NPM_REGISTRY:-}"

# —— 服务清单 ——
SERVICES_JSON='
[
  {"group":"Synology 管理","name":"DSM(HTTP)","port":5000,"ssl":false,"path":"","enabled":true},
  {"group":"Synology 管理","name":"DSM(HTTPS)","port":5001,"ssl":true,"path":"","enabled":false},
  {"group":"Synology 管理","name":"File Station","port":7000,"ssl":false,"path":"","enabled":false},
  {"group":"Synology 管理","name":"File Station(HTTPS)","port":7001,"ssl":true,"path":"","enabled":false},
  {"group":"媒体服务","name":"Emby","port":8098,"ssl":false,"path":"","enabled":true},
  {"group":"媒体服务","name":"Emby(HTTPS)","port":8922,"ssl":true,"path":"","enabled":false},
  {"group":"媒体服务","name":"Jellyfin","port":8096,"ssl":false,"path":"","enabled":false},
  {"group":"媒体服务","name":"Plex","port":32400,"ssl":false,"path":"","enabled":false},
  {"group":"媒体服务","name":"PhotoPrism","port":2342,"ssl":false,"path":"","enabled":false},
  {"group":"下载影音","name":"qBittorrent","port":8080,"ssl":false,"path":"","enabled":true},
  {"group":"下载影音","name":"Transmission","port":9091,"ssl":false,"path":"","enabled":true},
  {"group":"下载影音","name":"MoviePilot","port":3002,"ssl":false,"path":"","enabled":true},
  {"group":"下载影音","name":"IYUUPlus","port":8787,"ssl":false,"path":"","enabled":true},
  {"group":"下载影音","name":"MoviePilot-v2","port":3000,"ssl":false,"path":"","enabled":true},
  {"group":"文件与网盘","name":"Alist","port":5266,"ssl":false,"path":"","enabled":true},
  {"group":"文件与网盘","name":"Syncthing","port":8384,"ssl":false,"path":"","enabled":false},
  {"group":"文件与网盘","name":"Nextcloud","port":8081,"ssl":false,"path":"","enabled":false},
  {"group":"监控与DevOps","name":"Grafana","port":3000,"ssl":false,"path":"","enabled":false},
  {"group":"监控与DevOps","name":"Prometheus","port":9090,"ssl":false,"path":"","enabled":false},
  {"group":"监控与DevOps","name":"Node-RED","port":1880,"ssl":false,"path":"","enabled":false},
  {"group":"监控与DevOps","name":"code-server","port":8082,"ssl":false,"path":"","enabled":false},
  {"group":"网络与网关","name":"v2rayA(Web)","port":2017,"ssl":false,"path":"","enabled":true},
  {"group":"网络与网关","name":"AdGuard Home","port":3000,"ssl":false,"path":"","enabled":false},
  {"group":"桌面/浏览器","name":"msedge(noVNC)","port":11124,"ssl":false,"path":"","enabled":true},
  {"group":"工具应用","name":"CloudSaver","port":8008,"ssl":false,"path":"","enabled":true}
]'

########################################
#            以 下 勿 动
########################################
export CI=1
export NO_COLOR=1
export WRANGLER_TELEMETRY_DISABLE=1
export WRANGLER_SEND_METRICS=0

# —— 自动选择 UTF-8 locale ——
detect_utf8_locale() {
  for L in C.UTF-8 en_US.UTF-8 zh_CN.UTF-8; do
    if locale -a 2>/dev/null | grep -qi "^${L}$"; then echo "$L"; return 0; fi
  done
  return 1
}
if UTF8_LOCALE="$(detect_utf8_locale)"; then
  export LANG="$UTF8_LOCALE"
  export LC_ALL="$UTF8_LOCALE"
else
  export LANG=C
  unset LC_ALL
fi

log() { 
    echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"
}

need() { 
    command -v "$1" >/dev/null 2>&1 || { echo "缺少命令：$1" >&2; exit 2; }
}

need node
need npm
need ip
need curl
need jq

try_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$secs" "$@"; else "$@"; fi
}

ensure_wrangler() {
  # 使用兼容 Node v20 的 wrangler 版本
  local WRANGLER_VERSION="3.78.12"  # 最后一个支持 Node v20 的版本
  
  # 先尝试使用全局安装的 wrangler
  if command -v wrangler >/dev/null 2>&1; then
    # 检查版本
    if wrangler --version 2>&1 | grep -q "Wrangler"; then
      echo "wrangler" >&2
      echo "wrangler"
      return 0
    fi
  fi
  
  # 使用 npx 指定版本
  if command -v npx >/dev/null 2>&1; then
    if try_timeout "$WRANGLER_TIMEOUT" npx -y wrangler@${WRANGLER_VERSION} --version >/dev/null 2>&1; then
      echo "npx -y wrangler@${WRANGLER_VERSION}" >&2
      echo "npx -y wrangler@${WRANGLER_VERSION}"
      return 0
    fi
    log "npx 拉取 wrangler 超时/失败，转 npm exec ..." >&2
  fi
  
  # 尝试本地安装指定版本
  log "尝试本地安装 wrangler ${WRANGLER_VERSION} ..." >&2
  mkdir -p "$WRANGLER_HOME"
  
  if try_timeout "$WRANGLER_TIMEOUT" npm install -g --prefix "$WRANGLER_HOME" wrangler@${WRANGLER_VERSION} --silent >/dev/null 2>&1; then
    if [ -f "$WRANGLER_HOME/bin/wrangler" ]; then
      echo "$WRANGLER_HOME/bin/wrangler" >&2
      echo "$WRANGLER_HOME/bin/wrangler"
      return 0
    fi
  fi
  
  log "无法安装 wrangler" >&2
  return 1
}

get_global_ipv6() {
  if [ -n "${IFACE}" ]; then
    ip -6 addr show dev "${IFACE}" scope global 2>/dev/null | awk '/inet6/{print $2}' | cut -d/ -f1 \
    | grep -viE '^(fe80:|fd[0-9a-f]{2}:|fc[0-9a-f]{2}:)' | head -n1
  else
    ip -6 addr show scope global 2>/dev/null | awk '/inet6/{print $2}' | cut -d/ -f1 \
    | grep -viE '^(fe80:|fd[0-9a-f]{2}:|fc[0-9a-f]{2}:)' | head -n1
  fi
}

# 1) IPv6 变化判定
CURRENT_IP="$(get_global_ipv6 || true)"
if [ -z "${CURRENT_IP}" ]; then 
    log "未检测到全局 IPv6，退出。" 
    exit 0
fi

PREV_IP=""
[ -f "$LAST_IP_FILE" ] && PREV_IP="$(cat "$LAST_IP_FILE")"
if [ -n "$PREV_IP" ] && [ "$CURRENT_IP" = "$PREV_IP" ]; then 
    log "IPv6 未变化（${CURRENT_IP}），无需部署。" 
    exit 0
fi

log "检测到 IPv6 变化：${PREV_IP:-无} -> ${CURRENT_IP}"

# 2) 生成静态产物
OUTDIR="$(mktemp -d)"
trap 'rm -rf "${OUTDIR}"' EXIT
NOW_CN="$(date '+%Y-%m-%d %H:%M:%S')"

# 生成 HTML
cat > "${OUTDIR}/index.html" <<EOF
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>家庭 NAS 控制面板</title>
<style>
  :root{
    --bg:#0b1018;--panel:#0f1520;--accent:#60a5fa;--accent2:#34d399;
    --border:#263041;--text:#e6edf3;--sub:#8b949e;--radius:14px
  }
  *{box-sizing:border-box}
  body{margin:0;background:linear-gradient(180deg,#0b1018 0%,#111827 100%);color:var(--text);
       font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial}
  .wrap{max-width:1100px;margin:0 auto;padding:36px 20px}
  h1{margin:0 0 6px;font-size:34px;letter-spacing:.5px}
  .meta{color:var(--sub);font-size:14px}
  .group{margin:26px 0 12px 2px;font-size:16px;font-weight:600;color:#cbd5e1}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
  .card{
    display:block;text-decoration:none;color:inherit;background:rgba(255,255,255,.04);
    border:1px solid var(--border);border-radius:var(--radius);padding:14px 14px;
    box-shadow:0 10px 30px rgba(0,0,0,.28);backdrop-filter:blur(8px);transition:all .15s
  }
  .card:hover{border-color:var(--accent);transform:translateY(-3px)}
  .card-title{font-size:16px;margin-bottom:6px}
  .card-url{font-size:12.5px;color:var(--sub);word-break:break-all}
  code{background:rgba(255,255,255,.05);border:1px solid var(--border);
       border-radius:8px;padding:.18rem .4rem}
  .header{
    display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:8px
  }
  .tag{font-size:12px;color:#a7f3d0;border:1px solid #115e59;padding:2px 8px;border-radius:999px;background:#064e3b22}
</style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <h1>家庭 NAS 控制面板</h1>
      <span class="tag">IPv6：<code>[${CURRENT_IP}]</code></span>
      <span class="tag">更新时间：${NOW_CN}</span>
    </div>
    <div class="grid">
EOF

# 生成服务卡片
echo "$SERVICES_JSON" | jq -r --arg ip "$CURRENT_IP" '
    .[] | select(.enabled == true) | 
    "<a class=\"card\" href=\"" +
    (if .ssl then "https://" else "http://" end) +
    "[" + $ip + "]:" + (.port|tostring) + (.path // "") +
    "\" target=\"_blank\" rel=\"noopener\">" +
    "<div class=\"card-title\">" + .name + "</div>" +
    "<div class=\"card-url\">" +
    (if .ssl then "https" else "http" end) + "://" +
    "[" + $ip + "]:" + (.port|tostring) + (.path // "") +
    "</div></a>"
' >> "${OUTDIR}/index.html"

cat >> "${OUTDIR}/index.html" <<EOF
    </div>
  </div>
</body>
</html>
EOF

# 其他文件
cat > "${OUTDIR}/404.html" <<'EOF'
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>404</title>
<style>body{font-family:system-ui;display:grid;place-items:center;height:100vh;background:#0b1018;color:#e6edf3}
a{color:#93c5fd}</style>
<body><div><h1>404 · 页面未找到</h1><p><a href="/">返回首页</a></p></div></body>
EOF

cat > "${OUTDIR}/_headers" <<'EOF'
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Cache-Control: no-store
EOF

cat > "${OUTDIR}/_redirects" <<'EOF'
/*    /index.html   200
EOF

cat > "${OUTDIR}/_routes.json" <<'EOF'
{ "version": 1, "include": ["/*"], "exclude": [] }
EOF

# Basic Auth
if [ "${BASIC_AUTH_ENABLED}" = "true" ]; then
    cat > "${OUTDIR}/_worker.js" <<EOF
export default {
  async fetch(request, env) {
    const USER = "${BASIC_AUTH_USER}";
    const PASS = "${BASIC_AUTH_PASS}";
    
    const url = new URL(request.url);
    const safeExt = [".css",".js",".png",".jpg",".jpeg",".gif",".svg",".ico",
                     ".webp",".woff",".woff2",".ttf",".eot",".map"];
    if (safeExt.some(ext => url.pathname.endsWith(ext))) {
      return env.ASSETS.fetch(request);
    }
    
    const auth = request.headers.get("Authorization") || "";
    if (!auth.startsWith("Basic ")) {
      return new Response("Authentication required", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="Protected"', "Cache-Control":"no-store" }
      });
    }
    try {
      const decoded = atob(auth.replace("Basic ",""));
      const [u,p] = decoded.split(":");
      if (u === USER && p === PASS) return env.ASSETS.fetch(request);
    } catch (_) {}
    return new Response("Unauthorized", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="Protected"', "Cache-Control":"no-store" }
    });
  }
}
EOF
fi

log "产物目录结构："
find "$OUTDIR" -type f | sed "s|$OUTDIR/| - |" | tee -a "$LOG_FILE"

# 3) 部署
export CLOUDFLARE_API_TOKEN
export CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN="$(printf '%s' "$CLOUDFLARE_API_TOKEN" | tr -d '\r\n\t ')"

log "准备 wrangler 环境 ..."
WRANGLER_CMD="$(ensure_wrangler 2>/dev/null | tail -n 1)" || { 
    log "无法获得 wrangler 可执行；请检查 npm 网络/registry。" 
    exit 3
}
log "wrangler 命令：${WRANGLER_CMD}"

# 检查命令是否存在
if ! command -v ${WRANGLER_CMD%% *} >/dev/null 2>&1 && [ ! -f "${WRANGLER_CMD%% *}" ]; then
    log "错误：wrangler 命令不存在"
    exit 3
fi

log "开始部署（${OUTDIR}) ..."

set +e
DEPLOY_OUTPUT="$(try_timeout "$TIMEOUT_DEPLOY" ${WRANGLER_CMD} pages deploy "${OUTDIR}" --project-name="${CF_PAGES_PROJECT_NAME}" 2>&1)"
RET=$?
set -e

echo "$DEPLOY_OUTPUT" >> "$LOG_FILE"

if [ $RET -ne 0 ]; then
    log "部署失败；wrangler 退出码：$RET"
    echo "$DEPLOY_OUTPUT" | tail -n 50 | tee -a "$LOG_FILE"
    exit $RET
fi

# 4) 成功后更新
echo -n "${CURRENT_IP}" > "${LAST_IP_FILE}"

DEPLOY_URL="$(echo "$DEPLOY_OUTPUT" | grep -Eo 'https://[a-zA-Z0-9._-]+\.pages\.dev' | head -n 1 || true)"
log "部署成功 ✅ 访问地址：${DEPLOY_URL:-<未解析，见日志>}"
log "当前 IPv6：${CURRENT_IP}"
log "日志文件：${LOG_FILE}"