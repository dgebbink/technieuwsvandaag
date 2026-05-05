#!/usr/bin/env python3
"""
Flask approval server for TechNieuwsVandaag article review.
Runs on LAN, handles Decline and New Image button clicks.
Decline deletes the Bluesky post and WordPress post.
New Image regenerates the featured image without consuming the token.
"""

import html as _html_mod
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, request, jsonify
from dotenv import load_dotenv

from approval_store import get_token, mark_used, cleanup_expired, update_bluesky_uri
from wordpress_client import delete_post, update_featured_image
from social_poster import delete_bluesky_post
from mailer import send_reimage_email

load_dotenv()
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/approval_server.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)

# In-memory job store for dashboard submit requests
_jobs: dict = {}  # job_id -> {"status": "pending"/"done"/"error", "result": dict}


@app.route("/decline/<token>")
def decline(token: str):
    """Handles Decline button click.
    Pre:  token is a URL-safe string from the email button
    Post: Bluesky post deleted (if URI present), WordPress post deleted,
          token marked used — or error page if invalid/expired
    """
    entry = get_token(token)
    if not entry or entry["action"] != "decline":
        logging.warning(f"Invalid/expired decline token: {token[:8]}…")
        return _html_response(
            "Link ongeldig of verlopen",
            "Deze Decline-link is niet meer geldig (max 4 uur na publicatie).",
            error=True,
        )

    post_id      = entry["post_id"]
    post_title   = entry["post_title"]
    bluesky_uri  = entry.get("bluesky_uri", "")

    logging.info(f"Declining post {post_id}: {post_title}")
    mark_used(token)

    bsky_msg = ""
    if bluesky_uri:
        try:
            ok = delete_bluesky_post(bluesky_uri)
            bsky_msg = (
                "Bluesky post verwijderd.<br>"
                if ok else
                "Bluesky post verwijderen mislukt (handmatig verwijderen).<br>"
            )
            logging.info(f"Bluesky delete {'OK' if ok else 'FAILED'} for {bluesky_uri}")
        except Exception as e:
            bsky_msg = f"Bluesky fout: {e}<br>"
            logging.error(f"Bluesky delete failed for {post_id}: {e}")
    else:
        bsky_msg = "Geen Bluesky post gevonden (overgeslagen).<br>"
        logging.info(f"No bluesky_uri for post {post_id} — skipping Bluesky delete")

    try:
        delete_post(post_id)
        logging.info(f"Deleted WordPress post {post_id}")
        return _html_response(
            "Artikel verwijderd",
            f"<b>{post_title}</b> is verwijderd.<br><br>"
            f"{bsky_msg}",
        )

    except Exception as e:
        logging.error(f"WordPress delete failed for {post_id}: {e}")
        return _html_response(
            "Verwijderen mislukt",
            f"{bsky_msg}"
            f"WordPress-fout: {str(e)}<br>Verwijder handmatig via WordPress admin.",
            error=True,
        )


@app.route("/new-image/<token>")
def new_image(token: str):
    """Handles New Image button click.
    Pre:  token is a URL-safe string from the email button
    Post: new image generated via FAL.ai and set as featured image on WordPress post;
          token is NOT marked used so it can be clicked multiple times within 4 hours
    """
    entry = get_token(token)
    if not entry or entry["action"] != "new_image":
        logging.warning(f"Invalid/expired new_image token: {token[:8]}…")
        return _html_response(
            "Link ongeldig of verlopen",
            "Deze Nieuwe-afbeelding-link is niet meer geldig (max 4 uur na publicatie).",
            error=True,
        )

    post_id      = entry["post_id"]
    post_title   = entry["post_title"]
    meta         = entry.get("meta", {})
    article_text = meta.get("article_text", post_title)

    logging.info(f"New image request for post {post_id}: {post_title}")
    # Token intentionally NOT marked used — stays clickable until expiry

    def _do_new_image():
        try:
            from image_generator import generate_image_for_article
            dest = f"/tmp/tnv_reimage_{post_id}.jpg"
            new_image_path = generate_image_for_article(
                title=post_title,
                article_text=article_text,
                dest_path=dest,
                dry_run=False,
            )

            if not new_image_path:
                logging.error(f"FAL.ai image generation failed for post {post_id}")
                return

            new_image_url = update_featured_image(
                post_id, new_image_path, alt_text=post_title
            )
            logging.info(f"New image set for post {post_id}: {new_image_url}")

        except Exception as e:
            logging.error(f"New image failed for {post_id}: {e}")

    threading.Thread(target=_do_new_image, daemon=True).start()

    return _html_response(
        "Nieuwe afbeelding wordt gegenereerd",
        f"Een nieuwe afbeelding wordt op de achtergrond aangemaakt voor <b>{post_title}</b>.<br><br>"
        f"De afbeelding verschijnt binnen enkele minuten op de website.",
    )


@app.route("/submit", methods=["POST"])
def submit():
    """Dashboard endpoint: submit a URL for article generation.
    Pre:  JSON body with {"url": "https://..."}
    Post: returns {"job_id": "..."} immediately; processing runs in background
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "Ongeldige URL"}), 400

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending"}
    logging.info(f"Dashboard submit job {job_id}: {url}")

    def _run():
        try:
            from adhoc_processor import process_single_url
            result = process_single_url(url)
            if result and result.get("wp_url"):
                _jobs[job_id] = {"status": "done", "result": result}
                logging.info(f"Job {job_id} done: {result['wp_url']}")
            else:
                _jobs[job_id] = {"status": "error", "result": {"error": "Verwerking mislukt — controleer de logs."}}
                logging.error(f"Job {job_id} failed for {url}")
        except Exception as e:
            _jobs[job_id] = {"status": "error", "result": {"error": str(e)}}
            logging.error(f"Job {job_id} exception: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def job_status(job_id: str):
    """Returns current status of a submit job.
    Pre:  job_id from a prior /submit call
    Post: {"status": "pending"/"done"/"error", "result": {...}}
    """
    if job_id not in _jobs:
        return jsonify({"error": "Onbekend job ID"}), 404
    return jsonify(_jobs[job_id])


@app.route("/health")
def health():
    """Health check endpoint; also cleans up expired tokens."""
    cleanup_expired()
    return "OK", 200


def _html_response(title: str, body: str, error: bool = False) -> str:
    """Renders a minimal HTML response page."""
    color = "#dc3545" if error else "#28a745"
    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width">
  <title>{title} — TechNieuwsVandaag</title>
  <style>
    body {{font-family:sans-serif;max-width:480px;
           margin:60px auto;padding:0 16px;text-align:center}}
    h1   {{color:{color};font-size:24px}}
    p    {{color:#444;line-height:1.6}}
    a    {{color:#CC0000}}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{body}</p>
  <hr style="margin:32px 0;border-color:#eee">
  <small style="color:#aaa">TechNieuwsVandaag.nl</small>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Visitor stats via SSH — pre-fetched twice daily in background
# ---------------------------------------------------------------------------

_SSH_KEY      = "/home/dgebbink/.ssh/ssh-key-oracle-web.key"
_SSH_TARGET   = "ubuntu@141.144.195.65"
_STATS_SCRIPT = "/home/ubuntu/nginx_stats.py"
_REFRESH_INTERVAL = 12 * 3600  # seconds between scheduled refreshes

_visitor_cache: dict = {"data": None, "ts": 0.0}
_visitor_lock = threading.Lock()


def _do_fetch_visitor_stats():
    """Read pre-computed nginx_stats_cache.json from Oracle server via SSH."""
    try:
        result = subprocess.run(
            ["ssh", "-i", _SSH_KEY, "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=10", _SSH_TARGET,
             "cat /home/ubuntu/nginx_stats_cache.json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logging.error("nginx_stats_cache SSH error: %s", result.stderr[:200])
            return
        data = json.loads(result.stdout)
        with _visitor_lock:
            _visitor_cache["data"] = data
            _visitor_cache["ts"]   = time.monotonic()
        logging.info("Bezoekersstatistieken bijgewerkt vanuit cache")
    except Exception as exc:
        logging.error("nginx_stats fetch failed: %s", exc)


def _visitor_stats_scheduler():
    """Background thread: fetch at startup, then every 12 hours."""
    _do_fetch_visitor_stats()
    while True:
        time.sleep(_REFRESH_INTERVAL)
        _do_fetch_visitor_stats()


threading.Thread(target=_visitor_stats_scheduler, daemon=True, name="visitor-stats").start()


def _fetch_visitor_stats():
    """Return cached visitor stats; never blocks (returns None if not yet available)."""
    with _visitor_lock:
        return _visitor_cache["data"]


@app.route("/api/visitor-stats")
def api_visitor_stats():
    """JSON endpoint for visitor stats (pre-fetched, served from cache)."""
    data = _fetch_visitor_stats()
    if data is None:
        return jsonify({"error": "Stats niet beschikbaar"}), 503
    return jsonify(data)


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------

_BASE = Path(__file__).parent


def _analytics_posts() -> dict:
    """Parse posted_titles.txt and return stats dict."""
    entries = []
    path = _BASE / "posted_titles.txt"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) == 3:
                entries.append(parts)  # [date_str, url, title]

    today = date.today()
    day_counts = Counter(d for d, _, _ in entries)

    labels = [(today - timedelta(days=i)).isoformat() for i in range(89, -1, -1)]
    counts = [day_counts.get(d, 0) for d in labels]

    src: Counter = Counter()
    for _, url, _ in entries:
        domain = urlparse(url).netloc.removeprefix("www.")
        if domain:
            src[domain] += 1

    week_ago    = (today - timedelta(days=6)).isoformat()
    month_start = today.replace(day=1).isoformat()
    total       = len(entries)
    active_days = len(set(d for d, _, _ in entries)) or 1

    return {
        "total":       total,
        "this_week":   sum(1 for d, _, _ in entries if d >= week_ago),
        "this_month":  sum(1 for d, _, _ in entries if d >= month_start),
        "avg":         round(total / active_days, 1),
        "labels":      labels,
        "counts":      counts,
        "src_labels":  [s for s, _ in src.most_common(10)],
        "src_counts":  [c for _, c in src.most_common(10)],
        "recent":      list(reversed(entries))[:20],
    }


def _analytics_runs() -> list[tuple[str, int, int, str]]:
    """Parse run logs; return list of (date, ok, total, last_title) per day, newest first."""
    per_day: dict[str, dict] = {}
    for log_path in sorted((_BASE / "logs").glob("run_*.log")):
        parts = log_path.stem.split("_", 2)
        if len(parts) < 2:
            continue
        day = parts[1]
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        ok = "Gepubliceerd: 1" in content
        m  = re.search(r"Artikel verwerken \[(?:NL|EN)\]: (.+)", content)
        title = m.group(1)[:55] if (m and ok) else ""
        rec = per_day.setdefault(day, {"ok": 0, "total": 0, "title": ""})
        rec["total"] += 1
        if ok:
            rec["ok"] += 1
            rec["title"] = title
    return [
        (d, v["ok"], v["total"], v["title"])
        for d, v in sorted(per_day.items(), reverse=True)
    ][:14]


@app.route("/analytics")
def analytics():
    """Analytics dashboard — post history, top sources, run status."""
    data = _analytics_posts()
    runs = _analytics_runs()

    js = json.dumps({
        "labels":     data["labels"],
        "counts":     data["counts"],
        "src_labels": data["src_labels"],
        "src_counts": data["src_counts"],
    })

    def _card(value, label):
        return (
            f'<div class="bg-white rounded-xl p-5 border border-gray-100 shadow-sm">'
            f'<div class="text-3xl font-bold text-gray-800">{value}</div>'
            f'<div class="text-sm text-gray-400 mt-1">{label}</div>'
            f'</div>'
        )

    def _recent_row(d, url, title):
        domain = urlparse(url).netloc.removeprefix("www.")
        t = _html_mod.escape(title)
        u = _html_mod.escape(url, quote=True)
        return (
            f'<tr class="border-b border-gray-100 hover:bg-gray-50">'
            f'<td class="py-2 pr-4 text-sm text-gray-400 whitespace-nowrap">{d}</td>'
            f'<td class="py-2 pr-4 text-sm">'
            f'<a href="{u}" target="_blank" class="text-gray-800 hover:text-red-600">{t}</a>'
            f'</td>'
            f'<td class="py-2 text-sm text-gray-400 whitespace-nowrap">{_html_mod.escape(domain)}</td>'
            f'</tr>'
        )

    def _run_row(d, ok, total, title):
        ratio = f"{ok}/{total}"
        if ok == total:
            badge = f'<span class="text-xs px-2 py-0.5 rounded bg-green-100 text-green-700">{ratio}</span>'
        elif ok > 0:
            badge = f'<span class="text-xs px-2 py-0.5 rounded bg-yellow-100 text-yellow-700">{ratio}</span>'
        else:
            badge = f'<span class="text-xs px-2 py-0.5 rounded bg-red-100 text-red-700">{ratio}</span>'
        return (
            f'<tr class="border-b border-gray-100">'
            f'<td class="py-2 pr-3 text-sm text-gray-400 whitespace-nowrap">{d}</td>'
            f'<td class="py-2 pr-3">{badge}</td>'
            f'<td class="py-2 text-sm text-gray-500 truncate max-w-xs">{_html_mod.escape(title)}</td>'
            f'</tr>'
        )

    stats_html  = "".join([
        _card(data["total"],      "Totaal gepubliceerd"),
        _card(data["this_week"],  "Deze week"),
        _card(data["this_month"], "Deze maand"),
        _card(data["avg"],        "Gem. per actieve dag"),
    ])
    recent_html = "".join(_recent_row(d, u, t) for d, u, t in data["recent"])
    runs_html   = "".join(_run_row(d, ok, tot, title) for d, ok, tot, title in runs)
    if not runs_html:
        runs_html = '<tr><td colspan="3" class="py-4 text-sm text-gray-400">Geen run-logs gevonden</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Analyse — TechNieuwsVandaag</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <script>const D={js};</script>
</head>
<body class="bg-gray-50 min-h-screen">

  <header class="bg-red-700 text-white px-6 py-4 shadow">
    <div class="max-w-6xl mx-auto flex items-center gap-3">
      <a href="https://technieuwsvandaag.nl" target="_blank"
         class="text-lg font-bold tracking-tight hover:opacity-80">TechNieuwsVandaag</a>
      <span class="text-red-400">›</span>
      <span class="text-red-200 text-sm">Analyse</span>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-6 py-8 space-y-6">

    <!-- Publicatie stats -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      {stats_html}
    </div>

    <!-- Bezoekers (async via SSH) -->
    <div id="visitors-section" class="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-semibold text-gray-700">Bezoekers</h2>
        <div id="visitor-period" class="flex gap-1 hidden">
          <button id="vbtn-7"  onclick="setVisitorPeriod(7)"
            class="px-3 py-1 text-sm rounded-lg bg-red-700 text-white">7d</button>
          <button id="vbtn-30" onclick="setVisitorPeriod(30)"
            class="px-3 py-1 text-sm rounded-lg text-gray-500 hover:bg-gray-100">30d</button>
          <button id="vbtn-90" onclick="setVisitorPeriod(90)"
            class="px-3 py-1 text-sm rounded-lg text-gray-500 hover:bg-gray-100">90d</button>
        </div>
      </div>
      <div id="visitor-loading" class="text-sm text-gray-400 flex items-center gap-2 py-4">
        <svg class="animate-spin h-4 w-4 text-red-500" viewBox="0 0 24 24" fill="none">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
        </svg>
        Bezoekersdata laden via SSH…
      </div>
      <div id="visitor-error" class="hidden text-sm text-red-500 py-4"></div>
      <div id="visitor-cards" class="hidden grid grid-cols-2 md:grid-cols-3 gap-4 mb-6"></div>
      <canvas id="visitorsChart" class="hidden" height="70"></canvas>
    </div>

    <!-- Posts per dag -->
    <div class="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-semibold text-gray-700">Posts per dag</h2>
        <div class="flex gap-1">
          <button id="btn-7"  onclick="setPeriod(7)"
            class="px-3 py-1 text-sm rounded-lg bg-red-700 text-white">7d</button>
          <button id="btn-30" onclick="setPeriod(30)"
            class="px-3 py-1 text-sm rounded-lg text-gray-500 hover:bg-gray-100">30d</button>
          <button id="btn-90" onclick="setPeriod(90)"
            class="px-3 py-1 text-sm rounded-lg text-gray-500 hover:bg-gray-100">90d</button>
        </div>
      </div>
      <canvas id="postsChart" height="70"></canvas>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">

      <!-- Piekuren -->
      <div id="peak-section" class="bg-white rounded-xl shadow-sm p-6 border border-gray-100 hidden md:col-span-2">
        <h2 class="text-base font-semibold text-gray-700 mb-4">Piekuren (werkdagen vs weekend)</h2>
        <canvas id="peakChart" height="60"></canvas>
      </div>

      <!-- Apparaat / OS / Herkomst -->
      <div id="device-section" class="hidden bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 class="text-base font-semibold text-gray-700 mb-4">Apparaat</h2>
        <canvas id="deviceChart"></canvas>
      </div>

      <div id="os-section" class="hidden bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 class="text-base font-semibold text-gray-700 mb-4">Besturingssysteem</h2>
        <canvas id="osChart"></canvas>
      </div>

      <div id="country-section" class="hidden bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 class="text-base font-semibold text-gray-700 mb-4">Herkomst (top 10)</h2>
        <canvas id="countryChart"></canvas>
      </div>

      <!-- Top bronnen + top pagina's -->
      <div class="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 class="text-base font-semibold text-gray-700 mb-4">Top bronnen (all-time)</h2>
        <canvas id="sourcesChart"></canvas>
      </div>

      <div class="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 class="text-base font-semibold text-gray-700 mb-1">Top pagina's</h2>
        <p id="top-pages-period" class="text-xs text-gray-400 mb-4">laden…</p>
        <div id="top-pages-loading" class="text-sm text-gray-400">Laden…</div>
        <table id="top-pages-table" class="hidden w-full">
          <thead>
            <tr class="border-b border-gray-200">
              <th class="text-left text-xs text-gray-400 font-medium pb-2 pr-3">Pagina</th>
              <th class="text-right text-xs text-gray-400 font-medium pb-2">Pageviews</th>
            </tr>
          </thead>
          <tbody id="top-pages-body"></tbody>
        </table>
      </div>

    </div>

    <!-- Dagelijkse runs + recente posts -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">

      <div class="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 class="text-base font-semibold text-gray-700 mb-4">Dagelijkse runs (laatste 14 dagen)</h2>
        <div class="overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr class="border-b border-gray-200">
                <th class="text-left text-xs text-gray-400 font-medium pb-2 pr-3">Datum</th>
                <th class="text-left text-xs text-gray-400 font-medium pb-2 pr-3">OK / runs</th>
                <th class="text-left text-xs text-gray-400 font-medium pb-2">Laatste artikel</th>
              </tr>
            </thead>
            <tbody>{runs_html}</tbody>
          </table>
        </div>
      </div>

      <div class="bg-white rounded-xl shadow-sm p-6 border border-gray-100">
        <h2 class="text-base font-semibold text-gray-700 mb-4">Recente posts</h2>
        <div class="overflow-x-auto">
          <table class="w-full">
            <thead>
              <tr class="border-b border-gray-200">
                <th class="text-left text-xs text-gray-400 font-medium pb-2 pr-4 w-28">Datum</th>
                <th class="text-left text-xs text-gray-400 font-medium pb-2 pr-4">Titel</th>
                <th class="text-left text-xs text-gray-400 font-medium pb-2">Bron</th>
              </tr>
            </thead>
            <tbody>{recent_html}</tbody>
          </table>
        </div>
      </div>

    </div>

  </main>

  <script>
    // ── Posts per dag chart ──
    const postsChart = new Chart(document.getElementById('postsChart'), {{
      type: 'line',
      data: {{
        labels: D.labels.slice(-7),
        datasets: [{{
          data: D.counts.slice(-7),
          borderColor: '#dc2626',
          backgroundColor: 'rgba(220,38,38,0.07)',
          borderWidth: 2, fill: true, tension: 0.35,
          pointRadius: 2, pointHoverRadius: 5,
        }}]
      }},
      options: {{
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }}, maxTicksLimit: 14 }} }},
          y: {{ beginAtZero: true, ticks: {{ precision: 0, font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }} }},
        }}
      }}
    }});

    function setPeriod(days) {{
      postsChart.data.labels = D.labels.slice(-days);
      postsChart.data.datasets[0].data = D.counts.slice(-days);
      postsChart.update();
      [7, 30, 90].forEach(d => {{
        document.getElementById('btn-' + d).className = d === days
          ? 'px-3 py-1 text-sm rounded-lg bg-red-700 text-white'
          : 'px-3 py-1 text-sm rounded-lg text-gray-500 hover:bg-gray-100';
      }});
    }}

    // ── Top bronnen chart ──
    new Chart(document.getElementById('sourcesChart'), {{
      type: 'bar',
      data: {{
        labels: D.src_labels,
        datasets: [{{ data: D.src_counts, backgroundColor: 'rgba(220,38,38,0.75)', borderRadius: 4 }}]
      }},
      options: {{
        indexAxis: 'y',
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          x: {{ beginAtZero: true, ticks: {{ precision: 0, font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }} }},
          y: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }},
        }}
      }}
    }});

    // ── Bezoekers via async SSH ──
    let V = null;
    let visitorsChart = null;

    function _vcard(value, label) {{
      return `<div class="bg-gray-50 rounded-lg p-4 border border-gray-100">
        <div class="text-2xl font-bold text-gray-800">${{value.toLocaleString('nl-NL')}}</div>
        <div class="text-xs text-gray-400 mt-1">${{label}}</div>
      </div>`;
    }}

    function setVisitorPeriod(days) {{
      if (!V) return;
      const sl = V.labels.slice(-days);
      const sv = V.views_series.slice(-days);
      const su = V.unique_series.slice(-days);
      visitorsChart.data.labels = sl;
      visitorsChart.data.datasets[0].data = sv;
      visitorsChart.data.datasets[1].data = su;
      visitorsChart.update();
      [7, 30, 90].forEach(d => {{
        document.getElementById('vbtn-' + d).className = d === days
          ? 'px-3 py-1 text-sm rounded-lg bg-red-700 text-white'
          : 'px-3 py-1 text-sm rounded-lg text-gray-500 hover:bg-gray-100';
      }});
    }}

    function renderTopPages(pages, days) {{
      document.getElementById('top-pages-period').textContent = `afgelopen ${{days}} dagen`;
      const tbody = document.getElementById('top-pages-body');
      tbody.innerHTML = pages.map(p => `
        <tr class="border-b border-gray-100 hover:bg-gray-50">
          <td class="py-1.5 pr-3 text-sm text-gray-700 font-mono truncate max-w-xs">${{p.path || '/'}}</td>
          <td class="py-1.5 text-sm text-gray-500 text-right">${{p.views.toLocaleString('nl-NL')}}</td>
        </tr>`).join('');
      document.getElementById('top-pages-loading').classList.add('hidden');
      document.getElementById('top-pages-table').classList.remove('hidden');
    }}

    fetch('/api/visitor-stats')
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(data => {{
        V = data;
        document.getElementById('visitor-loading').classList.add('hidden');
        document.getElementById('visitor-period').classList.remove('hidden');

        // Stat cards
        const cards = document.getElementById('visitor-cards');
        cards.innerHTML =
          _vcard(data.views_today,  'Pageviews vandaag') +
          _vcard(data.unique_today, 'Uniek vandaag') +
          _vcard(data.views_7d,     'Pageviews 7 dagen') +
          _vcard(data.unique_7d,    'Uniek 7 dagen') +
          _vcard(data.views_30d,    'Pageviews 30 dagen') +
          _vcard(data.unique_30d,   'Uniek 30 dagen');
        cards.classList.remove('hidden');

        // Chart
        const canvas = document.getElementById('visitorsChart');
        canvas.classList.remove('hidden');
        visitorsChart = new Chart(canvas, {{
          type: 'line',
          data: {{
            labels: data.labels.slice(-7),
            datasets: [
              {{
                label: 'Pageviews',
                data: data.views_series.slice(-7),
                borderColor: '#dc2626',
                backgroundColor: 'rgba(220,38,38,0.06)',
                borderWidth: 2, fill: true, tension: 0.35,
                pointRadius: 2, pointHoverRadius: 5,
              }},
              {{
                label: 'Unieke bezoekers',
                data: data.unique_series.slice(-7),
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59,130,246,0.06)',
                borderWidth: 2, fill: true, tension: 0.35,
                pointRadius: 2, pointHoverRadius: 5,
              }},
            ]
          }},
          options: {{
            plugins: {{ legend: {{ labels: {{ font: {{ size: 11 }} }} }} }},
            scales: {{
              x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }}, maxTicksLimit: 14 }} }},
              y: {{ beginAtZero: true, ticks: {{ precision: 0, font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }} }},
            }}
          }}
        }});

        renderTopPages(data.top_pages, 90);

        // Pie chart helper
        function _pie(canvasId, sectionId, items) {{
          if (!items || !items.length) return;
          document.getElementById(sectionId).classList.remove('hidden');
          const COLORS = ['#dc2626','#3b82f6','#22c55e','#f59e0b','#8b5cf6','#ec4899','#14b8a6','#f97316'];
          new Chart(document.getElementById(canvasId), {{
            type: 'doughnut',
            data: {{
              labels: items.map(i => i.label),
              datasets: [{{ data: items.map(i => i.count),
                backgroundColor: items.map((_, idx) => COLORS[idx % COLORS.length]),
                borderWidth: 2, borderColor: '#fff' }}]
            }},
            options: {{
              plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }}, padding: 10 }} }} }},
            }}
          }});
        }}

        _pie('deviceChart',  'device-section',  data.devices);
        _pie('osChart',      'os-section',      data.os);

        // Country horizontal bar
        if (data.countries && data.countries.length) {{
          document.getElementById('country-section').classList.remove('hidden');
          new Chart(document.getElementById('countryChart'), {{
            type: 'bar',
            data: {{
              labels: data.countries.map(c => c.label),
              datasets: [{{ data: data.countries.map(c => c.count),
                backgroundColor: 'rgba(139,92,246,0.75)', borderRadius: 4 }}]
            }},
            options: {{
              indexAxis: 'y',
              plugins: {{ legend: {{ display: false }} }},
              scales: {{
                x: {{ beginAtZero: true, ticks: {{ precision: 0, font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }} }},
                y: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }},
              }}
            }}
          }});
        }}

        // Peak hours chart
        if (data.peak_weekday && data.peak_weekend) {{
          document.getElementById('peak-section').classList.remove('hidden');
          const hours = Array.from({{length: 24}}, (_, i) => i + 'u');
          new Chart(document.getElementById('peakChart'), {{
            type: 'bar',
            data: {{
              labels: hours,
              datasets: [
                {{
                  label: 'Werkdag',
                  data: data.peak_weekday,
                  backgroundColor: 'rgba(59,130,246,0.7)',
                  borderRadius: 3,
                }},
                {{
                  label: 'Weekend',
                  data: data.peak_weekend,
                  backgroundColor: 'rgba(34,197,94,0.7)',
                  borderRadius: 3,
                }},
              ]
            }},
            options: {{
              plugins: {{ legend: {{ labels: {{ font: {{ size: 11 }} }} }} }},
              scales: {{
                x: {{ stacked: true, grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }} }} }},
                y: {{ stacked: true, beginAtZero: true, ticks: {{ precision: 0, font: {{ size: 11 }} }},
                     grid: {{ color: '#f3f4f6' }} }},
              }}
            }}
          }});
        }}
      }})
      .catch(err => {{
        document.getElementById('visitor-loading').classList.add('hidden');
        document.getElementById('visitor-error').textContent = 'Bezoekersdata niet beschikbaar: ' + err;
        document.getElementById('visitor-error').classList.remove('hidden');
        document.getElementById('top-pages-loading').textContent = 'Niet beschikbaar';
        document.getElementById('top-pages-period').textContent = '';
      }});
  </script>
</body>
</html>"""


if __name__ == "__main__":
    host = os.getenv("APPROVAL_HOST", "0.0.0.0")
    port = int(os.getenv("APPROVAL_PORT", "5055"))
    logging.info(f"Approval server starting on {host}:{port}")
    print(f"Approval server running on http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
