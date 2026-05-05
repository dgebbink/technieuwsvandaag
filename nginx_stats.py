#!/usr/bin/env python3
"""
nginx_stats.py — Parse nginx access logs and output visitor stats as JSON.

Runs on the Oracle web server; called via SSH from the local analytics page.
Reads /var/log/nginx/access.log (and rotated copies) using sudo.

Output: single JSON object to stdout.
"""
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta, datetime
from typing import List, Optional

BOT_PATTERNS = re.compile(
    r"bot|crawler|spider|slurp|bingpreview|applebot|facebookexternalhit|"
    r"meta-external|Googlebot|Baiduspider|YandexBot|SemrushBot|AhrefsBot|"
    r"DotBot|PetalBot|BingBot|mj12bot|DataForSeoBot|GPTBot|ClaudeBot|"
    r"PerplexityBot|ia_archiver|archive\.org",
    re.IGNORECASE,
)

LOG_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>[^ "]+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "[^"]*" "(?P<ua>[^"]*)"'
)

PAGE_RE = re.compile(r"^/([a-z0-9][a-z0-9\-/]*)?$")

_SKIP_PATHS = re.compile(
    r"\.php|//|wp-content|wp-includes|wp-json|wp-admin|wp-login|"
    r"xmlrpc|favicon|robots|sitemap|\.well-known|feed|comments|"
    r"\.(css|js|jpg|jpeg|png|gif|svg|ico|woff|ttf|map|txt|xml|gz|zip)$",
    re.IGNORECASE,
)

GEOIP_DB = "/home/ubuntu/dbip-country.mmdb"


# ---------------------------------------------------------------------------
# User-agent helpers
# ---------------------------------------------------------------------------

def _device(ua: str) -> str:
    ua_l = ua.lower()
    if any(x in ua_l for x in ("ipad", "tablet", "kindle")):
        return "Tablet"
    if any(x in ua_l for x in ("iphone", "android", "mobile", "blackberry", "windows phone")):
        return "Mobiel"
    return "Desktop"


def _os(ua: str) -> str:
    if "Windows" in ua:
        return "Windows"
    if "iPhone" in ua or "iPad" in ua:
        return "iOS"
    if "Android" in ua:
        return "Android"
    if "Mac OS X" in ua:
        return "macOS"
    if "Linux" in ua:
        return "Linux"
    return "Overig"


# ---------------------------------------------------------------------------
# GeoIP helper
# ---------------------------------------------------------------------------

def _make_geo_reader():
    try:
        import geoip2.database
        return geoip2.database.Reader(GEOIP_DB)
    except Exception:
        return None


def _country(ip: str, reader) -> str:
    if reader is None:
        return "Onbekend"
    try:
        return reader.country(ip).country.name or "Onbekend"
    except Exception:
        return "Onbekend"


# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------

def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%d/%b/%Y:%H:%M:%S %z")
    except Exception:
        return None


def _read_log(path: str) -> List[str]:
    try:
        cmd = ["sudo", "zcat" if path.endswith(".gz") else "cat", path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout.splitlines()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Main collect
# ---------------------------------------------------------------------------

def collect(days: int = 90) -> dict:
    today = date.today()
    cutoff = today - timedelta(days=days - 1)

    log_files = ["/var/log/nginx/access.log", "/var/log/nginx/access.log.1"]
    for i in range(2, 20):
        log_files.append(f"/var/log/nginx/access.log.{i}.gz")

    geo_reader = _make_geo_reader()

    daily_views: defaultdict[str, int]   = defaultdict(int)
    daily_unique: defaultdict[str, set]  = defaultdict(set)
    page_counter: Counter                 = Counter()
    peak_weekday: List[int]              = [0] * 24
    peak_weekend: List[int]              = [0] * 24
    device_counter: Counter              = Counter()
    os_counter: Counter                  = Counter()
    country_counter: Counter             = Counter()
    seen_ips: dict                       = {}  # ip -> country (cache)

    for log_path in log_files:
        lines = _read_log(log_path)
        for line in lines:
            m = LOG_RE.match(line)
            if not m:
                continue
            ua = m.group("ua")
            if BOT_PATTERNS.search(ua):
                continue
            if int(m.group("status")) != 200:
                continue
            path = m.group("path").split("?")[0].rstrip("/") or "/"
            if _SKIP_PATHS.search(path) or not PAGE_RE.match(path):
                continue
            dt = _parse_dt(m.group("time"))
            if not dt or dt.date() < cutoff:
                continue

            ip      = m.group("ip")
            day_str = dt.date().isoformat()

            daily_views[day_str] += 1
            daily_unique[day_str].add(ip)
            page_counter[path or "/"] += 1

            if dt.weekday() < 5:
                peak_weekday[dt.hour] += 1
            else:
                peak_weekend[dt.hour] += 1

            device_counter[_device(ua)] += 1
            os_counter[_os(ua)] += 1

            if ip not in seen_ips:
                seen_ips[ip] = _country(ip, geo_reader)
            country_counter[seen_ips[ip]] += 1

    if geo_reader:
        geo_reader.close()

    labels        = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    views_series  = [daily_views.get(d, 0)          for d in labels]
    unique_series = [len(daily_unique.get(d, set())) for d in labels]

    week_ago  = (today - timedelta(days=6)).isoformat()
    month_ago = (today - timedelta(days=29)).isoformat()

    def _sum(series, start_label):
        idx = labels.index(start_label) if start_label in labels else 0
        return sum(series[idx:])

    today_idx    = labels.index(today.isoformat()) if today.isoformat() in labels else -1
    views_today  = views_series[today_idx]  if today_idx >= 0 else 0
    unique_today = unique_series[today_idx] if today_idx >= 0 else 0

    return {
        "labels":          labels,
        "views_series":    views_series,
        "unique_series":   unique_series,
        "views_today":     views_today,
        "unique_today":    unique_today,
        "views_7d":        _sum(views_series,  week_ago),
        "unique_7d":       _sum(unique_series, week_ago),
        "views_30d":       _sum(views_series,  month_ago),
        "unique_30d":      _sum(unique_series, month_ago),
        "top_pages":       [{"path": p, "views": c} for p, c in page_counter.most_common(10)],
        "peak_weekday":    peak_weekday,
        "peak_weekend":    peak_weekend,
        "devices":         [{"label": k, "count": v} for k, v in device_counter.most_common()],
        "os":              [{"label": k, "count": v} for k, v in os_counter.most_common()],
        "countries":       [{"label": k, "count": v} for k, v in country_counter.most_common(10)],
    }


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    print(json.dumps(collect(days)))
