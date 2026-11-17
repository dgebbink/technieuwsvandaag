#!/usr/bin/env bash
# service_watchdog.sh — controleert supervisor-services en logt-bestandsrechten
# Draait elke 5 minuten via cron.
set -euo pipefail

PROJECT_DIR="/home/dgebbink/projects/technieuwsvandaag"
LOGS_DIR="${PROJECT_DIR}/logs"
LOG_OWNER="dgebbink"
SERVICES=("tnv-approval-server")

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
            alerts+=("FOUT: $svc staat op $new_status en kon niet worden herstart.")
        fi
    fi
done

# Meld wat er gerepareerd of mis is in de log (cron schrijft stdout naar
# logs/service_watchdog.log). Telegram is op 2026-11-17 verwijderd: niet gebruikt.
if (( ${#fixed[@]} > 0 || ${#alerts[@]} > 0 )); then
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    for msg in "${fixed[@]}"; do echo "[$ts] $msg"; done
    for msg in "${alerts[@]}"; do echo "[$ts] $msg"; done
fi
