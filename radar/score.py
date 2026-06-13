"""LLM scoring against scoring-prompt.md. Pluggable provider via env:

  LLM_PROVIDER = deepseek | gemini | anthropic   (default: deepseek)
  DEEPSEEK_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY

Cost reality at ~10-20 scored jobs/day: DeepSeek a few øre/day,
Gemini Flash free tier covers it entirely, Claude Haiku a few kr/month.
If no key is set the pipeline still runs — jobs pass through unscored
with score=None so the digest/dashboard show them as 'unscored'.
"""
import json
import logging
import os

import requests

log = logging.getLogger("radar.score")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
TIMEOUT = 60

INSTRUCTION = """Du scorer ét jobopslag for Louis efter reglerne ovenfor.
Returnér KUN gyldig JSON, intet andet:
{"score": <int 0-100>, "verdict": "<én sætning på dansk, BRUG IKKE anførselstegn inni>",
 "deadline": "<ansøgningsfrist hvis nævnt, ellers null>"}
Vær kritisk men FAIR. Ingen oppustede scorer.
- Opfind ALDRIG en udelukkelsesgrund. Hvis noget ikke står i teksten, antag det ikke.
- En KORT jobtekst er IKKE i sig selv en grund til lav score. Bedøm rolletypen ud fra titel + det du har. En tysk Quartiersmanagement/Stadtteilarbeit-rolle eller en migrations-forskerstilling er et stærkt fit for Louis selv med sparsom tekst.
- Kun ægte hårde filtre (forkert arbejdssprog, ekskluderet region, krav om afsluttet ph.d./5+ års erfaring) giver score under 40.
- Hvis du er i tvivl pga. lidt tekst, så score rollen på dens type (typisk 70-80 for borgerinddragelse/migration/Quartier), ikke 0."""


def _rules():
    with open(os.path.join(os.path.dirname(__file__), "..", "config",
                           "scoring-prompt.md"), encoding="utf-8") as f:
        return f.read()


def _job_text(job):
    return (f"TITEL: {job.get('title')}\nORGANISATION: {job.get('org')}\n"
            f"KILDE: {job.get('source')} ({job.get('country')})\n"
            f"FLAGS: {', '.join(job.get('flags', [])) or 'ingen'}\n"
            f"TEKST: {job.get('snippet')}\nURL: {job.get('url')}")


def _enrich(job):
    """If the listing snippet is thin, fetch the job page and pull more text
    so the model scores on real content instead of just a title. Best-effort:
    any failure leaves the original snippet untouched."""
    snippet = job.get("snippet") or ""
    if len(snippet) >= 400 or job.get("page_watch") or not job.get("url"):
        return job
    try:
        import requests
        from bs4 import BeautifulSoup
        r = requests.get(job["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
        if len(text) > len(snippet):
            job["snippet"] = text[:3000]
    except Exception as e:  # noqa: BLE001
        log.debug("enrich failed for %s: %s", job.get("url"), e)
    return job


def _parse(raw):
    """Robustly extract score/verdict/deadline from a model reply that is
    *meant* to be JSON but may contain stray quotes or trailing text."""
    import re
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    # First try: strict JSON
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001
        pass
    # Fallback: pull the fields out by regex, tolerant of broken quoting
    out = {}
    m = re.search(r'"?score"?\s*:\s*(\d{1,3})', cleaned)
    if m:
        out["score"] = int(m.group(1))
    m = re.search(r'"?verdict"?\s*:\s*"(.*?)"(?:\s*[,}]|$)', cleaned, re.S)
    if m:
        out["verdict"] = m.group(1).strip()
    m = re.search(r'"?deadline"?\s*:\s*"?([^",}]+)', cleaned)
    if m and "null" not in m.group(1).lower():
        out["deadline"] = m.group(1).strip()
    if "score" not in out:
        raise ValueError(f"could not parse score from: {cleaned[:120]}")
    return out


def score_job(job):
    provider = os.environ.get("LLM_PROVIDER", "deepseek").lower()
    fn = {"deepseek": _deepseek, "gemini": _gemini, "anthropic": _anthropic}.get(provider)
    if fn is None:
        log.warning("unknown provider %s", provider)
        return _unscored(job)
    job = _enrich(job)
    try:
        raw = fn(_rules() + "\n\n" + INSTRUCTION, _job_text(job))
        data = _parse(raw)
        job.update(score=int(data["score"]), verdict=data.get("verdict", ""),
                   deadline=data.get("deadline"))
    except Exception as e:  # noqa: BLE001
        log.error("scoring failed for %s: %s", job.get("title"), e)
        return _unscored(job)
    return job


def _unscored(job):
    job.setdefault("score", None)
    job.setdefault("verdict", "Ikke scoret (ingen API-nøgle eller fejl) — vurder manuelt.")
    return job


def _deepseek(system, user):
    key = os.environ["DEEPSEEK_API_KEY"]
    r = requests.post("https://api.deepseek.com/chat/completions",
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": "deepseek-chat",
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}],
                            "temperature": 0.1, "max_tokens": 300},
                      timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(system, user):
    key = os.environ["GEMINI_API_KEY"]
    r = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={key}",
        json={"contents": [{"parts": [{"text": system + "\n\n---\n\n" + user}]}],
              "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300}},
        timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _anthropic(system, user):
    key = os.environ["ANTHROPIC_API_KEY"]
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                      json={"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
                            "system": system,
                            "messages": [{"role": "user", "content": user}]},
                      timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["content"][0]["text"]
