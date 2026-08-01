#!/bin/sh

JOB_NAME="$1"
shift

LOG_DIR="/logs"
LOG="$LOG_DIR/${JOB_NAME}.log"
ALERT_LOG="$LOG_DIR/cron_alert.log"

SEP="=================================================="

echo "$SEP" >> "$LOG"

echo "[START] $JOB_NAME  $(date '+%F %T')" >> "$LOG"

set +e
"$@" >> "$LOG" 2>&1
RET=$?
set -e

echo "[END]   $JOB_NAME  $(date '+%F %T') exit=$RET" >> "$LOG"

if [ "$RET" -ne 0 ]; then
  # ✅ 写入统一错误池
  echo "[ERROR] $JOB_NAME  $(date '+%F %T') exit=$RET" >> "$ALERT_LOG"

  if [ -n "$FEISHU_WEBHOOK" ]; then
    # ==================================================
    # 1️⃣ 提取最后一次完整执行日志块（按 SEP 分隔）
    # ==================================================
    LAST_BLOCK=$(
      awk -v sep="$SEP" '
        $0 ~ sep {buf=""; hit=1}
        {if (hit) buf = buf $0 "\n"}
        END {print buf}
      ' "$LOG"
    )

    # ==================================================
    # 2️⃣ 优先提取 error / fatal / exception
    # ==================================================
    ERROR_LINE=$(echo "$LAST_BLOCK" \
      | egrep -i "fatal|exception|traceback|error" \
      | tail -n 1)

    if [ -n "$ERROR_LINE" ]; then
      DETAIL="$ERROR_LINE"
    else
      DETAIL="$LAST_BLOCK"
    fi

    # ==================================================
    # 3️⃣ 长度裁剪，防止飞书 400
    # ==================================================
    DETAIL=$(echo "$DETAIL" | tail -c 1500)

    # ==================================================
    # 4️⃣ 构造飞书消息
    # ==================================================
    JSON=$(/usr/bin/jq -n \
      --arg job "$JOB_NAME" \
      --arg time "$(date '+%F %T')" \
      --arg ret "$RET" \
      --arg detail "$DETAIL" \
      '{
        msg_type: "text",
        content: {
          text: (
            "❌ Cron 任务失败\n"
            + "任务：" + $job + "\n"
            + "时间：" + $time + "\n"
            + "退出码：" + $ret + "\n\n"
            + "最后一次执行日志：\n"
            + $detail
          )
        }
      }'
    )

    /usr/bin/curl -sSf -X POST "$FEISHU_WEBHOOK" \
      -H "Content-Type: application/json" \
      -d "$JSON"
  fi
fi

exit $RET



