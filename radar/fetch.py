"""Fetch jobs from configured sources. Four adapter types:
jobtech_api (structured JSON), rss, html_list (CSS selectors), page_watch (diff alert).
Every adapter returns a list of dicts: {title, url, org, snippet, source, country}.
Failures are logged, never fatal — one broken source must not kill the run.
"""
import datetime
import hashlib
import logging
import re

import feedparser
import requests
from bs4 import BeautifulSoup

log = logging.getLogger("radar.fetch")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
           "Accept-Language": "da,en;q=0.8,de;q=0.6"}
TIMEOUT = 25


# ── DEADLINE EXTRACTION ───────────────────────────────────────────────────────
# Deterministic, language-aware. Runs on listing snippets (varbi prints the
# deadline inline) and on enriched job-page text. Returns ISO "YYYY-MM-DD" or None.
_MONTHS = {
    # Danish
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
    # German
    "märz": 3, "mai": 5, "oktober": 10, "dezember": 12,
    # English
    "january": 1, "february": 2, "march": 3, "may": 5, "june": 6, "july": 7,
    "october": 10, "december": 12,
}
# Words that signal a deadline is nearby (search a window after these).
_DEADLINE_CUES = [
    "ansøgningsfrist", "ansøgningsdato", "sidste frist", "sidste ansøgning",
    "frist", "senest den", "senest", "ansøg senest",
    "bewerbungsfrist", "bewerbungsschluss", "bewerben sie sich bis",
    "deadline", "closing date", "apply by", "application deadline",
]
_DATE_NUMERIC = re.compile(r'\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b')   # 25.06.2026 / 25/06/2026
_DATE_ISO = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')                     # 2026-06-25
_DATE_WORD = re.compile(
    r'\b(\d{1,2})\.?\s*('
    r'januar|februar|marts|märz|april|maj|mai|juni|juli|august|september'
    r'|oktober|november|december|dezember'
    r'|january|february|march|may|june|july|october'
    r')\s*(\d{4})\b')


def _iso(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def extract_deadline(text):
    """Find an application deadline in free text. Prefers a date that sits just
    after a deadline cue word; falls back to the first plausible date. ISO out."""
    if not text:
        return None
    low = text.lower()

    # 1. Look in a 60-char window right after each cue word — most reliable.
    for cue in _DEADLINE_CUES:
        start = 0
        while True:
            i = low.find(cue, start)
            if i == -1:
                break
            window = low[i: i + len(cue) + 60]
            d = _first_date(window)
            if d:
                return d
            start = i + len(cue)

    # 2. No cue matched — if the text is short (a listing snippet), accept the
    #    first date in it. Skip this for long pages to avoid grabbing junk dates.
    if len(text) <= 400:
        return _first_date(low)
    return None


def _first_date(s):
    m = _DATE_ISO.search(s)
    if m:
        return _iso(m.group(1), m.group(2), m.group(3))
    m = _DATE_WORD.search(s)
    if m:
        return _iso(m.group(3), _MONTHS.get(m.group(2), 0), m.group(1))
    m = _DATE_NUMERIC.search(s)
    if m:
        return _iso(m.group(3), m.group(2), m.group(1))   # day.month.year (DK/DE order)
    return None


def fetch_source(src, store):
    kind = src["type"]
    try:
        if kind == "jobtech_api":
            return _jobtech(src)
        if kind == "arbeitsagentur_api":
            return _arbeitsagentur(src)
        if kind == "rss":
            return _rss(src)
        if kind == "html_list":
            return _html_list(src)
        if kind == "dk_careers":
            return _dk_careers(src)
        if kind == "page_watch":
            return _page_watch(src, store)
        log.warning("unknown source type %s", kind)
    except Exception as e:  # noqa: BLE001 — keep the pipeline alive
        log.error("source %s failed: %s", src["name"], e)
    return []


def _base(src):
    return {"source": src["name"], "country": src.get("country", "")}


def _jobtech(src):
    r = requests.get(src["url"], params=src.get("params", {}),
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    jobs = []
    for hit in r.json().get("hits", []):
        snippet = (hit.get("description") or {}).get("text", "")[:2500]
        jobs.append({**_base(src),
                     "title": hit.get("headline", ""),
                     "org": (hit.get("employer") or {}).get("name", ""),
                     "url": (hit.get("application_details") or {}).get("url")
                            or f"https://arbetsformedlingen.se/platsbanken/annonser/{hit.get('id')}",
                     "snippet": snippet,
                     "deadline": hit.get("application_deadline") or extract_deadline(snippet)})
    return jobs


def _rss(src):
    feed = feedparser.parse(src["url"], request_headers=HEADERS)
    jobs = []
    for e in feed.entries:
        snippet = BeautifulSoup(e.get("summary", ""), "html.parser").get_text(" ", strip=True)[:2500]
        jobs.append({**_base(src),
                     "title": e.get("title", ""),
                     "org": e.get("author", ""),
                     "url": e.get("link", ""),
                     "snippet": snippet,
                     "deadline": extract_deadline(snippet)})
    return jobs


def _html_list(src):
    r = requests.get(src["url"], headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    sel = src.get("selectors", {})
    jobs, seen = [], set()

    # Mode A (robust): match job links by URL pattern, e.g. "/vis-job/".
    # Survives site redesigns as long as the URL scheme is stable.
    if "link_pattern" in sel:
        pat = re.compile(sel["link_pattern"])
        for a in soup.find_all("a", href=True):
            if not pat.search(a["href"]):
                continue
            url = requests.compat.urljoin(src["url"], a["href"])
            title = a.get_text(" ", strip=True)
            if url in seen or len(title) < 4:
                continue
            seen.add(url)
            parent = a.find_parent(["article", "li", "div", "tr"]) or a
            snippet = parent.get_text(" ", strip=True)[:2500]
            jobs.append({**_base(src), "title": title, "org": src.get("org", src["name"]),
                         "url": url, "snippet": snippet,
                         "deadline": extract_deadline(snippet)})
            if len(jobs) >= 60:
                break
        return jobs

    # Mode B: classic CSS selectors
    for item in soup.select(sel["item"])[:60]:
        a = item.select_one(sel.get("link", "a"))
        t = item.select_one(sel.get("title", "a"))
        if not a or not a.get("href"):
            continue
        url = requests.compat.urljoin(src["url"], a["href"])
        snippet = item.get_text(" ", strip=True)[:2500]
        jobs.append({**_base(src),
                     "title": t.get_text(" ", strip=True) if t else "",
                     "org": src.get("org", src["name"]),
                     "url": url,
                     "snippet": snippet,
                     "deadline": extract_deadline(snippet)})
    return jobs


# Nav / boilerplate link text that is never a job title on a DK career page.
_DK_NAV = [
    "ledige stillinger", "ledige job", "se alle", "alle stillinger",
    "jobagent", "tilmeld", "log ind", "login", "min profil", "opret",
    "dansk", "english", "cookie", "privatliv", "persondata", "tilgængelighed",
    "om kommunen", "om os", "kontakt", "find vej", "nyhedsbrev", "forside",
    "powered by", "luk", "annullér", "annuller", "gem", "forny", "glemt",
    "mød en kollega", "bliv elev", "som arbejdsplads", "praktik og elev",
    "facebook", "instagram", "linkedin", "vis flere", "læs mere", "se mere",
    "notificér", "notificer", "send til en ven", "print", "del",
]


def _dk_careers(src):
    """Generic heuristic for server-rendered Danish career pages (Emply,
    Recruitio, Signatur, simple kommune CMS). No site-specific selectors:
    we keep <a> elements whose text reads like a real, multi-word job title
    and drop nav/boilerplate. The free keyword pre-filter downstream removes
    anything that slips through, so over-capture here is cheap and safe.

    NOTE: requests-only — JavaScript-rendered SPA career pages (some
    architecture firms) return nothing here. Run
        python main.py --test-source <name>
    once when the page has a live vacancy to confirm it yields links."""
    r = requests.get(src["url"], headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    base_path = requests.compat.urlsplit(src["url"]).path.rstrip("/")
    jobs, seen = [], set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        title = a.get_text(" ", strip=True)
        low = title.lower()
        if len(title) < 10 or len(title.split()) < 2:
            continue
        if any(nav in low for nav in _DK_NAV):
            continue
        url = requests.compat.urljoin(src["url"], href)
        # A vacancy detail page lives *below* the listing page, never at it.
        path = requests.compat.urlsplit(url).path.rstrip("/")
        if path == base_path or url in seen:
            continue
        seen.add(url)
        parent = a.find_parent(["article", "li", "div", "tr"]) or a
        snippet = parent.get_text(" ", strip=True)[:2500]
        jobs.append({**_base(src), "title": title,
                     "org": src.get("org", src["name"]), "url": url,
                     "snippet": snippet, "deadline": extract_deadline(snippet)})
        if len(jobs) >= 60:
            break
    if not jobs:
        log.info("dk_careers %s: 0 links (tom side, eller JS-renderet — tjek med --test-source)", src["name"])
    return jobs


def _arbeitsagentur(src):
    """Bundesagentur für Arbeit job API — official, documented public key
    (see jobsuche.api.bund.dev). The German equivalent of Platsbanken."""
    r = requests.get("https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs",
                     params=src.get("params", {}),
                     headers={**HEADERS, "X-API-Key": "jobboerse-jobsuche"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    jobs = []
    for hit in r.json().get("stellenangebote", []):
        ref = hit.get("refnr", "")
        snippet = " ".join(str(v) for v in [
            hit.get("beruf", ""),
            (hit.get("arbeitsort") or {}).get("ort", "")])[:2500]
        jobs.append({**_base(src),
                     "title": hit.get("titel") or hit.get("beruf", ""),
                     "org": hit.get("arbeitgeber", ""),
                     "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}",
                     "snippet": snippet,
                     "deadline": extract_deadline(snippet)})
    return jobs


def _page_watch(src, store):
    """Hash visible page text. If it changed since last run, emit one
    synthetic 'check this page' item. Robust against any site redesign."""
    r = requests.get(src["url"], headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
    digest = hashlib.sha256(text.encode()).hexdigest()
    prev = store.get_page_hash(src["name"])
    store.set_page_hash(src["name"], digest)
    if prev is None or prev == digest:
        return []
    return [{**_base(src),
             "title": f"⚐ Side ændret: {src['name']}",
             "org": src["name"],
             "url": src["url"],
             "snippet": text[:2500],
             "page_watch": True}]
