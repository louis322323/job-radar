#!/usr/bin/env python3
"""Job radar pipeline: fetch -> dedup -> keyword filter -> LLM score -> digest.

Usage:
  python main.py                    # full run
  python main.py --dry-run          # no LLM calls, no email
  python main.py --test-source NAME # fetch one source, print results, no state
"""
import argparse
import logging
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from radar.fetch import fetch_source           # noqa: E402
from radar.filter import PreFilter              # noqa: E402
from radar.store import Store                   # noqa: E402
from radar.score import score_job               # noqa: E402
from radar.digest import write_results, email_digest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("radar")
ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = "v5 (2026-06-13)"


def load_yaml(name):
    with open(os.path.join(ROOT, "config", name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-source")
    args = ap.parse_args()
    print(f"job-radar {VERSION}")

    sources = load_yaml("sources.yaml")["sources"]
    prefilter = PreFilter(load_yaml("filters.yaml"))
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    store = Store(os.path.join(ROOT, "data",
                  "test.sqlite" if args.test_source else "radar.sqlite"))

    if args.test_source:
        src = next((s for s in sources if s["name"] == args.test_source), None)
        if not src:
            sys.exit(f"unknown source: {args.test_source}")
        jobs = fetch_source(src, store)
        print(f"--- {src['name']}: {len(jobs)} jobs hentet ---")
        if not jobs:
            print("(0 jobs — kilden er muligvis blokeret, tom eller har ændret format)")
        for j in jobs:
            ok, why = prefilter.check(dict(j))
            print(f"{'PASS' if ok else 'drop':4} [{why}] {j['title'][:70]}  {j['url']}")
        return

    fetched = dedup_new = passed = 0
    new_jobs = []
    for src in sources:
        jobs = fetch_source(src, store)
        fetched += len(jobs)
        for job in jobs:
            if not store.is_new(job):
                continue
            dedup_new += 1
            ok, why = prefilter.check(job)
            if not ok:
                log.debug("dropped (%s): %s", why, job["title"])
                continue
            passed += 1
            if not args.dry_run:
                job = score_job(job)
            store.save_scored(job)
            new_jobs.append(job)

    write_results(store, os.path.join(ROOT, "dashboard", "results.json"))
    if not args.dry_run:
        email_digest(new_jobs)
    log.info("run done: %d fetched, %d new, %d passed filter, %d scored/queued",
             fetched, dedup_new, passed, len(new_jobs))


if __name__ == "__main__":
    main()
