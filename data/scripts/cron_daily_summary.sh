#!/bin/sh
set -e

log() {
  echo "[$(date '+%F %T')] $*"
}

# ============================
# 基本检查
# ============================
if [ -z "$FEISHU_WEBHOOK" ]; then
  log "[ERROR] FEISHU_WEBHOOK is empty"
  exit 1
fi

LOG_DIR="/logs"

log "[DEBUG] cron_daily_summary start"

# ============================
# 时间窗口（昨天 06:00 → 今天 06:00）
# ============================
START_TS="$(date -d 'yesterday 06:00' '+%F %T')"
END_TS="$(date -d 'today 06:00' '+%F %T')"

# ============================
# 主任务列表（成功只统计这些）
# ============================
MAIN_JOBS="pt_rss_auto backup_docker_all_core alist_upload update_cloudflare log_cleanup"

FAIL_TMP="/tmp/cron_daily_fail.tmp"
OK_TMP="/tmp/cron_daily_ok.tmp"

> "$FAIL_TMP"
> "$OK_TMP"

# ============================
# ✅ 只扫描「业务日志」，明确排除汇总类日志
# ============================
LOG_FILES=$(ls "$LOG_DIR"/*.log 2>/dev/null \
  | grep -vE 'daily_summary\.log$|cron_daily_summary\.log$|cron_alive\.log$')

if [ -z "${LOG_FILES:-}" ]; then
  log "[WARN] no business log files found under $LOG_DIR"
fi

# ============================
# 一次性扫描（关键）
# ============================
if [ -n "${LOG_FILES:-}" ]; then
  awk -v start="$START_TS" -v end="$END_TS" \
      -v main_jobs="$MAIN_JOBS" \
      -v FAIL_TMP="$FAIL_TMP" \
      -v OK_TMP="$OK_TMP" '

  function is_main(job) {
    split(main_jobs, a, " ")
    for (i in a) if (a[i] == job) return 1
    return 0
  }

  /^\[END\]/ {
    job = $2
    ts  = $3 " " $4
    exitcode = $5

    if (ts < start || ts >= end) next

    # ========= 失败：全量 =========
    if (exitcode != "exit=0") {
      key = job "|" ts "|" exitcode
      if (!(key in seen_fail)) {
        seen_fail[key] = 1
        print > FAIL_TMP
      }
      next
    }

    # ========= 成功：主任务最后一次 =========
    if (is_main(job)) {
      last_ok[job] = $0
    }
  }

  END {
    for (j in last_ok)
      print last_ok[j] > OK_TMP
  }

' $LOG_FILES
fi

# ============================
# 生成摘要文本
# ============================
SUMMARY_TEXT="✅ Cron 每日执行摘要
时间窗口：$START_TS ～ $END_TS

"

if [ -s "$FAIL_TMP" ]; then
  SUMMARY_TEXT="${SUMMARY_TEXT}❌ 失败任务：
$(sort "$FAIL_TMP")

"
fi

if [ -s "$OK_TMP" ]; then
  SUMMARY_TEXT="${SUMMARY_TEXT}✅ 主任务最后一次成功：
$(sort "$OK_TMP")

"
fi

if [ ! -s "$FAIL_TMP" ] && [ ! -s "$OK_TMP" ]; then
  SUMMARY_TEXT="${SUMMARY_TEXT}（该时间窗口内无任务记录）"
fi

log "[INFO] summary generated"
echo "$SUMMARY_TEXT"

# ============================
# 飞书发送
# ============================
#JSON=$(jq -n --arg text "$SUMMARY_TEXT" '{
#  msg_type:"text",
#  content:{ text:$text }
#}')

JSON=$(/usr/bin/jq -n --arg text "$SUMMARY_TEXT" \
  '{msg_type:"text",content:{text:$text}}')

send_feishu_with_retry() {
  max_retries="${FEISHU_MAX_RETRIES:-3}"
  base_delay="${FEISHU_RETRY_DELAY:-2}"
  connect_timeout="${FEISHU_CONNECT_TIMEOUT:-5}"
  total_timeout="${FEISHU_TIMEOUT:-20}"

  i=1
  while [ "$i" -le "$max_retries" ]; do
    resp_file="/tmp/cron_daily_summary_feishu_resp_$$.json"
    http_code=$(
      /usr/bin/curl -sS \
        --connect-timeout "$connect_timeout" \
        --max-time "$total_timeout" \
        -o "$resp_file" \
        -w "%{http_code}" \
        -X POST "$FEISHU_WEBHOOK" \
        -H "Content-Type: application/json" \
        -d "$JSON" \
      || echo "000"
    )

    cf_code=""
    if [ -s "$resp_file" ]; then
      cf_code=$(/usr/bin/jq -r '.code // empty' "$resp_file" 2>/dev/null || true)
    fi

    if [ "$http_code" -ge 200 ] 2>/dev/null && [ "$http_code" -lt 300 ] 2>/dev/null; then
      if [ -z "$cf_code" ] || [ "$cf_code" = "0" ]; then
        rm -f "$resp_file"
        log "[INFO] feishu sent successfully (attempt=$i, http=$http_code, code=${cf_code:-n/a})"
        return 0
      fi
    fi

    log "[WARN] feishu send failed (attempt=$i/$max_retries, http=$http_code, code=${cf_code:-n/a})"
    if [ -s "$resp_file" ]; then
      tail -c 300 "$resp_file" | sed 's/^/[WARN] feishu resp: /'
    fi
    rm -f "$resp_file"

    if [ "$i" -lt "$max_retries" ]; then
      sleep "$((base_delay * i))"
    fi
    i=$((i + 1))
  done

  return 1
}

if ! send_feishu_with_retry; then
  log "[ERROR] feishu send failed after retries"
  exit 1
fi
