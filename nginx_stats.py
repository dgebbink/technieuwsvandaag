#!/usr/bin/env python3
"""
nginx_stats.py — Parse nginx access logs and output visitor stats as JSON.

Runs on the Oracle web server; called via SSH from the local analytics page.
Reads /var/log/nginx/access.log (and .1 for yesterday's tail) using sudo.

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

# Nginx combined log line pattern
LOG_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>[^ "]+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "[^"]*" "(?P<ua>[^"]*)"'
)

# Only count real article/page requests: clean slugs, no PHP, no double-slash, no attacks
PAGE_RE = re.compile(r"^/([a-z0-9][a-z0-9\-/]*)?$")  # slug chars only, optional trailing parts

_SKIP_PATHS = re.compile(
    r"\.php|//|wp-content|wp-includes|wp-json|wp-admin|wp-login|"
    r"xmlrpc|favicon|robots|sitemap|\.well-known|feed|comments|"
    r"\.(css|js|jpg|jpeg|png|gif|svg|ico|woff|ttf|map|txt|xml|gz|zip)$",
    re.IGNORECASE,
)


def _parse_time(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s, "%d/%b/%Y:%H:%M:%S %z").date()
    except Exception:
        return None


def _read_log(path: str, use_sudo: bool = True) -> List[str]:
    try:
        if path.endswith(".gz"):
            cmd = ["sudo", "zcat", path] if use_sudo else ["zcat", path]
        else:
            cmd = ["sudo", "cat", path] if use_sudo else ["cat", path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout.splitlines()
    except Exception:
        return []


def collect(days: int = 90) -> dict:
    today = date.today()
    cutoff = today - timedelta(days=days - 1)

    # Determine which log files to read — uncompressed + up to 90 days of .gz
    log_files = ["/var/log/nginx/access.log", "/var/log/nginx/access.log.1"]
    for i in range(2, 20):
        log_files.append(f"/var/log/nginx/access.log.{i}.gz")

    # Read and parse
    daily_views: defaultdict[str, int]    = defaultdict(int)
    daily_unique: defaultdict[str, set]   = defaultdict(set)
    page_counter: Counter                  = Counter()

    for log_path in log_files:
        lines = _read_log(log_path)
        for line in lines:
            m = LOG_RE.match(line)
            if not m:
                continue
            ua = m.group("ua")
            if BOT_PATTERNS.search(ua):
                continue
            status = int(m.group("status"))
            if status != 200:
                continue
            path = m.group("path").split("?")[0].rstrip("/") or "/"
            if _SKIP_PATHS.search(path):
                continue
            if not PAGE_RE.match(path):
                continue
            day = _parse_time(m.group("time"))
            if not day or day < cutoff:
                continue
            ip = m.group("ip")
            day_str = day.isoformat()
            daily_views[day_str] += 1
            daily_unique[day_str].add(ip)
            # Normalise path: strip trailing slash and query
            clean = path or "/"
            page_counter[clean] += 1

    # Build 90-day time series
    labels = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    views_series   = [daily_views.get(d, 0)         for d in labels]
    unique_series  = [len(daily_unique.get(d, set())) for d in labels]

    week_ago  = (today - timedelta(days=6)).isoformat()
    month_ago = (today - timedelta(days=29)).isoformat()

    def _sum(series, start_label):
        idx = labels.index(start_label) if start_label in labels else 0
        return sum(series[idx:])

    today_str   = today.isoformat()
    today_idx   = labels.index(today_str) if today_str in labels else -1
    views_today  = views_series[today_idx]  if today_idx >= 0 else 0
    unique_today = unique_series[today_idx] if today_idx >= 0 else 0

    top_pages = [
        {"path": p, "views": c}
        for p, c in page_counter.most_common(10)
    ]

    return {
        "labels":        labels,
        "views_series":  views_series,
        "unique_series": unique_series,
        "views_today":   views_today,
        "unique_today":  unique_today,
        "views_7d":      _sum(views_series,  week_ago),
        "unique_7d":     _sum(unique_series, week_ago),
        "views_30d":     _sum(views_series,  month_ago),
        "unique_30d":    _sum(unique_series, month_ago),
        "top_pages":     top_pages,
    }


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    print(json.dumps(collect(days)))
