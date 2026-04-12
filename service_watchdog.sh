#!/usr/bin/env bash
# service_watchdog.sh — controleert supervisor-services en logt-bestandsrechten
# Draait elke 5 minuten via cron.
set -euo pipefail

LOGS_DIR="/home/dgebbink/projects/technieuwsvandaag/logs"
LOG_OWNER="dgebbink"
SERVICES=("tnv-telegram-bot" "tnv-approval-server")
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

telegram() {
    [[ -z "$TELEGRAM_BOT_TOKEN" || -z "$TELEGRAM_CHAT_ID" ]] && return 0
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d parse_mode="HTML" \
        -d text="$1" > /dev/null
}

fixed=()
alerts=()

# Fix log file ownership
while IFS= read -r f; do
    owner=$(stat -c '%U' "$f")
    if [[ "$owner" != "$LOG_OWNER" ]]; then
        sudo chown "${LOG_OWNER}:${LOG_OWNER}" "$f"
        fixed+=("Rechten hersteld: $(basename "$f") (was: $owner)")
    fi
done < <(find "$LOGS_DIR" -type f)

# Check en herstart gestopte services
for svc in "${SERVICES[@]}"; do
    status=$(sudo supervisorctl status "$svc" 2>/dev/null | awk '{print $2}')
    if [[ "$status" != "RUNNING" ]]; then
        sudo supervisorctl restart "$svc" > /dev/null 2>&1
        sleep 3
        new_status=$(sudo supervisorctl status "$svc" 2>/dev/null | awk '{print $2}')
        if [[ "$new_status" == "RUNNING" ]]; then
            fixed+=("Service herstart: $svc (was: $status)")
        else
            alerts+=("❌ <b>$svc</b> staat op <code>$new_status</code> en kon niet worden herstart.")
        fi
    fi
done

# Stuur melding als er iets gerepareerd of mis is
if (( ${#fixed[@]} > 0 || ${#alerts[@]} > 0 )); then
    body=""
    for msg in "${fixed[@]}"; do body+="🔧 $msg"$'\n'; done
    for msg in "${alerts[@]}"; do body+="$msg"$'\n'; done
    telegram "⚙️ <b>Service Watchdog</b>

${body}"
fi
