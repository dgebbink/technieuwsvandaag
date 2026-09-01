#!/usr/bin/env python3
"""
nginx_stats.py — Parse nginx access logs and output visitor stats as JSON.

Runs on the Oracle web server; called via SSH from the local analytics page.
Reads /var/log/nginx/access.log (and rotated copies) using sudo.

**Een IP telt pas als bezoeker wanneer het zich als browser heeft bewezen:
minstens één stylesheet én minstens één afbeelding opgehaald.** Tot 2026-09-01
was een UA-blacklist de enige toets, en die overdreef het bezoek ~50×: op
31 aug telde dit script 324 "bezoekers" terwijl AdSense er 1–6 zag. Van die
324 haalden er 267 alleen de HTML op — nul css, js of beeld, dus geen browser.
Meer UA-patronen toevoegen is dweilen (crawlers verzinnen sneller een nieuwe
naam dan jij hem toevoegt); de omkering werkt wel, want een scraper die de
pagina niet rendert vraagt de bijbehorende bestanden nooit op.

Drie filters, in deze volgorde:
  1. UA-blacklist — inclusief de crawlers zonder het woord "bot" in hun naam
     (`GoogleOther`, `Google-InspectionTool`, `Lightpanda`, `"pc"`), en een
     lege UA, die geen enkele echte browser stuurt.
  2. Browserbewijs (css + afbeelding), zie hierboven.
  3. Datacenter-ranges — AWS, GCP, Tencent, Hetzner, OVH, IONOS. Zonder deze
     stap blijft ~2/3 van wat filter 2 doorlaat een cloud-IP met een
     nagemaakte iPhone-UA.

Het bewijs uit filter 2 geldt **per IP over het hele venster**, niet per dag.
Dat is bewust: een terugkerende bezoeker heeft css en beelden in zijn
browsercache staan en vraagt ze niet opnieuw op, dus een dagelijkse eis zou
juist de trouwste lezers wegfilteren.

Het resultaat is een ondergrens, geen waarheid: een bezoeker achter een VPN op
een datacenter-IP valt af. De ongefilterde telling blijft daarom zichtbaar in
`unique_series_raw` / `views_series_raw`, zodat het verschil navolgbaar is.

Output: single JSON object to stdout.
"""
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta, datetime
from typing import List, Optional
from urllib.parse import urlparse

BOT_PATTERNS = re.compile(
    r"bot|crawler|spider|slurp|bingpreview|applebot|facebookexternalhit|"
    r"meta-external|Googlebot|Baiduspider|YandexBot|SemrushBot|AhrefsBot|"
    r"DotBot|PetalBot|BingBot|mj12bot|DataForSeoBot|GPTBot|ClaudeBot|"
    r"PerplexityBot|ia_archiver|archive\.org|"
    r"Uptime-Kuma|UptimeRobot|StatusCake|Pingdom|uptimerobot|"
    r"Jetpack|WordPress\.com|python-requests|curl|wget|libwww|"
    r"Go-http-client|okhttp|axios|node-fetch|java/|"
    # Crawlers zónder het woord "bot" in hun naam — die glipten er tot
    # 2026-09-01 allemaal doorheen. GoogleOther is gewoon Google (66.249.x)
    # onder een andere vlag en was goed voor 17 "bezoekers" op één dag.
    r"GoogleOther|Google-InspectionTool|Google-Extended|AdsBot|Mediapartners|"
    r"FeedFetcher|Lightpanda|HeadlessChrome|PhantomJS|Puppeteer|Playwright|"
    r"ForestEngine|FlowIQ|visionheight|TheSocialWire|FlipboardProxy|"
    r"Palo Alto|Cortex|Expanse|masscan|zgrab|Nuclei|"
    r"Bytespider|Amazonbot|CCBot|Diffbot|Sogou|Exabot|Barkrowler|"
    r"Scrapy|Guzzle|Symfony|Apache-HttpClient|WinHTTP|HTTPClient|"
    r"Zabbix|check_http|Better Uptime|HetrixTools",
    re.IGNORECASE,
)

# Een lege UA stuurt geen enkele echte browser; nginx logt hem als "-" of "".
_EMPTY_UA = re.compile(r"^\s*-?\s*$")

# UA's die exact één woord zijn en geen browser benoemen. Een crawler die zich
# als `pc` voordoet komt niet door BOT_PATTERNS (geen "bot" in de naam) maar
# reed op 31 aug wel met verzonnen Baidu-referers rond op de site.
_JUNK_UA = re.compile(r"^(pc|python|test|unknown|none|null|mozilla)$", re.IGNORECASE)

# Hosting-/cloudranges. Bewust een korte, handmatige lijst van wat in deze
# logs daadwerkelijk voorkwam — een volledige ASN-database is hier niet
# beschikbaar (dbip-country.mmdb kent alleen landen). Het is dus een zeef,
# geen hek: nieuwe ranges duiken op en mogen hier bij. Prefix-match op de
# eerste twee octetten waar dat volstaat, anders drie.
_DATACENTER_PREFIXES = (
    # AWS
    "3.", "13.", "18.", "34.", "35.", "52.", "54.", "99.79.", "15.164.",
    # Google Cloud / Google infrastructuur (niet de crawler zelf: die valt al
    # op UA weg, maar 162.216.148.x rijdt rond met een lége UA)
    "104.196.", "130.211.", "162.216.148.", "146.148.",
    # Tencent Cloud + China Telecom/Unicom-hosting
    "43.", "101.32.", "101.42.", "101.43.", "119.28.", "129.226.", "1.92.",
    "116.204.", "211.159.", "62.234.", "82.157.", "152.32.", "124.243.",
    "140.206.", "154.8.", "180.153.", "170.106.", "49.51.",
    # Hetzner
    "5.9.", "65.21.", "88.99.", "95.216.", "116.202.", "138.201.", "159.69.",
    "167.235.", "168.119.", "49.12.", "49.13.", "78.46.", "78.47.",
    # OVH
    "51.68.", "51.75.", "51.83.", "51.89.", "51.91.", "137.74.", "145.239.",
    "147.135.", "158.69.", "167.114.", "192.99.", "198.244.",
    # IONOS / 1&1
    "82.165.", "85.215.", "87.106.", "212.227.", "217.160.", "74.208.",
    # DigitalOcean
    "104.131.", "138.68.", "142.93.", "143.110.", "159.65.", "159.89.",
    "165.22.", "167.71.", "174.138.", "178.62.", "188.166.", "206.189.",
    # Linode / Akamai
    "45.33.", "45.56.", "45.79.", "50.116.", "172.104.", "172.105.",
    "139.162.", "172.234.", "170.187.",
    # Alibaba
    "8.208.", "8.209.", "47.74.", "47.88.", "47.254.",
)


def _is_datacenter(ip: str) -> bool:
    """True als het IP in een bekende hosting-range valt (zie _DATACENTER_PREFIXES)."""
    return ip.startswith(_DATACENTER_PREFIXES)


def _is_bot_ua(ua: str) -> bool:
    """True als de user-agent bot, leeg of onzin is."""
    return bool(
        _EMPTY_UA.match(ua) or _JUNK_UA.match(ua.strip()) or BOT_PATTERNS.search(ua)
    )

LOG_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>[^ "]+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<referer>[^"]*)" "(?P<ua>[^"]*)"'
)

_OWN_DOMAINS = re.compile(r'technieuwsvandaag|gebbink', re.IGNORECASE)

_REFERER_LABELS = {
    "google":     "Google",
    "bing":       "Bing",
    "duckduckgo": "DuckDuckGo",
    "yahoo":      "Yahoo",
    "yandex":     "Yandex",
    "facebook":   "Facebook",
    "instagram":  "Instagram",
    "t.co":       "Twitter/X",
    "twitter":    "Twitter/X",
    "linkedin":   "LinkedIn",
    "reddit":     "Reddit",
    "youtube":    "YouTube",
    "github":     "GitHub",
    "nieuws.social": "nieuws.social",
    "mastodon":   "Mastodon",
}

PAGE_RE = re.compile(r"^/([a-z0-9][a-z0-9\-/]*)?$")

_SKIP_PATHS = re.compile(
    r"\.php|//|wp-content|wp-includes|wp-json|wp-admin|wp-login|"
    r"xmlrpc|favicon|robots|sitemap|\.well-known|feed|comments|"
    r"\.(css|js|jpg|jpeg|png|gif|svg|ico|woff|ttf|map|txt|xml|gz|zip)$",
    re.IGNORECASE,
)

GEOIP_DB = "/home/ubuntu/dbip-country.mmdb"


def _referer_label(referer: str) -> str:
    if not referer or referer == "-":
        return "Direct"
    try:
        host = urlparse(referer).hostname or ""
    except Exception:
        return "Direct"
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    if _OWN_DOMAINS.search(host):
        return None  # skip internal referers
    for key, label in _REFERER_LABELS.items():
        if key in host:
            return label
    return host or "Direct"


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

# Bestandssoorten die alleen een renderende browser opvraagt.
_CSS_RE = re.compile(r"\.css(\?|$)", re.IGNORECASE)
_IMG_RE = re.compile(r"\.(jpg|jpeg|png|webp|gif|svg)(\?|$)", re.IGNORECASE)


def collect(days: int = 90) -> dict:
    """Leest de nginx-logs en levert bezoekstatistieken als dict.

    Twee fasen, en die volgorde is noodzakelijk: het browserbewijs van een IP
    kan in een ánder logbestand staan dan de pageview die erdoor geldig wordt
    (een bezoeker die vandaag terugkomt bewees zich vorige week). Pas als álle
    regels gezien zijn, is te bepalen welke IP's een browser waren.

    Pre:  days >= 1; de logbestanden zijn leesbaar via sudo
    Post: dict met de series over `days` dagen. `unique_series`/`views_series`
          zijn gefilterd; `*_raw` bevat dezelfde telling zonder filter 2 en 3,
          zodat zichtbaar blijft hoeveel er is weggestreept.
    """
    today = date.today()
    cutoff = today - timedelta(days=days - 1)

    log_files = ["/var/log/nginx/access.log", "/var/log/nginx/access.log.1"]
    for i in range(2, 20):
        log_files.append(f"/var/log/nginx/access.log.{i}.gz")

    # ------------------------------------------------------------------
    # Fase 1 — alle regels lezen: browserbewijs verzamelen en de kandidaat-
    # pageviews apart leggen. Nog niets filteren op IP.
    # ------------------------------------------------------------------
    loaded_css: set = set()
    loaded_img: set = set()
    candidates: list = []  # (day_str, ip, path, ua, referer, hour, weekday)

    for log_path in log_files:
        for line in _read_log(log_path):
            m = LOG_RE.match(line)
            if not m:
                continue

            ip  = m.group("ip")
            raw = m.group("path")

            # Browserbewijs telt ongeacht status of datum: een 304 op de
            # stylesheet bewijst net zo goed dat er een browser aan de andere
            # kant zit, en het venster is toch al afgebakend door de bestanden.
            if _CSS_RE.search(raw):
                loaded_css.add(ip)
            elif _IMG_RE.search(raw):
                loaded_img.add(ip)

            # Filter 1: user-agent
            ua = m.group("ua")
            if _is_bot_ua(ua):
                continue
            if int(m.group("status")) != 200:
                continue

            path = raw.split("?")[0].rstrip("/") or "/"
            if _SKIP_PATHS.search(path) or not PAGE_RE.match(path):
                continue

            dt = _parse_dt(m.group("time"))
            if not dt or dt.date() < cutoff:
                continue

            candidates.append(
                (dt.date().isoformat(), ip, path, ua, m.group("referer"),
                 dt.hour, dt.weekday())
            )

    # ------------------------------------------------------------------
    # Fase 2 — pas nu is bekend welke IP's zich als browser gedroegen.
    # ------------------------------------------------------------------
    browsers = loaded_css & loaded_img

    def _is_visitor(ip: str) -> bool:
        return ip in browsers and not _is_datacenter(ip)

    geo_reader = _make_geo_reader()

    daily_views:  defaultdict = defaultdict(int)
    daily_unique: defaultdict = defaultdict(set)
    raw_views:    defaultdict = defaultdict(int)
    raw_unique:   defaultdict = defaultdict(set)
    page_counter:     Counter = Counter()
    peak_weekday_ips: defaultdict = defaultdict(lambda: defaultdict(set))
    peak_weekend_ips: defaultdict = defaultdict(lambda: defaultdict(set))
    device_counter:   Counter = Counter()
    os_counter:       Counter = Counter()
    country_counter:  Counter = Counter()
    referer_counter:  Counter = Counter()
    seen_ips: dict = {}

    for day_str, ip, path, ua, referer, hour, weekday in candidates:
        # De ongefilterde telling loopt altijd mee, zodat het verschil met de
        # oude cijfers navolgbaar blijft in plaats van stilletjes te verdwijnen.
        raw_views[day_str] += 1
        raw_unique[day_str].add(ip)

        if not _is_visitor(ip):
            continue

        daily_views[day_str] += 1
        daily_unique[day_str].add(ip)
        page_counter[path or "/"] += 1

        if weekday < 5:
            peak_weekday_ips[hour][day_str].add(ip)
        else:
            peak_weekend_ips[hour][day_str].add(ip)

        device_counter[_device(ua)] += 1
        os_counter[_os(ua)] += 1

        if ip not in seen_ips:
            seen_ips[ip] = _country(ip, geo_reader)
        country_counter[seen_ips[ip]] += 1

        ref_label = _referer_label(referer)
        if ref_label:
            referer_counter[ref_label] += 1

    if geo_reader:
        geo_reader.close()

    labels        = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    views_series  = [daily_views.get(d, 0)           for d in labels]
    unique_series = [len(daily_unique.get(d, set())) for d in labels]
    views_raw     = [raw_views.get(d, 0)             for d in labels]
    unique_raw    = [len(raw_unique.get(d, set()))   for d in labels]

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
        # Ongefilterd: alleen filter 1 (user-agent), zoals dit script vóór
        # 2026-09-01 telde. Staat erbij als referentie, niet als bezoekcijfer.
        "views_series_raw":  views_raw,
        "unique_series_raw": unique_raw,
        "views_today":     views_today,
        "unique_today":    unique_today,
        "views_7d":        _sum(views_series,  week_ago),
        "unique_7d":       _sum(unique_series, week_ago),
        "views_30d":       _sum(views_series,  month_ago),
        "unique_30d":      _sum(unique_series, month_ago),
        "unique_30d_raw":  _sum(unique_raw,    month_ago),
        "top_pages":       [{"path": p, "views": c} for p, c in page_counter.most_common(10)],
        "peak_weekday":    [sum(len(s) for s in peak_weekday_ips[h].values()) for h in range(24)],
        "peak_weekend":    [sum(len(s) for s in peak_weekend_ips[h].values()) for h in range(24)],
        "devices":         [{"label": k, "count": v} for k, v in device_counter.most_common()],
        "os":              [{"label": k, "count": v} for k, v in os_counter.most_common()],
        "countries":       [{"label": k, "count": v} for k, v in country_counter.most_common(10)],
        "referrers":       [{"label": k, "count": v} for k, v in referer_counter.most_common(15)],
    }


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    print(json.dumps(collect(days)))
