#!/usr/bin/env bash
# ssl_watchdog.sh — controleert SSL-certificaten en repareert nginx-config automatisch
# Stuurt Telegram-melding bij problemen of fixes.
set -euo pipefail

### Config ###
NGINX_SITES="/etc/nginx/sites-available"
LETSENCRYPT_LIVE="/etc/letsencrypt/live"
WARN_DAYS=30          # waarschuw als cert binnen N dagen verloopt
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
LOG_FILE="/var/log/ssl_watchdog.log"

### Helpers ###
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

telegram() {
    local msg="$1"
    [[ -z "$TELEGRAM_BOT_TOKEN" || -z "$TELEGRAM_CHAT_ID" ]] && return 0
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d parse_mode="HTML" \
        -d text="$msg" > /dev/null
}

# Geeft resterende dagen terug voor een PEM-bestand; -1 bij fout
cert_days_left() {
    local pem="$1"
    local expiry
    expiry=$(openssl x509 -enddate -noout -in "$pem" 2>/dev/null | cut -d= -f2) || { echo -1; return; }
    local exp_epoch now_epoch
    exp_epoch=$(date -d "$expiry" +%s 2>/dev/null) || { echo -1; return; }
    now_epoch=$(date +%s)
    echo $(( (exp_epoch - now_epoch) / 86400 ))
}

# Zoek de beste beschikbare cert voor een domein
best_cert_for_domain() {
    local domain="$1"
    local best_cert="" best_days=-999
    for cert_dir in "$LETSENCRYPT_LIVE"/*/; do
        local pem="${cert_dir}fullchain.pem"
        [[ -f "$pem" ]] || continue
        # Check of domain in de cert staat (SAN of CN)
        if openssl x509 -text -noout -in "$pem" 2>/dev/null \
                | grep -qE "DNS:${domain}|CN\s*=\s*${domain}"; then
            local days
            days=$(cert_days_left "$pem")
            if (( days > best_days )); then
                best_days=$days
                best_cert="${cert_dir%/}"
            fi
        fi
    done
    echo "$best_cert"
}

### Hoofd-logica ###
nginx_changed=0
messages=()
issues=0

log "=== SSL watchdog gestart ==="

# Loop over alle nginx site-configs
for conf in "$NGINX_SITES"/*; do
    [[ -f "$conf" ]] || continue

    # Pak ssl_certificate regels
    while IFS= read -r line; do
        cert_path=$(echo "$line" | awk '{print $2}' | tr -d ';')
        [[ -f "$cert_path" ]] || continue

        days=$(cert_days_left "$cert_path")
        cert_name=$(basename "$(dirname "$cert_path")")
        log "Cert '$cert_name' in $(basename "$conf"): ${days} dagen geldig"

        if (( days < 0 )); then
            # Verlopen — probeer betere cert te vinden voor elk domein in deze config
            domains=$(grep -Po 'server_name\s+\K[^;]+' "$conf" | tr ' ' '\n' \
                | grep -v '_' | head -1)
            log "VERLOPEN: $cert_path — zoek betere cert voor '$domains'"
            issues=1

            best=$(best_cert_for_domain "$domains")
            if [[ -n "$best" && "$(dirname "$cert_path")" != "$best" ]]; then
                new_full="${best}/fullchain.pem"
                new_key="${best}/privkey.pem"
                log "Fix: vervang '$cert_path' door '$new_full' in $(basename "$conf")"
                sudo sed -i \
                    -e "s|$(dirname "$cert_path")/fullchain.pem|$new_full|g" \
                    -e "s|$(dirname "$cert_path")/privkey.pem|$new_key|g" \
                    "$conf"
                nginx_changed=1
                messages+=("🔧 <b>Nginx auto-fix</b> in <code>$(basename "$conf")</code>: verwees naar verlopen cert <code>$cert_name</code>, nu gecorrigeerd naar <code>$(basename "$best")</code>.")
            else
                # Probeer certbot vernieuwen
                if sudo certbot renew --cert-name "$cert_name" --non-interactive 2>&1 | tee -a "$LOG_FILE"; then
                    log "Certbot vernieuwd: $cert_name"
                    messages+=("✅ Certbot heeft <code>$cert_name</code> succesvol vernieuwd.")
                    nginx_changed=1
                else
                    log "FOUT: certbot kon '$cert_name' niet vernieuwen"
                    messages+=("❌ <b>Cert verlopen en vernieuwen mislukt:</b> <code>$cert_name</code> in <code>$(basename "$conf")</code>. Handmatige actie vereist!")
                fi
            fi

        elif (( days <= WARN_DAYS )); then
            # Verloopt binnenkort — probeer vernieuwen
            log "WAARSCHUWING: cert '$cert_name' verloopt over $days dagen — vernieuw"
            if sudo certbot renew --cert-name "$cert_name" --non-interactive 2>&1 | tee -a "$LOG_FILE"; then
                new_days=$(cert_days_left "$cert_path")
                log "Vernieuwd: $cert_name — nu nog $new_days dagen"
                messages+=("✅ Cert <code>$cert_name</code> vernieuwd (was $days dagen, nu $new_days dagen).")
                nginx_changed=1
            else
                issues=1
                messages+=("⚠️ <b>Cert bijna verlopen:</b> <code>$cert_name</code> verloopt over <b>$days dagen</b> en vernieuwen mislukte. Check DNS of server bereikbaarheid.")
            fi
        fi

    done < <(grep 'ssl_certificate ' "$conf" | grep -v 'ssl_certificate_key')
done

# Herlaad nginx als er iets veranderd is
if (( nginx_changed )); then
    if sudo nginx -t 2>&1 | tee -a "$LOG_FILE"; then
        sudo systemctl reload nginx
        log "Nginx herladen na wijzigingen"
    else
        log "FOUT: nginx config ongeldig na aanpassingen — reload overgeslagen"
        messages+=("❌ <b>Nginx config ongeldig</b> na automatische aanpassingen — nginx NIET herladen. Controleer direct!")
        issues=1
    fi
fi

# Stuur Telegram-bericht
if (( ${#messages[@]} > 0 )); then
    body=$(printf '%s\n' "${messages[@]}")
    telegram "🔒 <b>SSL Watchdog — $(hostname)</b>

$body"
elif (( issues == 0 )); then
    log "Alle certificaten OK"
fi

log "=== SSL watchdog klaar ==="
