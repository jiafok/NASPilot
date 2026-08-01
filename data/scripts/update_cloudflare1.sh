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

# —— 日志/状态（默认用户目录，避免 /var/tmp 权限问题）——
export LOG_BASE="${LOG_BASE:-/logs/cloudflarecfpages}"
export WRANGLER_HOME="${WRANGLER_HOME:-$LOG_BASE/wrangler}"
export WRANGLER_CACHE="${WRANGLER_CACHE:-$WRANGLER_HOME/wrangler-cache}"
export WRANGLER_LOG_DIR="${WRANGLER_LOG_DIR:-$WRANGLER_HOME/logs}"
mkdir -p "$LOG_BASE" "$WRANGLER_HOME" "$WRANGLER_CACHE" "$WRANGLER_LOG_DIR"
LAST_IP_FILE="${LAST_IP_FILE:-$LOG_BASE/last_ipv6.txt}"
LOG_FILE="${LOG_FILE:-$LOG_BASE/pages_deploy.log}"
cd /logs

# —— Wrangler 获取与执行策略 —— 
WRANGLER_TIMEOUT="${WRANGLER_TIMEOUT:-120}"
NPM_REGISTRY="${NPM_REGISTRY:-}"

# 固定使用兼容 Node v20 的 wrangler 版本
WRANGLER_VERSION="3.78.12"

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

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE" >&2; }
need(){ command -v "$1" >/dev/null 2>&1 || { echo "缺少命令：$1" >&2; exit 2; }; }

need node; need npm; need ip; need curl; need jq

try_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then timeout "$secs" "$@"; else "$@"; fi
}

ensure_wrangler() {
  # 优先使用本地缓存的兼容版本
  if [ -x "$WRANGLER_HOME/node_modules/.bin/wrangler" ]; then
    # 检查版本是否兼容
    if "$WRANGLER_HOME/node_modules/.bin/wrangler" --version 2>&1 | grep -q "wrangler"; then
      echo "$WRANGLER_HOME/node_modules/.bin/wrangler"
      return 0
    fi
  fi
  
  # 尝试使用 npx 指定兼容版本
  if command -v npx >/dev/null 2>&1; then
    if try_timeout "$WRANGLER_TIMEOUT" npx -y wrangler@${WRANGLER_VERSION} --version >/dev/null 2>&1; then
      echo "npx -y wrangler@${WRANGLER_VERSION}"
      return 0
    fi
    log "npx 拉取 wrangler 超时/失败，转 npm exec ..."
  fi
  
  export NPM_CONFIG_YES=true NPM_CONFIG_FUND=false NPM_CONFIG_AUDIT=false NPM_CONFIG_PROGRESS=false
  [ -n "$NPM_REGISTRY" ] && export npm_config_registry="$NPM_REGISTRY"
  
  # 尝试使用 npm exec
  if try_timeout "$WRANGLER_TIMEOUT" npm exec --yes wrangler@${WRANGLER_VERSION} -- --version >/dev/null 2>&1; then
    echo "npm exec --yes wrangler@${WRANGLER_VERSION} --"
    return 0
  fi
  
  # 最后尝试本地缓存安装
  log "npm exec 失败，尝试本地缓存安装 wrangler@${WRANGLER_VERSION} ..."
  mkdir -p "$WRANGLER_HOME"
  
  # 删除可能存在的旧版本
  rm -rf "$WRANGLER_HOME/node_modules" "$WRANGLER_HOME/package-lock.json" 2>/dev/null || true
  
  if try_timeout "$WRANGLER_TIMEOUT" npm --prefix "$WRANGLER_HOME" install wrangler@${WRANGLER_VERSION} --silent >/dev/null 2>&1; then
    if [ -x "$WRANGLER_HOME/node_modules/.bin/wrangler" ]; then
      echo "$WRANGLER_HOME/node_modules/.bin/wrangler"
      return 0
    fi
  fi
  
  log "无法安装 wrangler"
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
if [ -z "${CURRENT_IP}" ]; then log "未检测到全局 IPv6，退出。"; exit 0; fi
PREV_IP=""; [ -f "$LAST_IP_FILE" ] && PREV_IP="$(tr -d '\n' < "$LAST_IP_FILE")"
if [ -n "$PREV_IP" ] && [ "$CURRENT_IP" = "$PREV_IP" ]; then log "IPv6 未变化（${CURRENT_IP}），无需部署。"; exit 0; fi
log "检测到 IPv6 变化：${PREV_IP:-无} -> ${CURRENT_IP}"

# 2) 生成静态产物
OUTDIR="$(mktemp -d)"; trap 'rm -rf "${OUTDIR}"' EXIT
NOW_CN="$(date '+%Y-%m-%d %H:%M:%S')"

# —— 组 → HTML 片段
build_services_html() {
  if command -v jq >/dev/null 2>&1; then
    jq -r --arg ip "$CURRENT_IP" '
        map(select(.enabled == true))
        | sort_by(.group, .name)
        | group_by(.group)
        | map({
          title: (.[0].group // "未分组"),
          items: (map(
            "<a class=\"card\" href=\"" +
            (if .ssl then "https://" else "http://" end) +
            "[" + $ip + "]:" + (.port|tostring) + (.path // "") +
            "\" target=\"_blank\" rel=\"noopener\">" +
            "<div class=\"card-title\">" + .name + "</div>" +
            "<div class=\"card-url\">" +
            (if .ssl then "https" else "http" end) + "://" +
            "[" + $ip + "]:" + (.port|tostring) + (.path // "") +
            "</div></a>"
          ))
        })
        | map(
          "<h2 class=\"group\">" + .title + "</h2>\n" +
          "<div class=\"grid\">\n" + (.items | join("\n")) + "\n</div>"
        )
        | join("\n")
    ' <<<"$SERVICES_JSON"
  else
    cat <<HTML
<h2 class="group">示例</h2>
<div class="grid">
  <a class="card" href="http://[${CURRENT_IP}]:5000">
    <div class="card-title">DSM 管理</div>
    <div class="card-url">http://[${CURRENT_IP}]:5000</div>
  </a>
  <a class="card" href="http://[${CURRENT_IP}]:8096">
    <div class="card-title">Emby</div>
    <div class="card-url">http://[${CURRENT_IP}]:8096</div>
  </a>
</div>
HTML
  fi
}
SERVICES_HTML="$(build_services_html)"

# —— index.html
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
    ${SERVICES_HTML}
  </div>
</body>
</html>
EOF

# —— 404.html
cat > "${OUTDIR}/404.html" <<'EOF'
<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>404</title>
<style>body{font-family:system-ui;display:grid;place-items:center;height:100vh;background:#0b1018;color:#e6edf3}
a{color:#93c5fd}</style>
<body><div><h1>404 · 页面未找到</h1><p><a href="/">返回首页</a></p></div></body>
EOF

# —— _headers
cat > "${OUTDIR}/_headers" <<'EOF'
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Cache-Control: no-store
  Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:
EOF

# —— _redirects
cat > "${OUTDIR}/_redirects" <<'EOF'
/*    /index.html   200
EOF

# —— _routes.json
cat > "${OUTDIR}/_routes.json" <<'EOF'
{ "version": 1, "include": ["/*"], "exclude": [] }
EOF

# ====== 鉴权实现 ======
if [ "${BASIC_AUTH_ENABLED}" = "true" ]; then
  if [ "${AUTH_MODE}" = "middleware" ]; then
    mkdir -p "${OUTDIR}/functions"
    cat > "${OUTDIR}/functions/_middleware.js" <<'EOF'
export const onRequest = async ({ request, next }) => {
  const USER = "__BASIC_USER__";
  const PASS = "__BASIC_PASS__";

  const url = new URL(request.url);
  const safeExt = [".css",".js",".png",".jpg",".jpeg",".gif",".svg",".ico",
                   ".webp",".woff",".woff2",".ttf",".eot",".map"];
  if (safeExt.some(ext => url.pathname.endsWith(ext))) {
    return next();
  }

  const auth = request.headers.get("Authorization");
  if (!auth || !auth.startsWith("Basic ")) {
    return new Response("Authentication required", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="Protected"', "Cache-Control":"no-store" }
    });
  }
  try {
    const [, encoded] = auth.split(" ");
    const decoded = atob(encoded);
    const [u, p] = decoded.split(":");
    if (u === USER && p === PASS) return next();
  } catch (_) {}
  return new Response("Unauthorized", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Protected"', "Cache-Control":"no-store" }
  });
};
EOF
    _u_esc="${BASIC_AUTH_USER//\//\\/}"; _u_esc="${_u_esc//&/\\&}"
    _p_esc="${BASIC_AUTH_PASS//\//\\/}"; _p_esc="${_p_esc//&/\\&}"
    sed -i "s/__BASIC_USER__/${_u_esc}/g" "${OUTDIR}/functions/_middleware.js"
    sed -i "s/__BASIC_PASS__/${_p_esc}/g" "${OUTDIR}/functions/_middleware.js"
  else
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
fi

# —— 部署前打印产物目录
log "产物目录结构（前两层）："
find "$OUTDIR" -maxdepth 2 -type f -print | sed "s|$OUTDIR/| - |" | tee -a "$LOG_FILE"

# 3) 调用 wrangler
export CLOUDFLARE_API_TOKEN
export CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN="$(printf '%s' "$CLOUDFLARE_API_TOKEN" | tr -d '\r\n\t ')"

log "准备 wrangler 环境 ..."
WRANGLER_CMD="$(ensure_wrangler 2>/dev/null | tail -n 1)" || { log "无法获得 wrangler 可执行；请检查 npm 网络/registry。"; exit 3; }
log "wrangler 命令：${WRANGLER_CMD}"

CF_API="https://api.cloudflare.com/client/v4"
auth_hdr=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" -H "Content-Type: application/json")
project_exists() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "${auth_hdr[@]}" \
          "${CF_API}/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects/${CF_PAGES_PROJECT_NAME}" || true)"
  [ "$code" = "200" ]
}

log "检查 Pages 项目：${CF_PAGES_PROJECT_NAME}"
if project_exists; then
  log "项目已存在，跳过创建。"
else
  log "项目不存在，尝试创建：${CF_PAGES_PROJECT_NAME}"
  set +e
  CREATE_OUT="$( try_timeout "$TIMEOUT_CREATE" ${WRANGLER_CMD} \
                pages project create "${CF_PAGES_PROJECT_NAME}" --production-branch=main 2>&1 )"
  RET=$?; set -e
  echo "$CREATE_OUT" >> "$LOG_FILE"
  if [ $RET -ne 0 ]; then
    if echo "$CREATE_OUT" | grep -qi "already exists"; then
      log "Cloudflare 返回：项目已存在，视为成功。"
    else
      log "创建项目失败（退出码 $RET），输出（尾部）："
      echo "$CREATE_OUT" | tail -n 80 | tee -a "$LOG_FILE"; exit $RET
    fi
  fi
fi

log "开始部署（${OUTDIR}) ..."
WRANGLER_BIN=($WRANGLER_CMD)
set +e
DEPLOY_OUTPUT="$(
  try_timeout "$TIMEOUT_DEPLOY" \
  "${WRANGLER_BIN[@]}" \
  pages deploy "${OUTDIR}" \
  --project-name="${CF_PAGES_PROJECT_NAME}" 2>&1
)"
RET=$?; set -e
echo "$DEPLOY_OUTPUT" >> "$LOG_FILE"

if [ $RET -ne 0 ] && [ $RET -eq 124 ] && [ "${RETRY_ON_TIMEOUT}" = "1" ]; then
  log "部署因超时中断（124），延长超时至 $((TIMEOUT_DEPLOY*2))s 后重试一次 ..."
  sleep 3
  set +e
  DEPLOY_OUTPUT="$(
    try_timeout "$((TIMEOUT_DEPLOY*2))" \
    "${WRANGLER_BIN[@]}" \
    pages deploy "${OUTDIR}" \
    --project-name="${CF_PAGES_PROJECT_NAME}" 2>&1
  )"
  RET=$?
  set -e
  echo "$DEPLOY_OUTPUT" >> "$LOG_FILE"
fi

if [ $RET -ne 0 ]; then
  log "部署失败；wrangler 退出码：$RET"
  echo "$DEPLOY_OUTPUT" | tail -n 120 | tee -a "$LOG_FILE"
  exit $RET
fi

# 4) 成功后更新
echo -n "${CURRENT_IP}" > "${LAST_IP_FILE}"

DEPLOY_URL="$(echo "$DEPLOY_OUTPUT" | grep -Eo 'https://[a-zA-Z0-9._-]+\.pages\.dev' | head -n 1 || true)"
log "部署成功 ✅ 访问地址：${DEPLOY_URL:-<未解析，见日志>}"
log "当前 IPv6：${CURRENT_IP}"
log "日志文件：${LOG_FILE}"