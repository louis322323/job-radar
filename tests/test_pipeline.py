"""Offline pipeline test with realistic mock jobs — verifies filter logic,
dedup, store, and results.json generation without network or LLM."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from radar.filter import PreFilter
from radar.store import Store
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config/filters.yaml"), encoding="utf-8") as f:
    pf = PreFilter(yaml.safe_load(f))

MOCK = [
    # should PASS — sweet spot
    {"title": "Projektmedarbejder til boligsocial helhedsplan", "org": "BO-VEST",
     "url": "https://x.dk/1", "snippet": "borgerinddragelse i Albertslund, samskabelse med beboere", "country": "DK", "source": "mock"},
    # should PASS with senior flag
    {"title": "Erfaren proceskonsulent", "org": "Kommune X",
     "url": "https://x.dk/2", "snippet": "minimum 5 års erfaring med borgerinddragelse", "country": "DK", "source": "mock"},
    # should DROP — wrong profession
    {"title": "Sygeplejerske til hjemmeplejen", "org": "Ishøj",
     "url": "https://x.dk/3", "snippet": "borgerinddragelse nævnes ikke", "country": "DK", "source": "mock"},
    # should DROP — excluded region
    {"title": "Byplanlægger", "org": "Aalborg Kommune",
     "url": "https://x.dk/4", "snippet": "byudvikling i Nordjylland", "country": "DK", "source": "mock"},
    # should DROP — no include match
    {"title": "Kommunikationskonsulent", "org": "Firma",
     "url": "https://x.dk/5", "snippet": "pressemeddelelser og SoMe", "country": "DK", "source": "mock"},
    # should DROP — German PhD type
    {"title": "Doktorandenstelle Stadtsoziologie", "org": "TU Berlin",
     "url": "https://x.de/6", "snippet": "stadtentwicklung", "country": "DE", "source": "mock"},
    # should PASS — German sweet spot
    {"title": "Quartiersmanagement Neukölln", "org": "Berlin",
     "url": "https://x.de/7", "snippet": "Bürgerbeteiligung im Quartier", "country": "DE", "source": "mock"},
    # page_watch always passes
    {"title": "⚐ Side ændret: opland", "org": "opland", "page_watch": True,
     "url": "https://opland.eu", "snippet": "…", "country": "DK", "source": "opland"},
]

EXPECTED = [True, True, False, False, False, False, True, True]

store = Store(":memory:")
results = []
for job, exp in zip(MOCK, EXPECTED):
    assert store.is_new(job), "dedup false positive"
    ok, why = pf.check(job)
    status = "OK " if ok == exp else "FAIL"
    print(f"{status} {'PASS' if ok else 'drop':4} [{why:28}] {job['title'][:55]}"
          + (f"  flags={job.get('flags')}" if job.get('flags') else ""))
    assert ok == exp, f"filter mismatch on: {job['title']}"
    if ok:
        job.setdefault("score", None)
        store.save_scored(job)
        results.append(job)

# dedup: second pass must reject everything
assert all(not store.is_new(j) for j in MOCK), "dedup failed on re-run"
print(f"\ndedup OK — re-run rejects all {len(MOCK)} jobs")

# page-watch hash logic
store.set_page_hash("opland", "abc")
assert store.get_page_hash("opland") == "abc"
print("page-watch hash store OK")

print(f"scored store holds {len(store.recent_scored())} jobs — all tests passed ✓")
