"""Fetch jobs from configured sources. Four adapter types:
jobtech_api (structured JSON), rss, html_list (CSS selectors), page_watch (diff alert).
Every adapter returns a list of dicts: {title, url, org, snippet, source, country}.
Failures are logged, never fatal — one broken source must not kill the run.
"""
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
        jobs.append({**_base(src),
                     "title": hit.get("headline", ""),
                     "org": (hit.get("employer") or {}).get("name", ""),
                     "url": (hit.get("application_details") or {}).get("url")
                            or f"https://arbetsformedlingen.se/platsbanken/annonser/{hit.get('id')}",
                     "snippet": (hit.get("description") or {}).get("text", "")[:2500]})
    return jobs


def _rss(src):
    feed = feedparser.parse(src["url"], request_headers=HEADERS)
    jobs = []
    for e in feed.entries:
        jobs.append({**_base(src),
                     "title": e.get("title", ""),
                     "org": e.get("author", ""),
                     "url": e.get("link", ""),
                     "snippet": BeautifulSoup(e.get("summary", ""), "html.parser")
                                .get_text(" ", strip=True)[:2500]})
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
            jobs.append({**_base(src), "title": title, "org": src["name"],
                         "url": url,
                         "snippet": parent.get_text(" ", strip=True)[:2500]})
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
        jobs.append({**_base(src),
                     "title": t.get_text(" ", strip=True) if t else "",
                     "org": src["name"],
                     "url": url,
                     "snippet": item.get_text(" ", strip=True)[:2500]})
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
        jobs.append({**_base(src),
                     "title": hit.get("titel") or hit.get("beruf", ""),
                     "org": hit.get("arbeitgeber", ""),
                     "url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}",
                     "snippet": " ".join(str(v) for v in [
                         hit.get("beruf", ""),
                         (hit.get("arbeitsort") or {}).get("ort", "")])[:2500]})
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
