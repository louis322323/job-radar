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
TIMEOUT = 60

INSTRUCTION = """Du scorer ét jobopslag for Louis efter reglerne ovenfor.
Returnér KUN gyldig JSON, intet andet:
{"score": <int 0-100>, "verdict": "<én sætning på dansk: hvorfor denne score>",
 "deadline": "<ansøgningsfrist hvis nævnt, ellers null>"}
Vær kritisk. Ingen oppustede scorer. Hvis opslaget rammer et hårdt filter
(sprog, region, senioritet), giv score under 40."""


def _rules():
    with open(os.path.join(os.path.dirname(__file__), "..", "config",
                           "scoring-prompt.md"), encoding="utf-8") as f:
        return f.read()


def _job_text(job):
    return (f"TITEL: {job.get('title')}\nORGANISATION: {job.get('org')}\n"
            f"KILDE: {job.get('source')} ({job.get('country')})\n"
            f"FLAGS: {', '.join(job.get('flags', [])) or 'ingen'}\n"
            f"TEKST: {job.get('snippet')}\nURL: {job.get('url')}")


def score_job(job):
    provider = os.environ.get("LLM_PROVIDER", "deepseek").lower()
    fn = {"deepseek": _deepseek, "gemini": _gemini, "anthropic": _anthropic}.get(provider)
    if fn is None:
        log.warning("unknown provider %s", provider)
        return _unscored(job)
    try:
        raw = fn(_rules() + "\n\n" + INSTRUCTION, _job_text(job))
        data = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
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

